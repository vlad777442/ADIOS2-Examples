import re

with open("scripts/plot_recovery_io.py", "r") as f:
    code = f.read()

# 1. arrays
code = code.replace(
    'write_mb = [r["write_mb"] for r in rows]',
    'write_mb = [r["write_mb"] for r in rows]\n    rx_mb    = [r["rx_mb"]    for r in rows]\n    tx_mb    = [r["tx_mb"]    for r in rows]'
)

# 2. subplots
code = code.replace(
    'fig, axes = plt.subplots(2, 1, figsize=(11, 6),\n                             sharex=True,\n                             facecolor="white",\n                             gridspec_kw={"hspace": 0.35,\n                                          "top": 0.88, "bottom": 0.10,\n                                          "left": 0.08, "right": 0.97})',
    'fig, axes = plt.subplots(4, 1, figsize=(11, 10),\n                             sharex=True,\n                             facecolor="white",\n                             gridspec_kw={"hspace": 0.40,\n                                          "top": 0.88, "bottom": 0.06,\n                                          "left": 0.08, "right": 0.97})'
)

# 3. panel 2 xlabel
code = code.replace(
    'ax2.set_xlabel("Elapsed time (s from analysis start)", fontsize=10)',
    ''
)

# 4. Add panels 3 and 4
code = code.replace(
    '# ── Title ─────────────────────────────────────────────────────────────────',
    '''# ── Panel 3: Network Rx ──────────────────────────────────────────────────
    ax3 = axes[2]
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
    ax4 = axes[3]
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

    # ── Title ─────────────────────────────────────────────────────────────────'''
)

# 5. Comment out Produces a publication-ready 2-panel
code = code.replace(
    'Produces a publication-ready 2-panel white-background PNG',
    'Produces a publication-ready 4-panel white-background PNG'
)

# 6. headers comment
code = code.replace(
    'rbd_usage_mb, io_read_mb, io_write_mb',
    'rbd_usage_mb, io_read_mb, io_write_mb, net_rx_mb, net_tx_mb'
)

with open("scripts/plot_recovery_io.py", "w") as f:
    f.write(code)

