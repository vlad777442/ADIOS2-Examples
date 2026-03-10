#!/usr/bin/env python3
"""
Measure Ceph OSD recovery time with realistic failure simulation.

DEFAULT MODE (grace period):
  Stops the OSD daemon on its host so the mon detects the failure naturally.
  The mon waits mon_osd_down_out_interval (default 600 s) before marking the
  OSD out, which then triggers PG remapping and recovery.

  Timeline:
    t_stop_wall  – wall-clock when daemon stop command is issued
    t_down       – "osd.X marked itself down and dead" (mon detects failure)
    t_marked_out – "Marking osd.X out (has been down for N seconds)" (grace period ends)
    t_degraded   – first pgmap line with degraded PGs (OSD is down, copies missing)
    t_recovering – first pgmap line with recovering/backfilling PGs (data moving)
    t_healthy    – pgmap: all PGs back to active+clean

  Restore: starts the daemon back and runs `ceph osd in`.

--force-out MODE:
  Uses `ceph osd out` immediately, bypassing the grace period.
  Useful for fast, repeatable benchmarks.

Requirements:
  - Local `ceph` CLI + keyring (client node, node11).
  - SSH access as root to node0 (the mon, running cephadm).
  - node0 has root SSH access to OSD host nodes (cephadm key distribution).
  - Mon cluster log is read via: journalctl -u 'ceph*mon*' --follow on node0.

Usage:
  python3 measure_ceph_recovery.py <osd_id> [options]
  python3 measure_ceph_recovery.py 3 --verbose
  python3 measure_ceph_recovery.py 3 --force-out        # skip grace period
  python3 measure_ceph_recovery.py 3 --no-restore       # leave OSD out after
"""

import argparse
import subprocess
import re
import json
import sys
import time
from datetime import datetime, timezone, timedelta

__all__ = [
    # OSD host discovery
    "find_osd_info",
    # OSD daemon control
    "stop_osd_daemon", "start_osd_daemon", "osd_out", "osd_in",
    # Journal streaming
    "start_journal_stream", "stop_journal_stream", "watch_journal",
    # Shell helpers
    "run_local", "run_on_mon", "run_on_osd_host",
    # Utilities
    "vlog", "parse_journal_ts", "pgmap_state", "all_clean",
    # Constants
    "MON_HOST",
]

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# journalctl --output short-iso lines start with an ISO-8601 timestamp:
#   2026-03-08T14:36:47+0000 node0 ceph-mon[6612]: ...
JOURNAL_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})")

# Auto-markout:  "Marking osd.3 out (has been down for 602 seconds)"
# Manual osd out: "osd.3 marked out"
MARKED_OUT_RE = re.compile(r"[Mm]arking osd\.(\d+) out|osd\.(\d+) marked out")

# OSD reports itself down:  "osd.3 marked itself down and dead"
OSD_DOWN_RE = re.compile(r"osd\.(\d+) marked itself down")

# pgmap line emitted by mon every ~1 s:
#   pgmap v210047: 97 pgs: 97 active+clean; 107 GiB data, ...
#   pgmap v210047: 97 pgs: 47 active+degraded, 50 active+clean; ...
#   pgmap v210047: 97 pgs: 47 active+recovering, 50 active+clean; ...
PGMAP_RE = re.compile(r"pgmap v\d+: (\d+) pgs: ([^;]+)")
DEGRADED_RE  = re.compile(r"degraded",         re.IGNORECASE)
RECOVERING_RE = re.compile(r"recover|backfill", re.IGNORECASE)

MON_HOST = "root@node0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def vlog(msg, verbose, file=sys.stderr):
    if verbose:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", file=file)


