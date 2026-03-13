#!/bin/bash
# spawn-claude: Launch Claude Code workers in visible Windows Terminal panes
# Usage: spawn-claude "task1" "task2" "task3" ... [-d directory] [--human]
#
# Examples:
#   spawn-claude "fix upload"                          # 1 worker, right pane
#   spawn-claude "fix upload" "write tests"            # 2 workers
#   spawn-claude "task1" "task2" "task3"               # 3 workers (quad layout)
#   spawn-claude "t1" "t2" --human                     # 2 workers + human shell
#   spawn-claude "t1" "t2" "t3" "t4" "t5" -d "F:\Docker\doc-db-v2"

BAT='F:\Docker\clambake\claude-worker.bat'
# Default to current directory (convert to Windows path for .bat)
DIR=$(pwd -W 2>/dev/null || pwd)
TASKS=()
HUMAN=0

# Parse arguments — collect tasks, detect -d flag for directory, --human flag
while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--dir)
      DIR="$2"
      shift 2
      ;;
    --human)
      HUMAN=1
      shift
      ;;
    *)
      TASKS+=("$1")
      shift
      ;;
  esac
done

COUNT=${#TASKS[@]}

if [ "$COUNT" -eq 0 ]; then
  echo "Usage: spawn-claude \"task1\" [\"task2\" ...] [-d directory] [--human]"
  echo ""
  echo "  Spawns 1+ Claude Code workers in visible Windows Terminal panes."
  echo "  Default directory: current working directory"
  echo ""
  echo "  Options:"
  echo "    -d, --dir DIR   Working directory for workers"
  echo "    --human         Also spawn a human shell pane (Git Bash, no claude)"
  echo ""
  echo "  Layout:"
  echo "    1 worker  = right pane"
  echo "    2 workers = right column (stacked)"
  echo "    3 workers = quad (boss top-left)"
  echo "    4+ workers = fills grid then adds more splits"
  exit 1
fi

TOTAL=$COUNT
[ "$HUMAN" -eq 1 ] && ((TOTAL++))

echo "Spawning $COUNT worker(s)$([ $HUMAN -eq 1 ] && echo ' + 1 human shell')..."

# Self-promote to boss
python F:/Docker/clambake/clambake.py heartbeat --role boss --task "orchestrating $TOTAL pane(s)" 2>/dev/null

# Worker 1 - always goes to the right
MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -V -- "$BAT" "${TASKS[0]}" "$DIR" "1" "worker"
echo "  Worker 1 (right): ${TASKS[0]}"

if [ "$COUNT" -ge 2 ]; then
  sleep 0.5
  # Worker 2 - split boss pane horizontally (bottom-left)
  MSYS_NO_PATHCONV=1 wt.exe -w 0 move-focus left
  MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -H -- "$BAT" "${TASKS[1]}" "$DIR" "2" "worker"
  echo "  Worker 2 (bottom-left): ${TASKS[1]}"
fi

if [ "$COUNT" -ge 3 ]; then
  sleep 0.5
  # Worker 3 - split right pane horizontally (bottom-right)
  MSYS_NO_PATHCONV=1 wt.exe -w 0 move-focus right
  MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -H -- "$BAT" "${TASKS[2]}" "$DIR" "3" "worker"
  echo "  Worker 3 (bottom-right): ${TASKS[2]}"
fi

if [ "$COUNT" -ge 4 ]; then
  sleep 0.5
  # Worker 4 - split top-right horizontally
  MSYS_NO_PATHCONV=1 wt.exe -w 0 move-focus up
  MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -H -- "$BAT" "${TASKS[3]}" "$DIR" "4" "worker"
  echo "  Worker 4 (top-right split): ${TASKS[3]}"
fi

# Workers 5+ - keep splitting the most recent pane
for (( i=4; i<COUNT; i++ )); do
  sleep 0.5
  MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -H -- "$BAT" "${TASKS[$i]}" "$DIR" "$((i+1))" "worker"
  echo "  Worker $((i+1)) (extra split): ${TASKS[$i]}"
done

# Human shell pane (if requested)
if [ "$HUMAN" -eq 1 ]; then
  sleep 0.5
  MSYS_NO_PATHCONV=1 wt.exe -w 0 split-pane -H -- "$BAT" "" "$DIR" "human" "human"
  echo "  Human shell spawned"
fi

echo ""
echo "Done. $COUNT worker(s)$([ $HUMAN -eq 1 ] && echo ' + 1 human') spawned. Use 'clambake status' to monitor."
