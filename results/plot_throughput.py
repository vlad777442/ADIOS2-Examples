#!/usr/bin/env python3
"""
Plot throughput over time from Gray-Scott analysis on Ceph RBD
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Read the CSV data
df = pd.read_csv('pdf-rbd.bp_throughput.csv')

# Create figure with multiple subplots
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle('Gray-Scott Analysis on Ceph RBD - Throughput Analysis', fontsize=14, fontweight='bold')

# Plot 1: Read Throughput over time
ax1 = axes[0, 0]
ax1.plot(df['step'], df['read_throughput_mb_s'], 'b-', alpha=0.7, linewidth=0.8)
ax1.axhline(y=df['read_throughput_mb_s'].mean(), color='r', linestyle='--', label=f'Mean: {df["read_throughput_mb_s"].mean():.1f} MB/s')
ax1.set_xlabel('Step')
ax1.set_ylabel('Read Throughput (MB/s)')
ax1.set_title('Read Throughput Over Time')
ax1.legend()
ax1.grid(True, alpha=0.3)

# Plot 2: Write Throughput over time
ax2 = axes[0, 1]
ax2.plot(df['step'], df['write_throughput_mb_s'], 'g-', alpha=0.7, linewidth=0.8)
ax2.axhline(y=df['write_throughput_mb_s'].mean(), color='r', linestyle='--', label=f'Mean: {df["write_throughput_mb_s"].mean():.1f} MB/s')
ax2.set_xlabel('Step')
ax2.set_ylabel('Write Throughput (MB/s)')
ax2.set_title('Write Throughput Over Time')
ax2.legend()
ax2.grid(True, alpha=0.3)

# Plot 3: Time breakdown per step
ax3 = axes[1, 0]
ax3.stackplot(df['step'], 
              df['read_time_sec'], 
              df['compute_time_sec'], 
              df['write_time_sec'],
              labels=['Read', 'Compute', 'Write'],
              colors=['#3498db', '#2ecc71', '#e74c3c'],
              alpha=0.8)
ax3.set_xlabel('Step')
ax3.set_ylabel('Time (seconds)')
ax3.set_title('Time Breakdown per Step')
ax3.legend(loc='upper right')
ax3.grid(True, alpha=0.3)

# Plot 4: Cumulative I/O time
ax4 = axes[1, 1]
ax4.plot(df['step'], df['cumulative_read_time'], 'b-', label='Cumulative Read Time', linewidth=2)
ax4.plot(df['step'], df['cumulative_write_time'], 'g-', label='Cumulative Write Time', linewidth=2)
ax4.set_xlabel('Step')
ax4.set_ylabel('Cumulative Time (seconds)')
ax4.set_title('Cumulative I/O Time')
ax4.legend()
ax4.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('rbd_throughput_analysis.png', dpi=150, bbox_inches='tight')
print("Plot saved to: rbd_throughput_analysis.png")

# Print summary statistics
print("\n=== Summary Statistics ===")
print(f"Total steps: {len(df)}")
print(f"Read throughput:  Mean={df['read_throughput_mb_s'].mean():.1f} MB/s, Min={df['read_throughput_mb_s'].min():.1f}, Max={df['read_throughput_mb_s'].max():.1f}")
print(f"Write throughput: Mean={df['write_throughput_mb_s'].mean():.1f} MB/s, Min={df['write_throughput_mb_s'].min():.1f}, Max={df['write_throughput_mb_s'].max():.1f}")
print(f"Total read time:  {df['cumulative_read_time'].iloc[-1]:.1f} seconds")
print(f"Total write time: {df['cumulative_write_time'].iloc[-1]:.1f} seconds")
print(f"Total data read:  {df['data_read_mb'].sum():.0f} MB ({df['data_read_mb'].sum()/1024:.1f} GB)")

plt.show()
