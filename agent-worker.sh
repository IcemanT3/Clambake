#!/usr/bin/env bash
# Clambake Agent Worker
# Runs in a tmux pane. Registers, claims tasks, launches Claude Code.
# Usage: ./agent-worker.sh <role> <project> [working-dir]
#
# Example: ./agent-worker.sh coder mindmeld /workspace
#
# Database isolation: Agents use mindmeld_agent user which CANNOT access docdb.

ROLE="$1"
PROJECT="${2:-mindmeld}"
WORKDIR="${3:-/workspace}"
MAX_TURNS="${MAX_TURNS:-200}"
# Ensure clambake is enabled and Claude doesn't think it's nested
export CLAMBAKE_ENABLED=1
unset CLAUDECODE 2>/dev/null

# Detect if running inside container or on host
if [ -f /opt/clambake/clambake.py ]; then
    CLAMBAKE="python3 /opt/clambake/clambake.py"
    DB_HOST="${MINDMELD_DB_HOST:-host.docker.internal}"
else
    CLAMBAKE="python F:/Docker/clambake/clambake.py"
    DB_HOST="${MINDMELD_DB_HOST:-localhost}"
fi

# Database credentials for agents — restricted to mindmeld DB only
export MINDMELD_DB_HOST="$DB_HOST"
export MINDMELD_DB_PORT="${MINDMELD_DB_PORT:-5433}"
export MINDMELD_DB_NAME="${MINDMELD_DB_NAME:-mindmeld}"
export MINDMELD_DB_USER="${MINDMELD_DB_USER:-mindmeld_agent}"
export MINDMELD_DB_PASS="${MINDMELD_DB_PASS:-mindmeld_agent}"

if [ -z "$ROLE" ]; then
    echo "Usage: ./agent-worker.sh <role> [project] [working-dir]"
    echo "Roles: planner, coder, qa, reviewer"
    exit 1
fi

echo "========================================="
echo "  CLAMBAKE AGENT: $ROLE"
echo "  Project: $PROJECT"
echo "  Working dir: $WORKDIR"
echo "  DB: $MINDMELD_DB_NAME@$MINDMELD_DB_HOST (user: $MINDMELD_DB_USER)"
echo "  Max turns: $MAX_TURNS"
echo "========================================="

# Register with clambake
$CLAMBAKE register --project "$PROJECT" --dir "$WORKDIR" --model haiku
echo ""

# Get our role's system prompt
SYSTEM_PROMPT=$($CLAMBAKE role-get "$ROLE" 2>/dev/null | sed -n '/System Prompt:/,$ p' | tail -n +2)

if [ -z "$SYSTEM_PROMPT" ]; then
    echo "ERROR: Could not fetch role '$ROLE'. Run 'clambake role-seed' first."
    exit 1
fi

echo "Role loaded. Checking for tasks..."
echo ""

# Main loop: find and claim tasks
while true; do
    # Look for available tasks matching our role
    AVAILABLE=$($CLAMBAKE task-list --available --role "$ROLE" 2>/dev/null)

    if echo "$AVAILABLE" | grep -q "none found"; then
        echo "[$(date +%H:%M:%S)] No tasks available for $ROLE. Waiting 30s..."
        $CLAMBAKE heartbeat --status idle
        sleep 30
        continue
    fi

    echo "$AVAILABLE"
    echo ""

    # Extract first available task ID
    TASK_ID=$(echo "$AVAILABLE" | grep '#' | head -1 | sed 's/.*#\([0-9]*\).*/\1/')

    if [ -z "$TASK_ID" ]; then
        echo "[$(date +%H:%M:%S)] Could not parse task ID. Waiting 30s..."
        sleep 30
        continue
    fi

    echo "[$(date +%H:%M:%S)] Claiming task #$TASK_ID..."
    CLAIM_OUTPUT=$($CLAMBAKE task-claim "$TASK_ID" 2>&1)
    CLAIM_STATUS=$?

    if [ $CLAIM_STATUS -ne 0 ]; then
        echo "Claim failed (probably grabbed by another agent). Retrying..."
        sleep 5
        continue
    fi

    echo "$CLAIM_OUTPUT"
    echo ""

    # Extract the spec from claim output
    SPEC=$(echo "$CLAIM_OUTPUT" | sed -n '/=== SPEC ===/,$ p' | tail -n +2)
    TASK_TITLE=$(echo "$CLAIM_OUTPUT" | head -1 | sed 's/CLAIMED: #[0-9]* — //')
    FILE_SCOPE=$(echo "$CLAIM_OUTPUT" | sed -n '/=== FILE SCOPE ===/,/^$/p' | grep -v '=== FILE' | tr -d ' ')

    # Build the prompt for Claude Code
    PROMPT="You are working as the $ROLE agent on project $PROJECT.

$SYSTEM_PROMPT

YOUR CURRENT TASK (#$TASK_ID): $TASK_TITLE

SPEC:
$SPEC

DATABASE CONNECTION (you can ONLY access the mindmeld database):
  Host: $MINDMELD_DB_HOST
  Port: $MINDMELD_DB_PORT
  Database: $MINDMELD_DB_NAME
  User: $MINDMELD_DB_USER
  Password: $MINDMELD_DB_PASS

CRITICAL RULES:
- You may ONLY connect to the 'mindmeld' database. NEVER connect to 'docdb'.
- Use the mindmeld_agent credentials above for ALL database operations.
- Connect with: PGPASSWORD=\$MINDMELD_DB_PASS psql -h \$MINDMELD_DB_HOST -p \$MINDMELD_DB_PORT -U \$MINDMELD_DB_USER -d \$MINDMELD_DB_NAME
- Only modify files within your file scope. Do not touch files outside your task.
- Write all code files to your working directory.
- When done, output a clear SUMMARY of what you built/changed with file paths.
- If you get stuck or encounter blockers, describe the problem clearly."

    if [ -n "$FILE_SCOPE" ]; then
        PROMPT="$PROMPT

FILE SCOPE (only modify these files):
$FILE_SCOPE"
    fi

    echo "========================================="
    echo "  LAUNCHING CLAUDE CODE FOR TASK #$TASK_ID"
    echo "  Model: haiku | Max turns: $MAX_TURNS"
    echo "========================================="

    # Write prompt to temp file to avoid shell escaping issues
    PROMPT_FILE=$(mktemp /tmp/clambake-prompt-XXXXX.txt)
    echo "$PROMPT" > "$PROMPT_FILE"

    # Launch Claude Code in print mode with max-turns for autonomous operation
    # -p: print mode (auto-exits when done, still shows all output)
    # --max-turns: limits agentic turns, prevents runaway agents
    # --permission-mode bypassPermissions: skips workspace trust + tool prompts
    cd "$WORKDIR"
    claude -p --permission-mode bypassPermissions --model haiku --max-turns "$MAX_TURNS" "$(cat "$PROMPT_FILE")"
    CLAUDE_EXIT=$?
    rm -f "$PROMPT_FILE"

    if [ $CLAUDE_EXIT -eq 0 ]; then
        echo ""
        echo "[$(date +%H:%M:%S)] Claude exited successfully. Marking task #$TASK_ID done."
        $CLAMBAKE task-done "$TASK_ID" --result "Completed by $ROLE agent"
    else
        echo ""
        echo "[$(date +%H:%M:%S)] Claude exited with error. Marking task #$TASK_ID failed."
        $CLAMBAKE task-fail "$TASK_ID" --result "Agent exited with code $CLAUDE_EXIT"
    fi

    echo ""
    echo "Task finished. Looking for next task..."
    sleep 5
done
