import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
# --- 1. Data Definition (Extracted from the visual plot) ---
time = np.array([0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600])

latency = np.array([7.8, 8.5, 13.5, 17.5, 28.0, 15.0, 8.3, 4.0, 2.5, 1.8, 1.5]) * 1000
request_rate = np.array([13.0, 11.5, 7.5, 5.5, 9.5, 14.0, 11.0, 11.5, 11.2, 11.0, 10.2]) * 1000
cpu_usage = np.array([27, 30, 65, 86, 96, 91, 22, 7, 5, 2, 1])
error_rate = np.array([0.0, 0.0, 0.0, 0.1, 0.3, 0.6, 0.2, 0.0, 0.0, 0.0, 0.0])

# --- 2. Figure Setup ---
fig = plt.figure(figsize=(14, 7.5))
# Create main axes with room at the top for the custom timeline and bottom for metadata
ax1 = fig.add_axes([0.08, 0.22, 0.82, 0.62])

# Twin axes for CPU Usage and Error Rate
ax2 = ax1.twinx()
ax3 = ax1.twinx()
ax3.spines["right"].set_position(("axes", 1.06))

# --- 3. Plotting the Series ---
p1, = ax1.plot(time, latency, color='#1f57df', marker='o', linewidth=1.5, label='Latency P95 (ms)')
p2, = ax1.plot(time, request_rate, color='#2ca02c', marker='s', linestyle='--', linewidth=1.5, label='Request Rate (req/s)')
p3, = ax2.plot(time, cpu_usage, color='#e31a1c', marker='^', linestyle='-.', linewidth=1.5, label='CPU Usage / Limit (%)')
p4, = ax3.plot(time, error_rate, color='#911eb4', marker='D', linestyle=':', linewidth=1.5, label='Error Rate (%)')

# --- 4. Styling Axes, Limits, and Labels ---
ax1.set_xlabel('Time (s)', fontsize=11, fontweight='bold', labelpad=8)
ax1.set_ylabel('Latency P95 (ms) / Request Rate (req/s)', fontsize=11, fontweight='bold')
ax2.set_ylabel('CPU Usage to Limit (%)', fontsize=11, color='#e31a1c', fontweight='bold')
ax3.set_ylabel('Error Rate (%)', fontsize=11, color='#911eb4', fontweight='bold')

ax1.set_xlim(-10, 610)
ax1.set_xticks(np.arange(0, 601, 60))
ax1.set_ylim(-500, 30000)
ax1.set_yticks(np.arange(0, 30001, 5000))
ax1.set_yticklabels(['0', '5k', '10k', '15k', '20k', '25k', '30k'])

ax2.set_ylim(-2, 120)
ax2.tick_params(axis='y', labelcolor='#e31a1c')
ax3.set_ylim(-0.2, 10)
ax3.tick_params(axis='y', labelcolor='#911eb4')

ax1.grid(True, axis='y', linestyle='-', alpha=0.3)

# Legend Configuration
lines = [p1, p2, p3, p4]
ax1.legend(lines, [l.get_label() for l in lines], loc='upper left', frameon=True, facecolor='white', framealpha=0.9, fontsize=10)

# --- 5. Timeline Phase Shading (Background Backgrounds) ---
t0, t1, t2, t_mid_start, t3, t4 = 100, 240, 330, 348, 385, 595

ax1.axvspan(-10, t0, color='#e2f0d9', alpha=0.4)       # Baseline
ax1.axvspan(t0, t1, color='#fce4d6', alpha=0.4)        # Fault Injected
ax1.axvspan(t1, t2, color='#fff2cc', alpha=0.4)        # Degraded
ax1.axvspan(t2, t3, color='#e6f2ff', alpha=0.4)        # Mitigation Executed
ax1.axvspan(t_mid_start, t3, color='#b4c6e7', alpha=0.6) # Highlighted Execution Blue Bar
ax1.axvspan(t3, 610, color='#f2f2f2', alpha=0.5)       # Recovery

# Execution label text inside the blue bar
ax1.text((t_mid_start + t3)/2, 22000, "Execution\n(85.7 s)", color='black', fontsize=9, ha='center', fontweight='bold')

# --- 6. Vertical Milestones Events ---
milestones = [
    (t0, "$t_0$\nFault Injected"),
    (t1, "$t_1$\nObservation End"),
    (t2, "$t_2$\nMitigation Executed"),
    (t3, "$t_3$\nRollout Complete"),
    (t4, "$t_4$\nPost Mitigation")
]

