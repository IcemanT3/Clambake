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

# Default: core 4 roles (backwards compatible)
# Extended roles (scout, plan-reviewer, documenter, red-team) are opt-in via args
if [ ${#ROLES[@]} -eq 0 ]; then
    ROLES=(planner coder qa reviewer)
fi

# Ensure clambake is enabled for all subprocesses
export CLAMBAKE_ENABLED=1
python3 /opt/clambake/clambake.py enable 2>/dev/null

# --- FIX for Issue #13: API key propagation ---
# Load ANTHROPIC_API_KEY from .env file if not already set.
# This must be done HERE (not via `source` in tmux send-keys)
# because nested tmux->runuser->bash-c quoting silently drops source commands.
if [ -z "$ANTHROPIC_API_KEY" ]; then
    for ENVFILE in /opt/clambake/.env /home/ubuntu/.env_claude /root/.env; do
        if [ -f "$ENVFILE" ]; then
            KEY=$(grep '^ANTHROPIC_API_KEY=' "$ENVFILE" | head -1 | cut -d= -f2-)
            if [ -n "$KEY" ]; then
                export ANTHROPIC_API_KEY="$KEY"
                echo "Loaded ANTHROPIC_API_KEY from $ENVFILE"
                break
            fi
        fi
    done
fi

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "WARNING: ANTHROPIC_API_KEY not set. Agents will fail to authenticate."
    echo "Set it via: export ANTHROPIC_API_KEY=sk-ant-..."
fi

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
# Each window gets ANTHROPIC_API_KEY exported explicitly (not via source)
# to avoid Issue #13 — nested quoting in tmux silently drops source commands
COUNTER=0
for ROLE in "${ROLES[@]}"; do
    COUNTER=$((COUNTER + 1))
    WNAME="${ROLE}-${COUNTER}"
    tmux new-window -t "$SESSION" -n "$WNAME"
    tmux send-keys -t "$SESSION:$WNAME" \
        "export ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}' CLAMBAKE_ENABLED=1 && bash $WORKER $ROLE $PROJECT /workspace" Enter
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
