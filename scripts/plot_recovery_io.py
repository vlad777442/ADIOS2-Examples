#!/usr/bin/env python3
"""
Plot Gray-Scott analysis I/O performance with OSD recovery annotations.

Produces a 4-panel dark-theme PNG showing read throughput, write throughput,
CPU/memory, and RBD disk usage over time, with vertical markers and shaded
regions for each recovery phase (grace period, data recovery).

Usage:
  python3 plot_recovery_io.py <perf_csv> <results_json> <output_png>

Inputs:
  perf_csv      – CSV from run_recovery_experiment.py perf monitor
                  columns: timestamp, cpu_percent, memory_mb,
                           rbd_usage_mb, io_read_mb, io_write_mb
  results_json  – JSON from run_recovery_experiment.py
                  must contain: t_analysis_start, t_fault_injected,
                  t_down, t_marked_out, t_recovering, t_healthy
  output_png    – destination path for the plot
"""

import csv
import json
import sys
import os
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Patch
except ImportError:
    os.system("sudo apt-get install -y python3-matplotlib --quiet")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.patches import Patch


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_perf(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "ts":       float(row["timestamp"]),
                    "cpu":      float(row["cpu_percent"]) if row["cpu_percent"] else 0.0,
                    "mem_mb":   float(row["memory_mb"]),
                    "rbd_mb":   float(row["rbd_usage_mb"]),
                    "read_mb":  float(row["io_read_mb"]),
                    "write_mb": float(row["io_write_mb"]),
                })
            except (ValueError, KeyError):
                continue
    return rows


