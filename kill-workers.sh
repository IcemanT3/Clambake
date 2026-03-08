#!/bin/bash
# kill-workers: Kill all Claude Code worker processes (spares the boss)
# Usage: kill-workers

BOSS_PID=$(MSYS_NO_PATHCONV=1 wmic process where "ProcessId=$PPID" get ParentProcessId //VALUE 2>/dev/null | grep -o '[0-9]*')

echo "Boss PID: $BOSS_PID (will be spared)"
echo "Killing worker node.exe processes..."

KILLED=0
while IFS=, read -r _ pid _ _ mem; do
  pid=$(echo "$pid" | tr -d '"' | tr -d ' ')
  [ "$pid" = "PID" ] && continue
  [ "$pid" = "$BOSS_PID" ] && continue
  [ -z "$pid" ] && continue
  taskkill //PID "$pid" //F >/dev/null 2>&1 && {
    echo "  Killed PID $pid ($mem)"
    ((KILLED++))
  }
done < <(MSYS_NO_PATHCONV=1 tasklist //FI "IMAGENAME eq node.exe" //FO CSV 2>/dev/null)

echo "Done. Killed $KILLED worker process(es)."

# Clean up per-worker instance files
CLEANED=0
for f in "$HOME"/.clambake_instance_worker_* "$HOME"/.clambake_instance_human; do
  if [ -f "$f" ]; then
    rm -f "$f"
    ((CLEANED++))
  fi
done

if [ "$CLEANED" -gt 0 ]; then
  echo "Cleaned up $CLEANED per-worker instance file(s)."
fi