for t_val, label in milestones:
    ax1.axvline(x=t_val, color='grey', linestyle='--', linewidth=1)
    ax1.text(t_val, 30500, label, fontsize=9, ha='center', va='bottom', clip_on=False)

# --- 7. Top Phase Indicator Banner ---
# We create a secondary conceptual axis layout on top of the main chart
timeline_y = 33800
ax1.plot([-10, t0], [timeline_y, timeline_y], color='green', marker='o', clip_on=False, linewidth=2)
ax1.plot([t0, t1], [timeline_y, timeline_y], color='red', marker='o', clip_on=False, linewidth=2)
ax1.plot([t1, t2], [timeline_y, timeline_y], color='red', marker='o', clip_on=False, linewidth=2)
ax1.plot([t2, t3], [timeline_y, timeline_y], color='blue', marker='o', clip_on=False, linewidth=2)
ax1.plot([t3, 610], [timeline_y, timeline_y], color='green', marker='>', clip_on=False, linewidth=2)

ax1.text((-10 + t0)/2, timeline_y + 800, "① Baseline\n(Healthy)", color='green', fontsize=10, ha='center', fontweight='bold', clip_on=False)
ax1.text((t0 + t1)/2, timeline_y + 800, "② Fault Injected\n(CPU Hog)", color='darkred', fontsize=10, ha='center', fontweight='bold', clip_on=False)
ax1.text((t1 + t2)/2, timeline_y + 800, "③ Degraded\n(Observation)", color='darkred', fontsize=10, ha='center', fontweight='bold', clip_on=False)
ax1.text((t2 + t3)/2, timeline_y + 800, "④ Mitigation Executed\n(scale_up_cpu)", color='blue', fontsize=10, ha='center', fontweight='bold', clip_on=False)
ax1.text((t3 + 610)/2, timeline_y + 800, "⑤ Recovery\n(Validation)", color='green', fontsize=10, ha='center', fontweight='bold', clip_on=False)

fig.suptitle("Motivating Example: CPU Hog Fault in 'cart' – Evolving Metrics & Execution Validation", fontsize=13, fontweight='bold', y=0.96)

# --- 8. Bottom Outcomes Summary Block (Table Representation) ---
# Create a dedicated clean bounding box panel below the main plotting frame
rect = FancyBboxPatch(
    (0.02, 0.03), 0.96, 0.11, 
    facecolor='white', 
    edgecolor='grey', 
    boxstyle="round,pad=0.01", 
    transform=fig.transFigure, 
    clip_on=False
)
fig.patches.extend([rect])

# Adding the textual components inside the bottom table layout
fig.text(0.06, 0.09, "Latency P95 (ms)\n↑ 8.0k → 28.0k\n(↑ 250%)", color='red', fontsize=9.5, ha='center', fontweight='bold')
fig.text(0.16, 0.05, "", bbox=dict(boxstyle="square", edgecolor='lightgrey', facecolor='none', lw=1), transform=fig.transFigure) # delimiter line simulation

fig.text(0.24, 0.09, "CPU Usage / Limit (%)\n↑ 30% → 100%\n(CPU Saturated)", color='darkred', fontsize=9.5, ha='center', fontweight='bold')
fig.text(0.38, 0.09, "Error Rate (%)\n↑ 0% → 1.0%", color='purple', fontsize=9.5, ha='center', fontweight='bold')

fig.text(0.53, 0.09, "Mitigation Action\nscale_up_cpu\n(+ CPU Limit)", color='blue', fontsize=9.5, ha='center', fontweight='bold')

fig.text(0.71, 0.09, "After Mitigation\nLatency P95 ↓ 28.0k → 1.5k\nCPU ↓ 100% → 5.3%\nError Rate ↓ 1.0% → 0%", color='green', fontsize=9.5, ha='center', fontweight='bold')

fig.text(0.89, 0.09, "Outcomes\nSHS: 0.5 → 1.0\nFRQ: 0.947\nReward: 0.993 ✓", color='black', fontsize=9.5, ha='center', fontweight='bold')

# Draw vertical segment dividers inside the block manually to cleanly mirror the original layout
for x_pos in [0.17, 0.34, 0.47, 0.61, 0.81]:
    fig.text(x_pos, 0.04, "┊", color='grey', fontsize=30, ha='center')

# Displaying and saving
# plt.savefig("replicated_motivation_plot.png", dpi=300, bbox_inches='tight')
plt.show()