def parse_ts(ts_str):
    """Parse an ISO-8601 string (with Z or ±HHMM offset) to a timezone-aware datetime."""
    if not ts_str:
        return None
    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 4:
        print("Usage: plot_recovery_io.py <perf_csv> <results_json> <output_png>")
        sys.exit(1)

    perf_csv     = sys.argv[1]
    results_json = sys.argv[2]
    output_png   = sys.argv[3]

    rows = load_perf(perf_csv)
    if not rows:
        print(f"ERROR: No data rows in {perf_csv}", file=sys.stderr)
        sys.exit(1)

    with open(results_json) as f:
        res = json.load(f)

    # Reference epoch: t_analysis_start (UTC epoch float)
    t_start_dt = parse_ts(res.get("t_analysis_start"))
    t_start_epoch = t_start_dt.timestamp() if t_start_dt else rows[0]["ts"]

    def to_elapsed(ts_str):
        """Convert a timestamp string to elapsed seconds from analysis start."""
        dt = parse_ts(ts_str)
        if dt is None:
            return None
        return dt.timestamp() - t_start_epoch

    # ── Time series ────────────────────────────────────────────────────────────
    elapsed  = [max(0.0, r["ts"] - t_start_epoch) for r in rows]
    read_mb  = [r["read_mb"]        for r in rows]
    write_mb = [r["write_mb"]       for r in rows]
    cpu      = [r["cpu"]            for r in rows]
    mem_gb   = [r["mem_mb"] / 1024  for r in rows]
    rbd_gb   = [r["rbd_mb"] / 1024  for r in rows]

    xmax = elapsed[-1] * 1.02 if elapsed else 100.0

    # ── Recovery events ────────────────────────────────────────────────────────
    # Each entry: elapsed_seconds (or None if event not captured)
    ev = {
        "fault":      to_elapsed(res.get("t_fault_injected")),
        "down":       to_elapsed(res.get("t_down")),
        "marked_out": to_elapsed(res.get("t_marked_out")),
        "recovering": to_elapsed(res.get("t_recovering")),
        "healthy":    to_elapsed(res.get("t_healthy")),
    }

    # Vertical-line styling: (color, linestyle, short label)
    EV_STYLE = {
        "fault":      ("#ff4444", "--", "OSD stopped"),
        "down":       ("#ff8c00", "-.", "OSD down"),
        "marked_out": ("#ffd700", "--", "Marked out"),
        "recovering": ("#00d4ff", "--", "Recovery start"),
        "healthy":    ("#44ee88", "--", "Healthy"),
    }

    # Shaded region boundaries
    grace_start = ev.get("fault") or ev.get("down")
    grace_end   = ev.get("marked_out")
    recov_start = grace_end
    recov_end   = ev.get("healthy")

    # ── Colour palette (matches plot_io.py) ───────────────────────────────────
    DARK  = "#0f1117"
    GRID  = "#1e2130"
    TICK  = "#8890a8"
    READ  = "#00d4ff"
    WRITE = "#ff6b6b"
    CPU_C = "#a8ff78"
    MEM_C = "#f7971e"
    RBD_C = "#c471ed"

    # ── Layout ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 13), facecolor=DARK)
    gs  = gridspec.GridSpec(4, 1, hspace=0.52, figure=fig,
                            top=0.90, bottom=0.07, left=0.09, right=0.97)
    axes = [fig.add_subplot(gs[i]) for i in range(4)]

    def style_ax(ax, ylabel, title):
        ax.set_facecolor(DARK)
        ax.tick_params(colors=TICK, labelsize=8)
        ax.spines[:].set_color(GRID)
        ax.yaxis.label.set_color(TICK)
        ax.xaxis.label.set_color(TICK)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_title(title, color="#c8cde0", fontsize=9, pad=5, loc="left")
        ax.grid(True, color=GRID, linewidth=0.5, linestyle="--")
        ax.set_xlim(0, xmax)

    def add_event_annotations(ax):
        """Add vertical lines and shaded regions to ax."""
        ymin, ymax = ax.get_ylim()
        yrange = max(ymax - ymin, 1e-6)

        # Shaded regions first (behind lines)
        if grace_start is not None and grace_end is not None and grace_start < grace_end:
            ax.axvspan(grace_start, min(grace_end, xmax),
                       alpha=0.08, color="#aaaaaa", zorder=1)
        if recov_start is not None and recov_end is not None and recov_start < recov_end:
            ax.axvspan(recov_start, min(recov_end, xmax),
                       alpha=0.10, color="#ff6600", zorder=1)

        # Vertical lines + rotated labels
        label_y_frac = [0.90, 0.78, 0.66, 0.54, 0.42]
        for i, (key, (col, ls, label)) in enumerate(EV_STYLE.items()):
            x = ev.get(key)
            if x is None or x < 0 or x > xmax:
                continue
            ax.axvline(x, color=col, linewidth=1.3, linestyle=ls,
                       alpha=0.90, zorder=2)
            text_y = ymin + yrange * label_y_frac[i % len(label_y_frac)]
            ax.text(x + xmax * 0.004, text_y, label,
                    color=col, fontsize=6.5, rotation=90,
                    va="top", ha="left", zorder=3)

    # ── Panel 1: Read throughput ───────────────────────────────────────────────
    ax = axes[0]
    peak_r = max(read_mb) if read_mb else 1.0
    avg_r  = sum(read_mb) / len(read_mb) if read_mb else 0.0
    ax.fill_between(elapsed, read_mb, alpha=0.20, color=READ)
    ax.plot(elapsed, read_mb, color=READ, lw=1.6,
            label=f"Read  (peak {peak_r:.1f} MB/s)")
    ax.axhline(avg_r, color=READ, lw=0.8, linestyle=":", alpha=0.6,
               label=f"Avg read {avg_r:.1f} MB/s")
    style_ax(ax, "MB/s", "Read throughput  —  analysis input from RBD")
    add_event_annotations(ax)

    region_patches = [
        Patch(facecolor="#aaaaaa", alpha=0.35, label="Grace period (~600 s)"),
        Patch(facecolor="#ff6600", alpha=0.40, label="Recovery"),
    ]
    ax.legend(handles=ax.get_lines() + region_patches,
              fontsize=7, framealpha=0.15, labelcolor="white",
              facecolor="#1e2130", edgecolor=GRID)

    # ── Panel 2: Write throughput ──────────────────────────────────────────────
    ax = axes[1]
    peak_w = max(write_mb) if write_mb else 1.0
    ax.fill_between(elapsed, write_mb, alpha=0.20, color=WRITE)
    ax.plot(elapsed, write_mb, color=WRITE, lw=1.6,
            label=f"Write (peak {peak_w:.1f} MB/s)")
    style_ax(ax, "MB/s", "Write throughput  —  PDF analysis output to RBD")
    add_event_annotations(ax)
    ax.legend(fontsize=7, framealpha=0.15, labelcolor="white",
              facecolor="#1e2130", edgecolor=GRID)

    # ── Panel 3: CPU + Memory (dual axis) ─────────────────────────────────────
    ax = axes[2]
    ax.plot(elapsed, cpu, color=CPU_C, lw=1.4, label="CPU %")
    ax_r = ax.twinx()
    ax_r.plot(elapsed, mem_gb, color=MEM_C, lw=1.4, linestyle="--", label="Mem (GB)")
    ax_r.tick_params(colors=TICK, labelsize=8)
    ax_r.set_ylabel("Memory (GB)", color=TICK, fontsize=8)
    ax_r.spines[:].set_color(GRID)
    style_ax(ax, "CPU %", "CPU utilisation & memory usage")
    add_event_annotations(ax)
    lines  = ax.get_lines() + ax_r.get_lines()
    labels = [l.get_label() for l in lines]
    ax.legend(lines, labels, fontsize=7, framealpha=0.15, labelcolor="white",
              facecolor="#1e2130", edgecolor=GRID)

    # ── Panel 4: RBD disk usage ────────────────────────────────────────────────
    ax = axes[3]
    final_gb = rbd_gb[-1] if rbd_gb else 0.0
    ax.fill_between(elapsed, rbd_gb, alpha=0.25, color=RBD_C)
    ax.plot(elapsed, rbd_gb, color=RBD_C, lw=1.6,
            label=f"RBD used: {final_gb:.1f} GB")
    style_ax(ax, "GB used", "RBD cumulative disk usage")
    ax.set_xlabel("Elapsed time (seconds from analysis start)", color=TICK, fontsize=8)
    add_event_annotations(ax)
    ax.legend(fontsize=7, framealpha=0.15, labelcolor="white",
              facecolor="#1e2130", edgecolor=GRID)

    # ── Event legend (bottom of figure) ───────────────────────────────────────
    ev_patches = [
        Patch(color=col, label=f"{label}  (t={ev[k]:.0f} s)" if ev.get(k) else label)
        for k, (col, _, label) in EV_STYLE.items()
        if ev.get(k) is not None
    ]
    if ev_patches:
        fig.legend(handles=ev_patches, loc="lower center",
                   ncol=len(ev_patches), fontsize=7.5,
                   framealpha=0.15, labelcolor="white",
                   facecolor="#1e2130", edgecolor=GRID,
                   bbox_to_anchor=(0.5, 0.01))

    # ── Title ─────────────────────────────────────────────────────────────────
    osd_id     = res.get("osd_id", "?")
    mpi_procs  = res.get("mpi_procs", "?")
    dur_a      = res.get("duration_analysis_s")
    dur_a_str  = f"{dur_a:.0f} s" if dur_a else "?"
    dur_r      = res.get("duration_recovery_s")
    dur_r_str  = f"{dur_r:.0f} s ({dur_r/60:.1f} min)" if dur_r else "?"
    dur_gp     = res.get("duration_grace_period_s")
    dur_gp_str = f"{dur_gp:.0f} s" if dur_gp else "?"

    fig.suptitle(
        f"Gray-Scott PDF Analysis  —  OSD {osd_id} failure & recovery  |  "
        f"MPI: {mpi_procs}  |  Analysis: {dur_a_str}  |  "
        f"Grace: {dur_gp_str}  |  Recovery: {dur_r_str}",
        color="#e0e4f0", fontsize=9.5, y=0.96,
    )

    plt.savefig(output_png, dpi=150, bbox_inches="tight", facecolor=DARK)
    print(f"Plot written to: {output_png}")


if __name__ == "__main__":
    main()
