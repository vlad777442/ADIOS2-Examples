#!/usr/bin/env python3
"""
Gray-Scott OSD Recovery Performance Experiment

Orchestrates the full experiment:
  1. Pre-flight checks (ceph healthy, RBD mounted, BP5 input exists)
  2. Starts performance monitor (CPU/mem/IO every 3 s → perf.csv)
  3. Starts analysis job: mpirun adios2-pdf-calc (non-blocking)
  4. After --fault-delay seconds: stops the OSD daemon to simulate failure
     (the mon waits ~600 s / mon_osd_down_out_interval before marking it out)
  5. Journal watcher thread detects t_down, t_marked_out, t_recovering, t_healthy
  6. Waits for analysis to complete, then for recovery detection
  7. Restores OSD (start daemon + ceph osd in)
  8. Writes results JSON and generates annotated 4-panel plot

Requirements:
  - Local `ceph` CLI + keyring (node11, the client)
  - SSH root access to node0 (mon, cephadm)
  - node0 has root SSH to OSD host nodes (cephadm key distribution)
  - adios2-pdf-calc binary built at source/cpp/gray-scott/build/
  - 200 GB simulation dataset at /mnt/rbd/gray-scott/gs-rbd.bp
    (generate with: mpirun -n 4 ./build/adios2-gray-scott settings-rbd-200gb.json)

Usage:
  python3 run_recovery_experiment.py <osd_id> [options]

Examples:
  python3 run_recovery_experiment.py 3
  python3 run_recovery_experiment.py 3 --mpi-procs 2 --fault-delay 60 --verbose
  python3 run_recovery_experiment.py 3 --no-restore   # leave OSD out after
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# Allow importing from the same scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_ceph_recovery import (
    find_osd_info,
    start_journal_stream, stop_journal_stream, watch_journal,
    stop_osd_daemon, start_osd_daemon, osd_in,
    run_local, vlog,
)

# ---------------------------------------------------------------------------
# Paths  (edit if your layout differs)
# ---------------------------------------------------------------------------
ADIOS2_LIB    = "/users/vlad777/research/ADIOS2-Examples/ADIOS2/build/lib"
GS_DIR        = "/users/vlad777/research/ADIOS2-Examples/source/cpp/gray-scott"
PDF_CALC      = os.path.join(GS_DIR, "build", "adios2-pdf-calc")
RBD_MOUNT     = "/mnt/rbd"
DEFAULT_INPUT  = "/mnt/rbd/gray-scott/gs-rbd.bp"
DEFAULT_OUTPUT = "/mnt/rbd/gray-scott/analysis/pdf-rbd.bp"
RESULTS_BASE   = "/users/vlad777/research/ADIOS2-Examples/results"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Gray-Scott OSD recovery performance experiment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("osd_id", type=int, help="OSD ID to fail (e.g. 3)")
    p.add_argument("--mpi-procs", type=int, default=2, metavar="N",
                   help="MPI ranks for analysis (default: 2)")
    p.add_argument("--bins", type=int, default=100,
                   help="PDF histogram bins (default: 100)")
    p.add_argument("--fault-delay", type=int, default=280, metavar="SECS",
                   help="Seconds after analysis start before injecting fault (default: 60)")
    p.add_argument("--input", default=DEFAULT_INPUT, metavar="PATH",
                   help=f"BP5 simulation input (default: {DEFAULT_INPUT})")
    p.add_argument("--output", default=DEFAULT_OUTPUT, metavar="PATH",
                   help=f"BP5 analysis output (default: {DEFAULT_OUTPUT})")
    p.add_argument("--output-dir", default=RESULTS_BASE, metavar="DIR",
                   help="Parent directory for results folder (default: results/)")
    p.add_argument("--timeout", type=int, default=24400,
                   help="Max seconds to wait for full recovery (default: 2s4400)")
    p.add_argument("--monitor-interval", type=float, default=3.0, metavar="SECS",
                   help="Performance sampling interval in seconds (default: 3)")
    p.add_argument("--no-restore", action="store_true",
                   help="Leave OSD stopped/out after experiment")
    p.add_argument("--rbd-mount", default=RBD_MOUNT, metavar="DIR",
                   help=f"Mount point of the RBD device to monitor (default: {RBD_MOUNT})")
    p.add_argument("--no-fault", action="store_true",
                   help="Run analysis only, no OSD fault injection (baseline/control run)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
def preflight_check(args):
    errors = []

    r = subprocess.run(["ceph", "health"], capture_output=True, text=True)
    if r.returncode != 0:
        errors.append("ceph health check failed — is the cluster reachable?")
    elif "HEALTH_ERR" in r.stdout:
        errors.append(f"Cluster not healthy: {r.stdout.strip()}")

    if not os.path.ismount(args.rbd_mount):
        errors.append(f"{args.rbd_mount} is not mounted")

    if not os.path.exists(args.input):
        errors.append(
            f"BP5 input not found: {args.input}\n"
            f"  Generate the dataset first (from {GS_DIR}):\n"
            f"    mpirun -n 4 ./build/adios2-gray-scott settings-rbd-200gb.json"
        )

    if not os.path.isfile(PDF_CALC):
        errors.append(f"Analysis binary not found: {PDF_CALC}")

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print("Pre-flight OK")


# ---------------------------------------------------------------------------
# RBD device detection
# ---------------------------------------------------------------------------
def find_rbd_device(mount=RBD_MOUNT):
    """Return the bare device name (e.g. 'rbd0') mounted at mount."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mount:
                    dev = parts[0]
                    if dev.startswith("/dev/"):
                        return dev[5:]
    except OSError:
        pass
    return "rbd0"


