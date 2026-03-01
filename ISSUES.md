# Clambake Orchestrator — Issues & Fixes Log

## Container: clambake-orchestrator (Docker)
## Date: 2026-02-20

### CRITICAL: Read Swarm Orchestration ISSUES.md First
Many issues here were ALREADY SOLVED in `F:/Claude App/Swarm Orchestration/ISSUES.md`.
That file documents 12 issues with running Claude Code agents in Docker containers.
**READ IT BEFORE MAKING CHANGES.** Don't repeat solved problems.

---

## Issue 1: Root User Blocks --dangerously-skip-permissions
**Problem**: Claude Code refuses `--dangerously-skip-permissions` when running as root (UID 0).
**Error**: `--dangerously-skip-permissions cannot be used with root/sudo privileges`
**From**: Swarm ISSUES.md #1
**Fix**: Dockerfile creates non-root `agent` user. Container runs as `USER agent`.
**Status**: FIXED in Dockerfile

---

## Issue 2: ANTHROPIC_API_KEY Must Be Exported
**Problem**: Environment variables set without `export` aren't visible to Claude Code child processes.
**From**: Swarm ISSUES.md #2
**Fix**: docker-compose.yml passes `ANTHROPIC_API_KEY` as environment variable. `.env` file in clambake dir.
**Status**: FIXED

---

## Issue 3: Claude Code First-Run Wizard / API Key Prompt
**Problem**: Claude Code shows onboarding wizard + API key approval prompt on first interactive run. Blocks automated agents.
**From**: Swarm ISSUES.md #3
**Fix**: Dockerfile pre-writes `/home/agent/.claude.json` with:
- `hasCompletedOnboarding: true`
- `customApiKeyResponses.approved: ["ANTHROPIC_API_KEY"]`
**IMPORTANT**: `-p` (print) mode skips the wizard. Interactive mode STILL shows API key confirmation on first run.
**Additional Fix**: Pre-approve all tools in `/home/agent/.claude/settings.json` so `--dangerously-skip-permissions` isn't needed:
```json
{
  "permissions": {
    "allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)", "Glob(*)", "Grep(*)"]
  }
}
```
**REMAINING ISSUE**: Interactive mode ALWAYS shows the "custom API key detected" prompt once per container lifecycle, regardless of `.claude.json` pre-config or `-p` mode warmup. This is a Claude Code limitation — the interactive mode check is separate from the config file approval.
**Workaround**: Press Enter once in the tmux pane to accept. It saves permanently for that container's lifetime. All subsequent agent launches (including tmux restarts) won't ask again.
**Status**: CANNOT BE FULLY AUTOMATED — one manual keypress per container lifecycle

---

## Issue 4: tmux Socket Permission Denied
**Problem**: tmux socket ownership prevents cross-user access.
**From**: Swarm ISSUES.md #4
**Fix**: Dockerfile creates `/tmp/clambake-tmux` with `chmod 1777`. ENV `TMUX_TMPDIR=/tmp/clambake-tmux`.
**Status**: FIXED in Dockerfile

---

## Issue 5: `python` vs `python3` in Container
**Problem**: Ubuntu 24.04 only has `python3`, not `python`. Scripts using `python` fail silently.
**Error**: `python: command not found`
**Fix**: All scripts must use `python3`. agent-worker.sh auto-detects container vs host.
**Status**: FIXED

---

## Issue 6: Host Paths vs Container Paths
**Problem**: Scripts written on Windows host have paths like `F:/Docker/clambake/clambake.py` which don't exist inside the container. Container paths are `/opt/clambake/clambake.py`.
**Fix**: agent-worker.sh detects environment:
```bash
if [ -f /opt/clambake/clambake.py ]; then
    CLAMBAKE="python3 /opt/clambake/clambake.py"
else
    CLAMBAKE="python F:/Docker/clambake/clambake.py"
fi
```
**Status**: FIXED

---

## Issue 7: wt.exe (Windows Terminal) Spawning Multiple Windows
**Problem**: Launching `wt.exe` from Git Bash with complex nested commands spawns multiple terminal windows instead of one.
**Fix**: Don't use `wt.exe` for tmux. Use Docker container with real tmux. Launch via `start.cmd` which does a single `docker exec -it`.
**Status**: FIXED (abandoned wt.exe approach)

---

## Issue 8: --dangerously-skip-permissions Interactive Confirmation
**Problem**: In interactive mode, `--dangerously-skip-permissions` shows a confirmation prompt ("Press Enter to confirm, Escape to cancel"). This blocks autonomous agents.
**Fix Attempted**: Pre-approve all tools in `settings.json` and don't use the flag.
**REMAINING QUESTION**: Does tool pre-approval in settings.json fully replace --dangerously-skip-permissions? Need to verify that agents can write files, run bash, etc. without any prompts when tools are listed in settings.json allow list.
**Status**: TESTING

---

## Issue 9: CLAMBAKE_ENABLED Not Persisting in tmux Subprocesses
**Problem**: The `CLAMBAKE_ENABLED` flag file is per-user, but tmux subprocesses may not inherit the env var.
**Fix**: launch-tmux.sh explicitly runs `clambake enable` and `export CLAMBAKE_ENABLED=1` before starting tmux.
**Status**: FIXED

---

## Issue 10: Workspace Trust + Permission Bypass Both Prompt in Interactive Mode
**Problem**: In interactive mode, BOTH `--permission-mode bypassPermissions` and `--dangerously-skip-permissions` show a one-time confirmation prompt. Two prompts total on first agent launch:
1. "Detected custom API key" → press Enter
2. "Dangerously skip permissions" → press Enter
**Note**: `-p` (print) mode skips ALL prompts. Interactive mode does not.
**Workaround**: Accept both prompts once per container lifecycle. They save permanently after that.
**Status**: CANNOT BE FULLY AUTOMATED — two manual keypresses per container lifecycle

