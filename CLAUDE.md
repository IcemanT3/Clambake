# Clambake — Multi-Instance Coordination

## What This Is
Clambake is a lightweight coordination system for multiple Claude Code instances. It uses the shared Postgres database (`docdb` on `swarm-postgres:5433`) to let instances communicate, share knowledge, and avoid conflicts.

## Setup
- **CLI**: `python F:/Docker/clambake/clambake.py <command>`
- **Database**: `docdb` on `localhost:5433` (same as Doc DB v2)
- **Schema**: `clambake` (separate from Doc DB's `public` schema)
- **Requires**: `psycopg2` (already installed in Doc DB v2 container; install locally with `pip install psycopg2-binary`)

## Session Protocol — FOLLOW THIS

### On Session Start (MANDATORY)
```bash
# 1. Register yourself
python F:/Docker/clambake/clambake.py register --project <project-name> --dir <working-dir>

# 2. Check for messages from other instances
python F:/Docker/clambake/clambake.py inbox

# 3. Load project knowledge
python F:/Docker/clambake/clambake.py recall --project <project-name>

# 4. Load global knowledge
python F:/Docker/clambake/clambake.py recall --global --limit 10
```

### Before Risky Operations (MANDATORY)
```bash
# Before Docker restart/rebuild
python F:/Docker/clambake/clambake.py send --to @all --type warning --subject "Restarting Docker" --body "Details here"

# Before editing shared config files
python F:/Docker/clambake/clambake.py send --to @all --type blocker --subject "Editing docker-compose" --body "Details here"

# After completing the operation
python F:/Docker/clambake/clambake.py send --to @all --type done --subject "Docker rebuild complete"
```

### When You Learn Something (SHOULD DO)
```bash
# Issues, fixes, decisions, patterns, gotchas
python F:/Docker/clambake/clambake.py remember --project <name> --type <type> --title "..." --content "..."

# Cross-project knowledge
python F:/Docker/clambake/clambake.py remember --global --type <type> --title "..." --content "..."
```

### On Session End
```bash
python F:/Docker/clambake/clambake.py deregister
```

## CLI Quick Reference
| Command | Purpose |
|---------|---------|
| `register --project X` | Join the coordination system |
| `heartbeat --task "..."` | Update what you're working on |
| `status` | See all active instances and recent activity |
| `send --to X --subject Y` | Send a message |
| `inbox` | Check unread messages |
| `read <id>` | Read full message |
| `remember --project X --type Y --title Z --content W` | Store knowledge |
| `recall --project X [--type Y] [--search Z]` | Query project memory |
| `recall --global [--type Y]` | Query global memory |
| `update-memory <id> --status resolved` | Update memory status |
| `log --action X --summary Y` | Log a session action |
| `deregister` | Leave the coordination system |
| `cleanup` | Remove stale instances and expired messages |

## Memory Types
**Project**: architecture, feature, issue, fix, decision, pattern, gotcha, update
**Global**: infrastructure, convention, tool, preference, credential, lesson

## Message Types
info, warning, blocker, request, done
