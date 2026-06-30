import matplotlib.pyplot as plt
import matplotlib.patches as patches

import matplotlib.pyplot as plt

# Force Matplotlib to use a font with extensive emoji support
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', 'DejaVu Sans']

# Initialize the figure and axis
fig, ax = plt.subplots(figsize=(18, 11), dpi=100)
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis('off')

# Color Palette Definition
C_BLUE = '#1E40AF'      # Environment & Observability
C_ORANGE = '#EA580C'    # EV-RCA (Diagnosis)
C_GREEN = '#166534'     # EV-AIM (Mitigation)
C_PURPLE = '#5B21B6'    # Learning Loop
C_BG_LIGHT = '#F8FAFC'  # General Box Backgrounds

# ----------------------------------------------------
# 1. TOP ROW: Environment, Monitoring, Fault Injection
# ----------------------------------------------------

# Box 1: Cloud-Native Environment
ax.add_patch(patches.FancyBboxPatch((2, 78), 28, 18, boxstyle="round,pad=0.3", edgecolor=C_BLUE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(3, 93, "1  CLOUD-NATIVE ENVIRONMENT", color=C_BLUE, fontsize=11, fontweight='bold')
ax.text(5, 84, "🛒\nRobot-Shop", ha='center', fontsize=10)
ax.text(14, 84, "🧦\nSock-Shop", ha='center', fontsize=10)
ax.text(23, 84, "🛍️\nOnline Boutique", ha='center', fontsize=10)
ax.add_patch(patches.FancyBboxPatch((4, 79), 24, 3, boxstyle="round,pad=0.2", edgecolor=C_BLUE, facecolor='#EFF6FF'))
ax.text(16, 80, "Microservice Applications", color=C_BLUE, ha='center', fontsize=9, fontweight='bold')

# Box 2: Monitoring & Telemetry
ax.add_patch(patches.FancyBboxPatch((33, 78), 30, 18, boxstyle="round,pad=0.3", edgecolor=C_BLUE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(34, 93, "2  MONITORING & TELEMETRY", color=C_BLUE, fontsize=11, fontweight='bold')
ax.text(38, 85, "🔥\nPrometheus", ha='center', fontsize=10)
ax.text(48, 85, "🔭\nOpenTelemetry", ha='center', fontsize=10)
ax.text(58, 85, "📊\nGrafana", ha='center', fontsize=10)
ax.text(48, 79, "📊 Metrics   📄 Logs   🔔 Events   ☍ Traces", color=C_BLUE, ha='center', fontsize=9, fontweight='bold')

# Box 3: Fault Injection
ax.add_patch(patches.FancyBboxPatch((66, 78), 32, 18, boxstyle="round,pad=0.3", edgecolor=C_BLUE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(67, 93, "3  FAULT INJECTION", color=C_BLUE, fontsize=11, fontweight='bold')
ax.text(70, 85, "⚙️", fontsize=24, color=C_BLUE)
fault_text = "• CPU Stress / Hog\n• Memory Stress\n• Disk Stress\n• K8s-Native Faults\n  (Pod Kill, Network, Dependency...)"
ax.text(75, 82, fault_text, fontsize=9, va='center')

# ----------------------------------------------------
# 2. MAIN MIDDLE BOX: EV-Ops Framework
# ----------------------------------------------------
ax.add_patch(patches.FancyBboxPatch((2, 28), 96, 46, boxstyle="round,pad=0.5", edgecolor=C_BLUE, facecolor='none', linewidth=2))
ax.text(50, 71, "EV-Ops: Execution-Validated Operations for Cloud-Native Systems", color=C_BLUE, fontsize=14, fontweight='bold', ha='center')

# --- EV-RCA Engine (Orange Box) ---
ax.add_patch(patches.FancyBboxPatch((3, 29), 38, 39, boxstyle="round,pad=0.3", edgecolor=C_ORANGE, facecolor='#FFF7ED', linewidth=1.5))
ax.text(5, 65, "🧬 EV-RCA: Root Cause Analysis Engine", color=C_ORANGE, fontsize=11, fontweight='bold')

# Sub-components of RCA
# 1. Metric Rep
ax.add_patch(patches.FancyBboxPatch((4, 31), 11, 32, boxstyle="round,pad=0.2", edgecolor=C_ORANGE, facecolor='white'))
ax.text(5, 61, "1  Metric\n    Representation", color=C_ORANGE, fontsize=9, fontweight='bold')
ax.text(9.5, 45, "📈\nCPU\n\n📉\nMemory\n\n📈\nDisk", ha='center', fontsize=9)

# 2. Multi-View Projection
ax.add_patch(patches.FancyBboxPatch((16, 31), 11, 32, boxstyle="round,pad=0.2", edgecolor=C_ORANGE, facecolor='white'))
ax.text(17, 61, "2  Multi-View\n    Projection", color=C_ORANGE, fontsize=9, fontweight='bold')
ax.text(21.5, 52, "[Low-Rank]\n[Orthogonal]\n[1D CNN]", ha='center', color='gray', fontsize=9)

# 3. Anomaly Scoring
ax.add_patch(patches.FancyBboxPatch((28, 31), 12, 32, boxstyle="round,pad=0.2", edgecolor=C_ORANGE, facecolor='white'))
ax.text(29, 61, "3  Anomaly Scoring\n    & Localization", color=C_ORANGE, fontsize=9, fontweight='bold')
ax.text(34, 40, "Top-K Candidates:\n• Service A\n• Service B\n• Service K", fontsize=8)

# --- Incident Context Bridge ---
ax.add_patch(patches.FancyBboxPatch((42, 35), 8, 25, boxstyle="round,pad=0.2", edgecolor='gray', facecolor='white'))
ax.text(46, 57, "Incident\nContext", ha='center', fontweight='bold', fontsize=10)
ax.text(46, 50, "📄", fontsize=20, ha='center')
ax.text(46, 40, "Top-K causes,\nsnapshots,\nevents", ha='center', fontsize=8)

# --- EV-AIM Engine (Green Box) ---
ax.add_patch(patches.FancyBboxPatch((51, 29), 46, 39, boxstyle="round,pad=0.3", edgecolor=C_GREEN, facecolor='#F0FDF4', linewidth=1.5))
ax.text(53, 65, "EV-AIM: Autonomous Incident Mitigation Engine", color=C_GREEN, fontsize=11, fontweight='bold')

aim_steps = [
    ("1  Retrieve", "🔍", "Retrieve Top-K\nsimilar episodes"),
    ("2  Plan", "🧠", "LLM Planner\ngenerates\nmitigation plan"),
    ("3  Execute", "💻", "Generate Ansible\nplaybook &\nexecute on K8s"),
    ("4  Validate", "📋", "Collect metrics\n& evaluate\nimpact"),
    ("5  Feedback", "📊", "Compute FRs\n& Reward\nmechanisms")
]

for i, (title, icon, desc) in enumerate(aim_steps):
    x_pos = 52 + (i * 8.8)
    ax.add_patch(patches.FancyBboxPatch((x_pos, 31), 8, 32, boxstyle="round,pad=0.2", edgecolor=C_GREEN, facecolor='white'))
    ax.text(x_pos + 4, 60, title, color=C_GREEN, ha='center', fontsize=9, fontweight='bold')
    ax.text(x_pos + 4, 50, icon, ha='center', fontsize=18)
    ax.text(x_pos + 4, 38, desc, ha='center', fontsize=8)


# ----------------------------------------------------
# 3. BOTTOM ROW: Learning Loop & Storage
# ----------------------------------------------------

# Box 6: Adaptive Experience Retrieval
ax.add_patch(patches.FancyBboxPatch((2, 10), 22, 12, boxstyle="round,pad=0.3", edgecolor=C_PURPLE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(3, 19, "6  ADAPTIVE EXPERIENCE RETRIEVAL", color=C_PURPLE, fontsize=9, fontweight='bold')
ax.text(5, 14, "🔄", fontsize=20)
ax.text(10, 14, "Continuously updated\nindex for similarity\nsearch & reuse", fontsize=8, va='center')

# Box 7: Experience Store
ax.add_patch(patches.FancyBboxPatch((28, 10), 40, 12, boxstyle="round,pad=0.3", edgecolor=C_BLUE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(33, 19, "7  EXPERIENCE STORE (EPISODIC REPOSITORY)", color=C_BLUE, fontsize=9, fontweight='bold')
ax.text(30, 14, "🛢️", fontsize=24)
store_text = "✓ Fault type & affected service     ✓ FRs, Reward, Resource cost\n✓ Incident context & signatures    ✓ Outcome (success/failure)\n✓ Mitigation plan & actions          ✓ Retries, Rollout time"
ax.text(35, 14, store_text, fontsize=8, va='center')

# Box 8: Learning & Improvement
ax.add_patch(patches.FancyBboxPatch((72, 10), 26, 12, boxstyle="round,pad=0.3", edgecolor=C_PURPLE, facecolor=C_BG_LIGHT, linewidth=1.5))
ax.text(73, 19, "8  LEARNING & IMPROVEMENT", color=C_PURPLE, fontsize=9, fontweight='bold')
ax.text(75, 14, "📈", fontsize=20)
ax.text(80, 14, "Use feedback to refine\nreward, retrieval ranking,\nand planner behavior", fontsize=8, va='center')


# ----------------------------------------------------
# 4. FLOW ARROWS
# ----------------------------------------------------
arrow_props = dict(facecolor='black', edgecolor='none', width=0.5, headwidth=4, headlength=4)

# Top row connections
ax.annotate('', xy=(33, 87), xytext=(30, 87), arrowprops=arrow_props)
ax.annotate('', xy=(66, 87), xytext=(63, 87), arrowprops=arrow_props)

# Framework internal connections
ax.annotate('', xy=(42, 47), xytext=(41, 47), arrowprops=arrow_props)
ax.annotate('', xy=(51, 47), xytext=(50, 47), arrowprops=arrow_props)

# Bottom Loop connections
ax.annotate('', xy=(24, 16), xytext=(28, 16), arrowprops=arrow_props)  # Store to Retrieval
ax.annotate('', xy=(13, 28), xytext=(13, 22), arrowprops=arrow_props)  # Retrieval to Main Framework
ax.annotate('', xy=(72, 16), xytext=(68, 16), arrowprops=arrow_props)  # Store to Learning
ax.annotate('', xy=(50, 28), xytext=(50, 22), arrowprops=arrow_props)  # Framework down to Store

# Feedback loop dashed arrow (Right side back to Fault Injection)
ax.annotate('', xy=(94, 87), xytext=(85, 10),
            arrowprops=dict(arrowstyle="->", color=C_BLUE, linestyle="--", lw=1.5,
                            connectionstyle="bar,angle=180,fraction=-0.2"))


# ----------------------------------------------------
# 5. LEGEND (Bottom Center)
# ----------------------------------------------------
ax.add_patch(patches.Rectangle((15, 2), 70, 5, facecolor='none', edgecolor='lightgray', linestyle='--'))
ax.text(17, 4, "Legend:", fontweight='bold', fontsize=10)

legends = [
    (25, "Environment &\nObservability", C_BLUE),
    (42, "EV-RCA\n(Diagnosis)", C_ORANGE),
    (58, "EV-AIM\n(Mitigation)", C_GREEN),
    (74, "Learning Loop\n(Continuous Improvement)", C_PURPLE)
]

for x, text, color in legends:
    ax.add_patch(patches.Rectangle((x, 3.5), 3, 2, facecolor=color, edgecolor='none'))
    ax.text(x + 4, 4, text, fontsize=8, va='center')

# Show the rendering
plt.tight_layout()
plt.show()