---

## Issue 11: Docker Desktop 500 Errors / Engine Crashes
**Problem**: Docker Desktop's WSL2 backend intermittently returns HTTP 500 errors from the Linux engine. All container operations fail. Crashes happen during agent workloads — possibly resource exhaustion or WSL2 VM instability.
**Frequency**: Multiple times per session (2026-02-20). Requires Docker Desktop restart from system tray each time.
**Impact**: Kills all running agents. Tasks stuck in "claimed" state with dead instances.
**Fix**: Added stale claim recovery to `clambake cleanup` — resets orphaned tasks from dead agents (no heartbeat in 5 min).
**Long-term Fix**: Migrate orchestrator from Docker Desktop to:
  - Option A: Podman (daemonless, no background service to crash) — **RECOMMENDED**
  - Option B: Docker Engine directly in WSL2 (no Desktop GUI layer)
  - Option C: Native Linux server
**Status**: WORKAROUND (stale claim cleanup). Migration planned as pre-production phase.

---

## Issue 12: Agent Print Mode (-p) Not Visible in tmux
**Problem**: Agents running with `claude -p` (print mode) don't show live tool-by-tool output in tmux panes. The pane appears frozen until the agent finishes.
**Cause**: Print mode writes final output to stdout but doesn't render the interactive TUI with progress indicators.
**Impact**: User can't see agents working in real-time, which was the whole point of tmux.
**Fix Options**:
  - Switch back to interactive mode with `--max-turns N` for auto-exit (needs testing)
  - Accept silent operation and monitor via dashboard/task-list instead
**Status**: OPEN — need to test interactive + --max-turns

---

## Issue 13: Stale Task Claims After Agent Death
**Problem**: When Docker crashes or an agent dies, tasks remain in "claimed" status with a dead instance ID. New agents can't pick them up.
**Fix**: Extended `clambake.cleanup()` function to reset tasks claimed by instances with no heartbeat in 5 minutes. Run `clambake cleanup` to recover.
**Automatic**: The cleanup function now runs as part of normal `clambake cleanup`. Could add periodic auto-cleanup to the dashboard loop.
**Status**: FIXED

---

## Checklist for New Container Builds
Before building a new orchestrator container, verify:
- [ ] Non-root user created (Claude blocks --dangerously-skip-permissions as root)
- [ ] `/home/<user>/.claude.json` pre-written with `hasCompletedOnboarding: true` and API key approved
- [ ] `/home/<user>/.claude/settings.json` pre-written with tool permissions
- [ ] `ANTHROPIC_API_KEY` passed as env var with `export`
- [ ] `TMUX_TMPDIR` set to shared directory with 1777 permissions
- [ ] All scripts use `python3` not `python`
- [ ] All paths are container paths (`/opt/clambake/`) not host paths (`F:/Docker/clambake/`)
- [ ] `.env` file copied from Swarm Orchestration or created fresh with API key

---

## Issue 14: `update-memory --global --status` Crashes (Missing Column)
**Problem**: `global_memory` table has no `status` column, but `cmd_update_memory` tries to SET it when `--global --status` is used.
**Error**: `psycopg2.errors.UndefinedColumn: column "status" of relation "global_memory" does not exist`
**Root Cause**: `project_memory` has a `status` column (active/resolved/deprecated/superseded) but `global_memory` does not — by design, global knowledge doesn't have lifecycle states.
**Fix**: Added guard in `cmd_update_memory` — when `--global` is set, `--status` is ignored with a warning message instead of crashing.
**Status**: FIXED (2026-02-21)

---

## Issue 15: Unicode Emoji Crash on Windows (cp1252 Encoding)
**Problem**: The `digest` command uses Unicode emoji characters (`⚠` U+26A0, `🚨` U+1F6A8) in print statements. Windows console uses cp1252 encoding which can't encode these characters.
**Error**: `UnicodeEncodeError: 'charmap' codec can't encode character '\u26a0' in position 6`
**Affected**: `cmd_digest` function — lines with "Stale Instances" and "Active Blockers" headers.
**Fix**: Replaced emoji with ASCII equivalents: `⚠` → `[!]`, `🚨` → `[!!]`
**Note**: Em dashes (`—`, U+2014) are safe — they map to cp1252 `\x97`.
**Status**: FIXED (2026-02-21)

---

## Semantic Pipeline Verification (2026-02-21)
All Phase 1 semantic features tested and working:

| Feature | Status | Notes |
|---------|--------|-------|
| `remember --project` with embedding | PASS | Returns "(embedded)" confirmation |
| `recall --project --search` (semantic) | PASS | Returns results with similarity scores (0.86 for exact match) |
| `recall --global --search` (semantic) | PASS | Cross-project semantic search works |
| `recall --project` (text/list mode) | PASS | Non-semantic recall works |
| `embed-backfill` | PASS | Found and backfilled 1 missing embedding |
| `register` with CORE MEMORIES | PASS | Shows semantically relevant memories on registration |
| `cleanup` | PASS | Reports stale instance counts correctly |
| `update-memory` | PASS | Title, content, and status updates work (with Issue 14 fix) |
| `digest` | PASS | Shows instances, stale warnings, tasks, memory counts (with Issue 15 fix) |
| `task-list` | PASS | Returns empty list correctly |
| `status` | PASS | Shows active instances and recent messages |
