#!/usr/bin/env bash
# Clambake Multi-Agent Launcher
# Opens Windows Terminal with one tab per agent + a dashboard tab
# Usage: ./launch-agents.sh [project] [roles...]
#
# Examples:
#   ./launch-agents.sh mindmeld                          # all 4 roles
#   ./launch-agents.sh mindmeld planner coder            # just planner + coder
#   ./launch-agents.sh mindmeld coder coder coder        # 3 coders
#
# Prerequisites:
#   - Windows Terminal (wt.exe)
#   - clambake enabled (clambake enable)
#   - Tasks created (clambake task-list --available)
#   - Roles seeded (clambake role-seed)

PROJECT="${1:-mindmeld}"
shift 2>/dev/null
ROLES=("$@")
WORKDIR="F:/Claude App/Mind Meld"
CLAMBAKE="python F:/Docker/clambake/clambake.py"
WORKER="F:/Docker/clambake/agent-worker.sh"

# Default: all 4 roles
if [ ${#ROLES[@]} -eq 0 ]; then
    ROLES=(planner coder qa reviewer)
fi

echo "========================================="
echo "  CLAMBAKE MULTI-AGENT LAUNCHER"
echo "  Project: $PROJECT"
echo "  Agents: ${ROLES[*]}"
echo "========================================="
echo ""

# Verify prerequisites
echo "Checking prerequisites..."
$CLAMBAKE task-list --project "$PROJECT" --available
echo ""

# Build the wt.exe command
# First tab: dashboard (auto-refreshing task list)
WT_CMD="wt.exe --title \"Clambake Dashboard\" bash -c \"while true; do clear; echo '=== CLAMBAKE DASHBOARD === ($(date))'; echo ''; $CLAMBAKE task-list --project $PROJECT; echo ''; $CLAMBAKE status; sleep 10; done\""

# Add a tab for each agent
COUNTER=0
for ROLE in "${ROLES[@]}"; do
    COUNTER=$((COUNTER + 1))
    WT_CMD="$WT_CMD ; new-tab --title \"$ROLE-$COUNTER\" bash \"$WORKER\" \"$ROLE\" \"$PROJECT\" \"$WORKDIR\""
done

echo "Launching $COUNTER agents in Windows Terminal..."
echo ""

# Launch Windows Terminal with all tabs
eval "$WT_CMD" &

echo "Windows Terminal launched with:"
echo "  Tab 1: Dashboard (auto-refreshing every 10s)"
for i in $(seq 1 $COUNTER); do
    echo "  Tab $((i+1)): ${ROLES[$((i-1))]}-$i agent"
done
echo ""
echo "Manage from any terminal:"
echo "  $CLAMBAKE task-list               # check all tasks"
echo "  $CLAMBAKE task-list --available    # see claimable tasks"
echo "  $CLAMBAKE status                   # see active instances"
