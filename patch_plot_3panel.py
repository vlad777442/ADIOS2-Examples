import re

with open("scripts/plot_recovery_io.py", "r") as f:
    code = f.read()

# Change figure subplots from 4 to 3
code = code.replace(
    'fig, axes = plt.subplots(4, 1, figsize=(11, 10),',
    'fig, axes = plt.subplots(3, 1, figsize=(11, 8.5),'
)

# Comment out Write IO panel completely
code = code.replace(
    '# ── Panel 2: Write throughput ──────────────────────────────────────────────\n    ax2 = axes[1]',
    '# ── Panel 2: Write throughput ──────────────────────────────────────────────\n    # ax2 = axes[1]'
)
code = code.replace('avg_w = sum(write_mb) / len(write_mb)', '# avg_w = sum(write_mb) / len(write_mb)')
code = code.replace('ax2.fill_between(elapsed, write_mb, alpha=0.18, color=C_WRITE)', '# ax2.fill_between(elapsed, write_mb, alpha=0.18, color=C_WRITE)')
code = code.replace('ax2.plot(elapsed, write_mb, color=C_WRITE, lw=1.4, label="Write throughput")', '# ax2.plot(elapsed, write_mb, color=C_WRITE, lw=1.4, label="Write throughput")')
code = code.replace('ax2.axhline(avg_w, color=C_WRITE, lw=1.0, linestyle=":", alpha=0.7,\n                label=f"Mean: {avg_w:.2f} MB/s")', '# ax2.axhline(avg_w, color=C_WRITE, lw=1.0, linestyle=":", alpha=0.7,\n    #             label=f"Mean: {avg_w:.2f} MB/s")')
code = code.replace('ax2.set_ylabel("Write throughput (MB/s)", fontsize=10)', '# ax2.set_ylabel("Write throughput (MB/s)", fontsize=10)')
code = code.replace('ax2.yaxis.set_minor_locator(ticker.AutoMinorLocator())', '# ax2.yaxis.set_minor_locator(ticker.AutoMinorLocator())')
code = code.replace('ax2.grid(True, which="major")', '# ax2.grid(True, which="major")')
code = code.replace('ax2.set_facecolor("white")', '# ax2.set_facecolor("white")')
code = code.replace('add_annotations(ax2)', '# add_annotations(ax2)')
code = code.replace('ax2.legend(fontsize=8.5, frameon=True, framealpha=0.9, loc="upper right")', '# ax2.legend(fontsize=8.5, frameon=True, framealpha=0.9, loc="upper right")')


# Shift panels 3 and 4 up one index
code = code.replace(
    'ax3 = axes[2]',
    'ax3 = axes[1]'
)
code = code.replace(
    'ax4 = axes[3]',
    'ax4 = axes[2]'
)


with open("scripts/plot_recovery_io.py", "w") as f:
    f.write(code)

