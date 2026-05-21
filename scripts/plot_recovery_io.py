#!/usr/bin/env python3
"""
Plot Gray-Scott analysis I/O performance with OSD recovery annotations.

Produces a publication-ready 2-panel white-background PNG showing read and
write throughput over time with shaded recovery phases and event markers.

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
import re
from datetime import datetime, timezone

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import matplotlib.ticker as ticker
    from matplotlib.patches import Patch
except ImportError:
    os.system("sudo apt-get install -y python3-matplotlib --quiet")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import matplotlib.ticker as ticker
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
                    "read_mb":  float(row["io_read_mb"]),
                    "write_mb": float(row["io_write_mb"]),
                    "rx_mb":    float(row.get("net_rx_mb", 0)),
                    "tx_mb":    float(row.get("net_tx_mb", 0)),
                })
            except (ValueError, KeyError):
                continue
    return rows


def parse_ts(ts_str):
    """Parse an ISO-8601 string (with Z or ±HHMM/±HH:MM offset) to datetime."""
    if not ts_str:
        return None
    s = ts_str.strip()
    s = s.replace("Z", "+00:00")
    s = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
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

    no_fault = res.get("no_fault", False)

    # Reference epoch from analysis start
    t_start_dt    = parse_ts(res.get("t_analysis_start"))
    t_start_epoch = t_start_dt.timestamp() if t_start_dt else rows[0]["ts"]

    def to_elapsed(key):
        dt = parse_ts(res.get(key))
        if dt is None:
            return None
        return dt.timestamp() - t_start_epoch

    # ── Time series ────────────────────────────────────────────────────────────
    
    def smooth(data, window=5):
        if len(data) < window or window < 2: return data
        res = []
        for i in range(len(data)):
            s = max(0, i - window//2)
            e = min(len(data), i + window//2 + 1)
            res.append(sum(data[s:e]) / (e - s))
        return res

    elapsed  = [max(0.0, r["ts"] - t_start_epoch) for r in rows]
    read_mb  = smooth([r["read_mb"]  for r in rows], 7)
    write_mb = smooth([r["write_mb"] for r in rows], 7)
    rx_mb    = smooth([r["rx_mb"]    for r in rows], 7)
    tx_mb    = smooth([r["tx_mb"]    for r in rows], 7)
    xmax     = elapsed[-1] * 1.02

    # ── Recovery event timestamps (elapsed s) ─────────────────────────────────
    ev = {
        "fault":      to_elapsed("t_fault_injected"),
        "down":       to_elapsed("t_down"),
        "marked_out": to_elapsed("t_marked_out"),
        "recovering": to_elapsed("t_recovering"),
        "healthy":    to_elapsed("t_healthy"),
    }

    # Use "down" if available, else "fault" as the OSD-stopped reference
    osd_stopped = ev["down"] if ev["down"] is not None else ev["fault"]

    # Shaded region boundaries
    grace_start = osd_stopped
    grace_end   = ev["marked_out"]
    recov_start = ev["recovering"] if ev["recovering"] is not None else grace_end
    recov_end   = ev["healthy"]

    # ── Colours (paper-friendly) ───────────────────────────────────────────────
    C_READ    = "#2166ac"   # blue
    C_WRITE   = "#d6604d"   # red-orange
    C_GRACE   = "#f4a460"   # sandy brown shading
    C_RECOV   = "#f08080"   # light coral shading
    C_DOWN    = "#e74c3c"   # event line: OSD down/stopped
    C_OUT     = "#e67e22"   # event line: marked out
    C_RSTART  = "#27ae60"   # event line: recovery start
    C_HEALTHY = "#1a9641"   # event line: healthy

    # ── Figure setup (paper style) ─────────────────────────────────────────────
    plt.rcParams.update({
        "font.family":        "sans-serif",
        "font.size":          10,
        "axes.linewidth":     0.8,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "xtick.direction":    "out",
        "ytick.direction":    "out",
        "xtick.major.size":   4,
        "ytick.major.size":   4,
        "xtick.minor.size":   2,
        "ytick.minor.size":   2,
        "xtick.minor.visible": True,
        "ytick.minor.visible": True,
        "grid.color":         "#dddddd",
        "grid.linewidth":     0.5,
        "grid.linestyle":     "--",
        "figure.dpi":         150,
    })

    fig, axes = plt.subplots(3, 1, figsize=(11, 8.5),
                             sharex=True,
                             facecolor="white",
                             gridspec_kw={"hspace": 0.40,
                                          "top": 0.88, "bottom": 0.08,
                                          "left": 0.08, "right": 0.97})

    def add_annotations(ax):
        """Shaded regions + vertical event lines + in-plot labels."""
        ymin, ymax = ax.get_ylim()
        text_top = ymax - (ymax - ymin) * 0.04   # just below top edge

        # ── shaded regions ────────────────────────────────────────────────────
        if not no_fault:
            if grace_start is not None and grace_end is not None and grace_start < grace_end:
                ax.axvspan(grace_start, min(grace_end, xmax),
                           color=C_GRACE, alpha=0.30, zorder=0)
            if recov_start is not None:
                ax.axvspan(recov_start,
                           min(recov_end, xmax) if recov_end else xmax,
                           color=C_RECOV, alpha=0.30, zorder=0)

        # ── vertical event lines ───────────────────────────────────────────────
        events = []
        if not no_fault:
            if osd_stopped is not None:
                events.append((osd_stopped, C_DOWN,   "--", "OSD down"))
            if ev["marked_out"] is not None:
                events.append((ev["marked_out"], C_OUT, "--", "OSD marked out"))
            if recov_start is not None:
                events.append((recov_start, C_RSTART, "--", "recovery start"))
            if ev["healthy"] is not None:
                events.append((ev["healthy"], C_HEALTHY, "--", "recovered"))

        for x, color, ls, label in events:
            if x < 0 or x > xmax:
                continue
            ax.axvline(x, color=color, linewidth=1.5, linestyle=ls,
                       alpha=0.9, zorder=3)
            ax.text(x + xmax * 0.005, text_top, label,
                    color=color, fontsize=8, fontweight="bold",
                    va="top", ha="left", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec=color, alpha=0.75, linewidth=0.6))

    # ── Panel 1: Read throughput ───────────────────────────────────────────────
    ax1 = axes[0]
    avg_r = sum(read_mb) / len(read_mb)
    ax1.fill_between(elapsed, read_mb, alpha=0.18, color=C_READ)
    ax1.plot(elapsed, read_mb, color=C_READ, lw=1.4, label="Read throughput")
    ax1.axhline(avg_r, color=C_READ, lw=1.0, linestyle=":", alpha=0.7,
                label=f"Mean: {avg_r:.1f} MB/s")
    ax1.set_ylabel("Read throughput (MB/s)", fontsize=10)
    ax1.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax1.grid(True, which="major")
    ax1.set_facecolor("white")
    ax1.set_xlim(0, xmax)
    add_annotations(ax1)

    # shade legend patches
    legend_handles = [ln for ln in ax1.get_lines()
                      if not ln.get_label().startswith("_")]
    if not no_fault:
        legend_handles += [
            Patch(facecolor=C_GRACE, alpha=0.45, label="Grace period (~600 s)"),
            Patch(facecolor=C_RECOV, alpha=0.45, label="EC recovery"),
        ]
    ax1.legend(handles=legend_handles, fontsize=8.5, frameon=True,
               framealpha=0.9, loc="lower right")

    # ── Panel 2: Write throughput ──────────────────────────────────────────────
    # ax2 = axes[1]
    # avg_w = sum(write_mb) / len(write_mb)
    # ax2.fill_between(elapsed, write_mb, alpha=0.18, color=C_WRITE)
    # ax2.plot(elapsed, write_mb, color=C_WRITE, lw=1.4, label="Write throughput")
    # ax2.axhline(avg_w, color=C_WRITE, lw=1.0, linestyle=":", alpha=0.7,
    #             label=f"Mean: {avg_w:.2f} MB/s")
    # ax2.set_ylabel("Write throughput (MB/s)", fontsize=10)
    # ax2.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    # ax2.grid(True, which="major")
    # ax2.set_facecolor("white")
    # add_annotations(ax2)
    # ax2.legend(fontsize=8.5, frameon=True, framealpha=0.9, loc="upper right")

    # ── Panel 3: Network Rx ──────────────────────────────────────────────────
    ax3 = axes[1]
    avg_rx = sum(rx_mb) / len(rx_mb) if rx_mb else 0
    C_RX = "#9b59b6"
    ax3.fill_between(elapsed, rx_mb, alpha=0.18, color=C_RX)
    ax3.plot(elapsed, rx_mb, color=C_RX, lw=1.4, label="Network Rx (total)")
    ax3.axhline(avg_rx, color=C_RX, lw=1.0, linestyle=":", alpha=0.7,
                label=f"Mean: {avg_rx:.2f} MB/s")
    ax3.set_ylabel("Net Rx (MB/s)", fontsize=10)
    ax3.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax3.grid(True, which="major")
    ax3.set_facecolor("white")
    add_annotations(ax3)
    ax3.legend(fontsize=8.5, frameon=True, framealpha=0.9, loc="upper right")

    # ── Panel 4: Network Tx ──────────────────────────────────────────────────
    ax4 = axes[2]
    avg_tx = sum(tx_mb) / len(tx_mb) if tx_mb else 0
    C_TX = "#34495e"
    ax4.fill_between(elapsed, tx_mb, alpha=0.18, color=C_TX)
    ax4.plot(elapsed, tx_mb, color=C_TX, lw=1.4, label="Network Tx (total)")
    ax4.axhline(avg_tx, color=C_TX, lw=1.0, linestyle=":", alpha=0.7,
                label=f"Mean: {avg_tx:.2f} MB/s")
    ax4.set_ylabel("Net Tx (MB/s)", fontsize=10)
    ax4.set_xlabel("Elapsed time (s from analysis start)", fontsize=10)
    ax4.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax4.grid(True, which="major")
    ax4.set_facecolor("white")
    add_annotations(ax4)
    ax4.legend(fontsize=8.5, frameon=True, framealpha=0.9, loc="upper right")

    # ── Title ─────────────────────────────────────────────────────────────────
    osd_id    = res.get("osd_id", "?")
    pool      = "EC RS(k=6,m=4)" if "ec" in str(res.get("input", "")).lower() else "3-replica RBD"
    dur_a     = res.get("duration_analysis_s")
    dur_a_str = f"{dur_a:.0f} s ({dur_a/60:.1f} min)" if dur_a else "?"
    dur_gp    = res.get("duration_grace_period_s")
    gp_str    = f"{dur_gp:.0f} s" if dur_gp else "n/a"
    dur_r     = res.get("duration_recovery_s")
    r_str     = f"{dur_r:.0f} s ({dur_r/60:.1f} min)" if dur_r else "ongoing"

    if no_fault:
        title = (f"Gray-Scott PDF Analysis  —  {pool}  |  No-fault baseline\n"
                 f"Analysis duration: {dur_a_str}")
    else:
        title = (f"Gray-Scott PDF Analysis  —  {pool}  |  OSD {osd_id} failure & recovery\n"
                 f"Analysis: {dur_a_str}   Grace period: {gp_str}   Recovery: {r_str}")

    fig.suptitle(title, fontsize=10.5, fontweight="bold", y=0.97)

    plt.savefig(output_png, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Plot written to: {output_png}")


if __name__ == "__main__":
    main()
