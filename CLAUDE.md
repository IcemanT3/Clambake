# Clambake — Multi-Instance Coordination

## What This Is
Clambake coordinates multiple Claude Code instances through a shared Postgres database. It handles instance registration, inter-instance messaging, persistent project/global memory with semantic search, infrastructure status tracking, and multi-agent task dispatch.

## Setup
- **CLI**: `python F:/Docker/clambake/clambake.py <command>`
- **Database**: `docdb` on `localhost:5433`, schema: `clambake`
- **Requires**: `psycopg2-binary`, `requests` (pip install)
- **Embeddings**: Ollama `nomic-embed-text` at `localhost:11434` (optional, for semantic search)
- **Enabled by default** — degrades gracefully if Postgres is down

## Session Protocol

```bash
# Start of session (one command does it all)
clambake up

# End of session
clambake down
```

`clambake up` auto-detects the project from your working directory, registers the instance, checks inbox, loads project + global memories, and shows infrastructure warnings. If Postgres is unreachable, it prints a warning and exits cleanly.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `up [--project X]` | Start session (register + inbox + recall + infra check) |
| `down [--summary "..."]` | End session (deregister + log) |
| `status` | See active instances and recent messages |
| `infra` | Show live infrastructure status |
| `infra-warn --service X --status S --message "..."` | Report service status |
| `project-list` | List all projects with memory counts |
| `remember --project X --type T --title "..." --content "..."` | Store project knowledge |
| `remember --global --type T --title "..." --content "..."` | Store global knowledge |
| `recall --project X [--search Q]` | Query project memory (semantic) |
| `recall --global [--search Q]` | Query global memory (semantic) |
| `update-memory ID [--content "..."] [--status S]` | Update existing entry |
| `send --to @all --type warning --subject "..."` | Warn before risky ops |
| `inbox` | Check unread messages |
| `digest --hours 24` | Activity summary |
| `embed-backfill` | Generate missing embeddings |

## Memory Types
**Project**: architecture, feature, issue, fix, decision, pattern, gotcha, update
**Global**: infrastructure, convention, tool, preference, credential, lesson

## Before Risky Operations
```bash
clambake send --to @all --type warning --subject "Restarting Docker" --body "Details"
# ... do the work ...
clambake send --to @all --type done --subject "Docker restart complete"
```

## Multi-Agent Task Dispatch
```bash
clambake role-seed                          # Create default roles
clambake task-create --project X --title "..." --role coder --description "..."
clambake task-list --available --role coder  # See claimable tasks
clambake task-claim 5                       # Claim a task
clambake task-done 5 --result "summary"     # Mark complete
```

## Full Documentation
See [README.md](README.md) for the complete technical manual.
