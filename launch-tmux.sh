#!/usr/bin/env bash
# Clambake tmux Launcher — runs INSIDE the Docker container
# Creates a tmux session with dashboard + agent panes
# Usage: ./launch-tmux.sh [project] [roles...]
#
# Examples:
#   ./launch-tmux.sh mindmeld planner              # Phase 1: just planner
#   ./launch-tmux.sh mindmeld planner coder coder  # planner + 2 coders
#   ./launch-tmux.sh mindmeld                      # all 4 roles

SESSION="clambake"
PROJECT="${1:-mindmeld}"
shift 2>/dev/null
ROLES=("$@")
CLAMBAKE="python3 /opt/clambake/clambake.py"
WORKER="/opt/clambake/agent-worker.sh"

# Default: all 4 roles
if [ ${#ROLES[@]} -eq 0 ]; then
    ROLES=(planner coder qa reviewer)
fi

# Ensure clambake is enabled for all subprocesses
export CLAMBAKE_ENABLED=1
python3 /opt/clambake/clambake.py enable 2>/dev/null

# Kill existing session
tmux kill-session -t "$SESSION" 2>/dev/null

echo "========================================="
echo "  CLAMBAKE ORCHESTRATOR"
echo "  Project: $PROJECT"
echo "  Agents: ${ROLES[*]}"
echo "========================================="

# Verify clambake is reachable
$CLAMBAKE task-list --project "$PROJECT" 2>/dev/null
echo ""

# Create tmux session with dashboard window
tmux new-session -d -s "$SESSION" -n "dashboard"
tmux send-keys -t "$SESSION:dashboard" \
    "while true; do clear; echo '=== CLAMBAKE DASHBOARD ==='; date; echo ''; $CLAMBAKE task-list --project $PROJECT; echo ''; $CLAMBAKE status; sleep 10; done" Enter

# Create one window per agent
COUNTER=0
for ROLE in "${ROLES[@]}"; do
    COUNTER=$((COUNTER + 1))
    WNAME="${ROLE}-${COUNTER}"
    tmux new-window -t "$SESSION" -n "$WNAME"
    tmux send-keys -t "$SESSION:$WNAME" \
        "bash $WORKER $ROLE $PROJECT /workspace" Enter
done

echo ""
echo "Launched $COUNTER agents in tmux session '$SESSION'"
echo ""
echo "Attaching to tmux..."
echo "  Switch tabs: Ctrl+B then number (0=dashboard, 1=first agent, etc.)"
echo "  Detach:      Ctrl+B then D"
echo "  Reattach:    tmux attach -t $SESSION"
echo ""

# Attach
tmux attach -t "$SESSION"