# ---------------------------------------------------------------------------
# Performance monitor (thread target)
# ---------------------------------------------------------------------------
def _read_diskstats(device):
    """Return (sectors_read, sectors_written) for device from /proc/diskstats."""
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 10 and parts[2] == device:
                    # Field indices: 5=sectors_read, 9=sectors_written
                    return int(parts[5]), int(parts[9])
    except OSError:
        pass
    return 0, 0


def _rbd_usage_mb(mount=RBD_MOUNT):
    r = subprocess.run(["df", "-m", mount], capture_output=True, text=True)
    if r.returncode == 0:
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            try:
                return float(lines[1].split()[2])
            except (IndexError, ValueError):
                pass
    return 0.0


def _cpu_and_mem():
    """Return (cpu_percent, memory_mb). Uses psutil if available."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().used / (1024 * 1024)
        return cpu, mem
    except ImportError:
        pass
    # Fallback: /proc/meminfo for memory, 0 for CPU
    total_kb = avail_kb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
    except OSError:
        pass
    mem_mb = (total_kb - avail_kb) / 1024.0
    return 0.0, mem_mb


def perf_monitor(csv_path, rbd_device, stop_event, interval=3.0, rbd_mount=RBD_MOUNT):
    """
    Thread target: samples CPU/mem/IO every `interval` seconds.
    Writes a CSV compatible with plot_io.py:
      timestamp, cpu_percent, memory_mb, rbd_usage_mb, io_read_mb, io_write_mb
    io_read_mb and io_write_mb are instantaneous rates (MB/s) computed from
    /proc/diskstats deltas.
    """
    SECTOR_BYTES = 512
    prev_r = prev_w = prev_ts = None

    # Prime psutil CPU counter (first call always returns 0)
    try:
        import psutil
        psutil.cpu_percent(interval=None)
    except ImportError:
        pass

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "cpu_percent", "memory_mb",
                         "rbd_usage_mb", "io_read_mb", "io_write_mb"])

        while not stop_event.is_set():
            ts = time.time()
            cpu, mem_mb = _cpu_and_mem()
            rbd_mb = _rbd_usage_mb(rbd_mount)
            cur_r, cur_w = _read_diskstats(rbd_device)

            if prev_r is not None and prev_ts is not None:
                dt = ts - prev_ts
                if dt > 0:
                    read_mb_s  = (cur_r - prev_r) * SECTOR_BYTES / (1024**2) / dt
                    write_mb_s = (cur_w - prev_w) * SECTOR_BYTES / (1024**2) / dt
                else:
                    read_mb_s = write_mb_s = 0.0
            else:
                read_mb_s = write_mb_s = 0.0

            prev_r, prev_w, prev_ts = cur_r, cur_w, ts

            writer.writerow([
                f"{ts:.6f}", f"{cpu:.1f}", f"{mem_mb:.0f}",
                f"{rbd_mb:.0f}", f"{read_mb_s:.3f}", f"{write_mb_s:.3f}",
            ])
            f.flush()

            stop_event.wait(interval)


# ---------------------------------------------------------------------------
# Analysis job
# ---------------------------------------------------------------------------
def start_analysis(args):
    """Start adios2-pdf-calc as a non-blocking subprocess."""
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    env = os.environ.copy()
    ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{ADIOS2_LIB}:{ld}" if ld else ADIOS2_LIB

    # If launched via sudo, OpenMPI blocks root by default unless explicitly allowed.
    run_as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if run_as_root:
        env["OMPI_ALLOW_RUN_AS_ROOT"] = "1"
        env["OMPI_ALLOW_RUN_AS_ROOT_CONFIRM"] = "1"

    cmd = [
        "mpirun",
        *( ["--allow-run-as-root"] if run_as_root else [] ),
        "-n", str(args.mpi_procs), "--oversubscribe",
        PDF_CALC,
        args.input,
        args.output,
        str(args.bins),
    ]
    vlog(f"analysis cmd: {' '.join(cmd)}", args.verbose)

    return subprocess.Popen(
        cmd,
        cwd=GS_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def drain_analysis_output(proc, log_path):
    """Thread target: relay analysis stdout/stderr to a log file."""
    with open(log_path, "w") as f:
        for line in proc.stdout:
            f.write(line)
            f.flush()


# ---------------------------------------------------------------------------
# Results building
# ---------------------------------------------------------------------------
def _secs(dt0, dt1):
    if dt0 is not None and dt1 is not None:
        return round((dt1 - dt0).total_seconds(), 1)
    return None


def build_results(args, t_analysis_start, t_analysis_end,
                  t_fault, recovery_events, perf_csv, out_dir):
    down_dt,       down_ts       = recovery_events.get("down",       (None, None))
    marked_out_dt, marked_out_ts = recovery_events.get("marked_out", (None, None))
    degraded_dt,   degraded_ts   = recovery_events.get("degraded",   (None, None))
    recovering_dt, recovering_ts = recovery_events.get("recovering", (None, None))
    healthy_dt,    healthy_ts    = recovery_events.get("healthy",    (None, None))

    # Grace period reference: use t_down if detected, else t_fault wall-clock
    grace_ref = down_dt or t_fault

    return {
        "osd_id":    args.osd_id,
        "mpi_procs": args.mpi_procs,
        "bins":      args.bins,
        "input":     args.input,
        "output":    args.output,
        # --- wall-clock timestamps (UTC) ---
        "t_analysis_start": t_analysis_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "t_fault_injected": t_fault.strftime("%Y-%m-%dT%H:%M:%SZ") if t_fault else None,
        "t_analysis_end":   t_analysis_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "no_fault": args.no_fault,
        # --- recovery event timestamps (raw journal strings, node0 local tz) ---
        "t_down":       down_ts,
        "t_marked_out": marked_out_ts,
        "t_degraded":   degraded_ts,
        "t_recovering": recovering_ts,
        "t_healthy":    healthy_ts,
        # --- durations (seconds) ---
        "duration_analysis_s":          _secs(t_analysis_start, t_analysis_end),
        "duration_fault_delay_s":       args.fault_delay,
        "duration_grace_period_s":      _secs(grace_ref, marked_out_dt),
        "duration_out_to_recovering_s": _secs(marked_out_dt, recovering_dt),
        "duration_recovery_s":          _secs(recovering_dt, healthy_dt),
        "duration_total_downtime_s":    _secs(degraded_dt or t_fault, healthy_dt),
        # --- file paths ---
        "perf_csv":  perf_csv,
        "output_dir": out_dir,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    # Create timestamped output directory
    ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(args.output_dir, f"recovery-experiment-{ts_tag}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Output directory: {out_dir}")

    preflight_check(args)

    # OSD host and service name (needed for fault/restore; skip lookup in no-fault mode)
    if not args.no_fault:
        osd_host, service_name = find_osd_info(args.osd_id, args.verbose)
    else:
        osd_host = service_name = None
        print("No-fault mode: OSD fault injection disabled (baseline/control run)")

    # ── Start journal stream early (before fault so we don't miss any events) ──
    if not args.no_fault:
        journal_proc = start_journal_stream(30, args.verbose)
        time.sleep(1.5)  # let SSH + journalctl establish
    else:
        journal_proc = None

    # ── Start performance monitor ──────────────────────────────────────────────
    perf_csv = os.path.join(out_dir, "perf.csv")
    rbd_device = find_rbd_device(args.rbd_mount)
    vlog(f"RBD device: /dev/{rbd_device}", args.verbose)

    stop_monitor = threading.Event()
    monitor_thread = threading.Thread(
        target=perf_monitor,
        args=(perf_csv, rbd_device, stop_monitor, args.monitor_interval,
              args.rbd_mount),
        daemon=True, name="perf-monitor",
    )
    monitor_thread.start()

    # ── Start analysis ─────────────────────────────────────────────────────────
    t_analysis_start = datetime.now(timezone.utc)
    analysis_proc = start_analysis(args)

    analysis_log = os.path.join(out_dir, "analysis.log")
    log_thread = threading.Thread(
        target=drain_analysis_output,
        args=(analysis_proc, analysis_log),
        daemon=True, name="analysis-log",
    )
    log_thread.start()
    print(f"Analysis started (PID {analysis_proc.pid}), {args.mpi_procs} MPI ranks, "
          f"{args.bins} bins")

    # ── Start recovery journal watcher ─────────────────────────────────────────
    recovery_events = {}
    watcher_done = threading.Event()

    if not args.no_fault:
        def run_watcher():
            raw = watch_journal(
                journal_proc, args.osd_id, args.timeout,
                grace_period_mode=True,
                verbose=args.verbose,
            )
            recovery_events.update(raw)
            watcher_done.set()

        watcher_thread = threading.Thread(
            target=run_watcher, daemon=True, name="journal-watcher"
        )
        watcher_thread.start()
    else:
        watcher_done.set()  # nothing to wait for

    # ── Fault injection (main thread, after fault_delay) ──────────────────────
    if not args.no_fault:
        print(f"Waiting {args.fault_delay} s before fault injection...")
        time.sleep(args.fault_delay)
        t_fault = datetime.now(timezone.utc)
        print(f"Injecting fault at {t_fault.strftime('%H:%M:%SZ')} UTC: "
              f"stopping osd.{args.osd_id} on {osd_host}...")
        try:
            stop_osd_daemon(args.osd_id, osd_host, service_name, args.verbose)
            print(f"OSD daemon stopped. Mon will mark it out after "
                  f"~{args.timeout//10} s grace period. Recovery watcher running...")
        except SystemExit:
            print("WARNING: stop_osd_daemon failed — fault not injected.", file=sys.stderr)
    else:
        t_fault = None

    # ── Wait for analysis to complete ─────────────────────────────────────────
    print("Waiting for analysis to complete...")
    analysis_proc.wait()
    t_analysis_end = datetime.now(timezone.utc)
    analysis_duration = (t_analysis_end - t_analysis_start).total_seconds()
    print(f"Analysis completed in {analysis_duration:.1f} s  "
          f"(exit code {analysis_proc.returncode})")

    # ── Stop performance monitor ───────────────────────────────────────────────
    stop_monitor.set()
    monitor_thread.join(timeout=10)

    # ── Wait for recovery detection ────────────────────────────────────────────
    if not args.no_fault:
        remaining = max(60, args.timeout - int(analysis_duration))
        print(f"Waiting up to {remaining} s for recovery detection...")
        watcher_done.wait(timeout=remaining)
        if not watcher_done.is_set():
            print("WARNING: recovery detection timed out before healthy.", file=sys.stderr)

    # ── Restore OSD ───────────────────────────────────────────────────────────
    if not args.no_fault and not args.no_restore:
        print(f"Restoring osd.{args.osd_id}...")
        try:
            start_osd_daemon(osd_host, service_name, args.verbose)
        except SystemExit:
            print("WARNING: failed to start OSD daemon", file=sys.stderr)
        osd_in(args.osd_id, args.verbose)

    if journal_proc is not None:
        stop_journal_stream(journal_proc)

    # ── Write results JSON ─────────────────────────────────────────────────────
    results = build_results(
        args, t_analysis_start, t_analysis_end,
        t_fault, recovery_events, perf_csv, out_dir,
    )
    results_path = os.path.join(out_dir, "results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Results JSON: {results_path}")

    # Copy throughput CSV if the analysis binary generated one
    bp5_base = args.output.rstrip("/")
    for suffix in ["_throughput.csv", ".throughput.csv"]:
        candidate = bp5_base + suffix
        if os.path.isfile(candidate):
            dest = os.path.join(out_dir, os.path.basename(candidate))
            shutil.copy2(candidate, dest)
            results["throughput_csv"] = dest
            print(f"Throughput CSV: {dest}")
            break

    # ── Generate plot ──────────────────────────────────────────────────────────
    plot_path = os.path.join(out_dir, "recovery_plot.png")
    plot_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "plot_recovery_io.py"
    )
    if os.path.isfile(plot_script):
        r = subprocess.run(
            [sys.executable, plot_script, perf_csv, results_path, plot_path],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"Plot: {plot_path}")
        else:
            print(f"Plot generation failed:\n{r.stderr}", file=sys.stderr)
    else:
        print(f"Plot script not found: {plot_script}", file=sys.stderr)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Experiment complete. Results in: {out_dir}")
    print(f"{'='*60}")
    summary_keys = [
        "duration_analysis_s", "duration_fault_delay_s",
        "duration_grace_period_s", "duration_out_to_recovering_s",
        "duration_recovery_s", "duration_total_downtime_s",
        "t_fault_injected", "t_marked_out", "t_recovering", "t_healthy",
    ]
    print(json.dumps(
        {k: results.get(k) for k in summary_keys if results.get(k) is not None},
        indent=2,
    ))


if __name__ == "__main__":
    main()