def run_local(cmd_str, verbose=False):
    """Run a command on the local host (client)."""
    vlog(f"local: {cmd_str}", verbose)
    r = subprocess.run(
        cmd_str, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if r.returncode != 0:
        vlog(f"  stdout: {r.stdout.strip()}", verbose)
        vlog(f"  stderr: {r.stderr.strip()}", verbose)
    return r


def run_on_mon(cmd_str, verbose=False):
    """Run a command on node0 (the mon / cephadm host)."""
    vlog(f"node0: {cmd_str}", verbose)
    r = subprocess.run(
        ["ssh", MON_HOST, cmd_str],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if r.returncode != 0:
        vlog(f"  stdout: {r.stdout.strip()}", verbose)
        vlog(f"  stderr: {r.stderr.strip()}", verbose)
    return r


def run_on_osd_host(osd_host, cmd_str, verbose=False):
    """
    Run a command on an OSD host.
    Client → node0 → osd_host (client has no direct root SSH to OSD nodes).
    """
    vlog(f"{osd_host}: {cmd_str}", verbose)
    nested = f"ssh -o StrictHostKeyChecking=no root@{osd_host} {cmd_str}"
    r = subprocess.run(
        ["ssh", MON_HOST, nested],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if r.returncode != 0:
        vlog(f"  stdout: {r.stdout.strip()}", verbose)
        vlog(f"  stderr: {r.stderr.strip()}", verbose)
    return r


def parse_journal_ts(line):
    """
    Extract a UTC datetime from a journalctl --output short-iso line.
    Returns (datetime_utc, raw_ts_str) or (None, None).
    """
    m = JOURNAL_TS_RE.match(line.strip())
    if not m:
        return None, None
    ts_str = m.group(1)
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z")
        return dt.astimezone(timezone.utc), ts_str
    except ValueError:
        return None, None


def pgmap_state(line):
    """
    If this is a pgmap line, return (total_pgs, state_summary).
    Otherwise None.
    """
    m = PGMAP_RE.search(line)
    if not m:
        return None
    return int(m.group(1)), m.group(2).strip()


def all_clean(total_pgs, summary):
    """True when pgmap shows all PGs as active+clean and nothing else."""
    parts = [p.strip() for p in summary.split(",")]
    if len(parts) != 1:
        return False
    m = re.match(r"(\d+)\s+active\+clean$", parts[0])
    return bool(m) and int(m.group(1)) == total_pgs


# ---------------------------------------------------------------------------
# OSD host discovery
# ---------------------------------------------------------------------------

def find_osd_info(osd_id, verbose):
    """
    Return (hostname, service_name) for the given OSD.
    hostname    – e.g. 'node4'
    service_name – e.g. 'ceph-<fsid>@osd.3.service'
    """
    r = run_local(f"ceph osd find {osd_id} --format json", verbose)
    if r.returncode != 0:
        print(f"ERROR: ceph osd find {osd_id} failed", file=sys.stderr)
        sys.exit(1)
    host = json.loads(r.stdout)["host"]

    r2 = run_local("ceph fsid", verbose)
    if r2.returncode != 0:
        print("ERROR: ceph fsid failed", file=sys.stderr)
        sys.exit(1)
    fsid = r2.stdout.strip()

    service_name = f"ceph-{fsid}@osd.{osd_id}.service"
    vlog(f"osd.{osd_id} → host={host}  service={service_name}", verbose)
    return host, service_name


# ---------------------------------------------------------------------------
# OSD daemon control
# ---------------------------------------------------------------------------

def stop_osd_daemon(osd_id, osd_host, service_name, verbose):
    """Stop the OSD systemd service on its host (via node0 SSH hop)."""
    r = run_on_osd_host(osd_host, f"systemctl stop {service_name}", verbose)
    if r.returncode != 0:
        print(f"ERROR: failed to stop {service_name} on {osd_host}", file=sys.stderr)
        sys.exit(1)
    vlog(f"stopped {service_name} on {osd_host}", verbose)


def start_osd_daemon(osd_host, service_name, verbose):
    """Start the OSD systemd service on its host (via node0 SSH hop)."""
    r = run_on_osd_host(osd_host, f"systemctl start {service_name}", verbose)
    if r.returncode != 0:
        print(f"WARNING: failed to start {service_name} on {osd_host}", file=sys.stderr)
    else:
        vlog(f"started {service_name} on {osd_host}", verbose)


def osd_out(osd_id, verbose):
    """Immediately mark OSD out via local ceph CLI (--force-out mode)."""
    r = run_local(f"ceph osd out osd.{osd_id}", verbose)
    if r.returncode != 0:
        print(f"ERROR: ceph osd out osd.{osd_id} failed:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    vlog(f"osd.{osd_id} marked out (force)", verbose)


def osd_in(osd_id, verbose):
    """Mark OSD back in via local ceph CLI."""
    r = run_local(f"ceph osd in osd.{osd_id}", verbose)
    if r.returncode != 0:
        print(f"WARNING: ceph osd in osd.{osd_id} failed:\n{r.stderr}", file=sys.stderr)
    else:
        vlog(f"osd.{osd_id} marked in", verbose)


# ---------------------------------------------------------------------------
# Journal streaming
# ---------------------------------------------------------------------------

def start_journal_stream(tail_lines, verbose):
    """
    SSH to node0 and follow the ceph-mon journal in ISO format.
    Returns Popen proc; read from proc.stdout.
    """
    remote_cmd = (
        f"journalctl -u 'ceph*mon*' --follow --output short-iso -n {tail_lines}"
    )
    vlog(f"journal stream: ssh {MON_HOST} '{remote_cmd}'", verbose)
    return subprocess.Popen(
        ["ssh", MON_HOST, remote_cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


def stop_journal_stream(proc):
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Recovery detection
# ---------------------------------------------------------------------------

def watch_journal(proc, osd_id, timeout, grace_period_mode, verbose):
    """
    Read journal lines and return timing milestones as a dict.
    Each value is (datetime_utc | None, raw_ts_str | None).

    Keys: marked_out, degraded, recovering, healthy
    """
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)

    results   = {k: (None, None) for k in ("down", "marked_out", "degraded", "recovering", "healthy")}
    found     = {k: False        for k in results}

    if grace_period_mode:
        vlog(f"waiting for mon to auto-mark osd.{osd_id} out "
             f"(mon_osd_down_out_interval ≈ 600 s) ...", verbose)

    for raw in iter(proc.stdout.readline, ""):
        if datetime.now(timezone.utc) > deadline:
            print(f"Timeout ({timeout}s) reached.", file=sys.stderr)
            break

        line = raw.rstrip()
        if not line:
            continue

        vlog(f"  {line}", verbose)

        dt, ts = parse_journal_ts(line)

        # Detect OSD going down ("osd.3 marked itself down and dead")
        if not found["down"] and OSD_DOWN_RE.search(line) and f"osd.{osd_id}" in line:
            results["down"] = (dt, ts)
            found["down"] = True
            vlog(f"  → osd.{osd_id} down: {ts}", verbose)

        # Detect marked_out (auto: "Marking osd.3 out ..." or manual: "osd.3 marked out")
        if not found["marked_out"] and MARKED_OUT_RE.search(line) and f"osd.{osd_id}" in line:
            results["marked_out"] = (dt, ts)
            found["marked_out"] = True
            vlog(f"  → osd.{osd_id} marked out: {ts}", verbose)

        # Detect pgmap state transitions
        pg = pgmap_state(line)
        if pg is not None:
            total_pgs, summary = pg

            if not found["degraded"] and DEGRADED_RE.search(summary):
                results["degraded"] = (dt, ts)
                found["degraded"] = True
                vlog(f"  → degraded ({summary}): {ts}", verbose)

            if not found["recovering"] and RECOVERING_RE.search(summary):
                results["recovering"] = (dt, ts)
                found["recovering"] = True
                vlog(f"  → recovering ({summary}): {ts}", verbose)

            if (found["degraded"] or found["recovering"]) and not found["healthy"]:
                if all_clean(total_pgs, summary):
                    results["healthy"] = (dt, ts)
                    found["healthy"] = True
                    vlog(f"  → all pgs active+clean: {ts}", verbose)
                    break

    return results


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Measure Ceph OSD recovery time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("osd_id", type=int, help="OSD ID to take out (e.g. 3)")
    p.add_argument(
        "--force-out", action="store_true",
        help="Skip grace period: use `ceph osd out` immediately instead of "
             "stopping the daemon (default: stop daemon, wait for auto-markout)",
    )
    p.add_argument(
        "--timeout", type=int, default=7200,
        help="Abort after this many seconds (default: 7200; allows for 600 s "
             "grace period + up to ~1.5 h recovery)",
    )
    p.add_argument(
        "--no-restore", action="store_true",
        help="Do NOT restart daemon / mark OSD back in after measurement",
    )
    p.add_argument(
        "--tail-lines", type=int, default=30,
        help="Lines of journal backlog to read before following (default: 30)",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    osd_id   = args.osd_id
    verbose  = args.verbose
    force_out = args.force_out

    osd_host, service_name = find_osd_info(osd_id, verbose)

    # Start journal stream BEFORE taking the OSD out so we don't miss events
    journal_proc = start_journal_stream(args.tail_lines, verbose)
    time.sleep(1.5)  # let SSH + journalctl connect and start streaming

    t_stop_wall = datetime.now(timezone.utc)

    if force_out:
        osd_out(osd_id, verbose)
    else:
        stop_osd_daemon(osd_id, osd_host, service_name, verbose)
        vlog(
            f"OSD daemon stopped. Mon will mark osd.{osd_id} out after grace period "
            f"(mon_osd_down_out_interval). Waiting ...",
            verbose,
        )

    try:
        events = watch_journal(
            journal_proc, osd_id, args.timeout,
            grace_period_mode=not force_out,
            verbose=verbose,
        )
    finally:
        if not args.no_restore:
            if not force_out:
                start_osd_daemon(osd_host, service_name, verbose)
            # Mark OSD in regardless of mode (auto-markout doesn't auto-mark-in)
            osd_in(osd_id, verbose)
        stop_journal_stream(journal_proc)

    # -----------------------------------------------------------------------
    # Compute durations
    # -----------------------------------------------------------------------
    def secs(dt0, dt1):
        if dt0 is not None and dt1 is not None:
            return round((dt1 - dt0).total_seconds(), 1)
        return None

    down_dt,        down_ts        = events["down"]
    marked_out_dt,  marked_out_ts  = events["marked_out"]
    degraded_dt,    degraded_ts    = events["degraded"]
    recovering_dt,  recovering_ts  = events["recovering"]
    healthy_dt,     healthy_ts     = events["healthy"]

    t_start_dt = degraded_dt or recovering_dt
    t_start_ts = degraded_ts or recovering_ts

    # Grace period: from OSD going down to mon marking it out.
    # Fall back to t_stop_wall if t_down wasn't captured.
    grace_ref_dt = down_dt or (t_stop_wall if not force_out else None)

    result = {
        "osd_id":    osd_id,
        "mode":      "force-out" if force_out else "grace-period",
        # --- timestamps (raw journal strings, local timezone of node0) ---
        "t_stop_wall":   t_stop_wall.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "t_down":        down_ts,
        "t_marked_out":  marked_out_ts,
        "t_degraded":    degraded_ts,
        "t_recovering":  recovering_ts,
        "t_healthy":     healthy_ts,
        # --- durations (seconds) ---
        # grace period: OSD down → mon marks it out (should be ≈600 s)
        "duration_grace_period_s":      secs(grace_ref_dt, marked_out_dt) if not force_out else None,
        # time from marked-out to data movement starting
        "duration_out_to_recovering_s": secs(marked_out_dt, recovering_dt),
        # actual data movement window (recovering → healthy)
        "duration_recovery_s":          secs(recovering_dt, healthy_dt),
        # full window from first degradation to healthy
        "duration_total_s":             secs(t_start_dt, healthy_dt),
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
