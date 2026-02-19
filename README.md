# Clambake

Multi-instance Claude Code coordination through Postgres.

## The Problem

When running multiple Claude Code instances on different projects simultaneously, they have no awareness of each other. One instance can restart Docker while another is mid-build. Knowledge discovered in one session is lost to the next. Project history lives in fragmented markdown files across dozens of directories.

## The Solution

Clambake uses a shared Postgres database as the coordination layer:

- **Instance Registry** — Every Claude Code session registers itself. All instances can see who's active and what they're working on.
- **Message Bus** — Instances send warnings before risky operations (Docker restarts, config changes) and blockers to prevent conflicts.
- **Project Memory** — What was built, what issues were hit, what decisions were made — all queryable per project.
- **Global Memory** — Cross-project knowledge: infrastructure, conventions, tools, preferences.
- **Session Log** — Audit trail of what happened across all sessions.

## Design Principles

- **No orchestrator** — All instances are peers. The database is the shared brain.
- **Postgres, not files** — Concurrent access, structured queries, single source of truth.
- **Minimal ceremony** — A few CLI commands, not a framework. Fits vibe coding workflow.
- **Token efficient** — Query only what's relevant instead of loading entire markdown files.
- **Existing infrastructure** — Runs in the Postgres you already have. No new containers.

## Inspired By

Cherry-picked the best ideas from:

- **[BMAD Method](https://github.com/bmad-code-org/BMAD-METHOD)** — Architecture as shared contract, structured project context, story-based work boundaries
- **[Overstory](https://github.com/jayminwest/overstory)** — Instance coordination, checkpoint/handoff, named failure modes, propulsion principle

Without the ceremony of BMAD (34 workflows, 9 agent personas) or the complexity of Overstory (tmux swarms, git worktrees, 4-tier merge resolution).

## Quick Start

```bash
# 1. Ensure Postgres is running
docker start swarm-postgres

# 2. Install Python dependency
pip install psycopg2-binary

# 3. Initialize the schema
python clambake.py init

# 4. Register your instance
python clambake.py register --project my-project --dir /path/to/project

# 5. Check who else is working
python clambake.py status

# 6. You're coordinated
```

## Requirements

- Python 3.8+
- PostgreSQL with pgvector (for future semantic search)
- `psycopg2-binary` package

## Database

Clambake creates a `clambake` schema in your existing Postgres database. It does not touch any other schemas or tables.

**Default connection:** `localhost:5433/docdb` (configurable via environment variables)

| Env Var | Default |
|---------|---------|
| `CLAMBAKE_DB_HOST` | `localhost` |
| `CLAMBAKE_DB_PORT` | `5433` |
| `CLAMBAKE_DB_NAME` | `docdb` |
| `CLAMBAKE_DB_USER` | `postgres` |
| `CLAMBAKE_DB_PASS` | `postgres` |

## Schema

| Table | Purpose |
|-------|---------|
| `clambake.instances` | Active instance registry with heartbeats |
| `clambake.messages` | Instance-to-instance communication |
| `clambake.project_memory` | Per-project knowledge base |
| `clambake.global_memory` | Cross-project shared knowledge |
| `clambake.session_log` | Audit trail of actions |

See [schema.sql](schema.sql) for full DDL.

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for the full coordination protocol.

## CLI Reference

See [CLAUDE.md](CLAUDE.md) for the quick reference used by Claude Code instances.

## Future

- [ ] Semantic search over memory via pgvector embeddings
- [ ] Auto-heartbeat via Claude Code hooks
- [ ] Memory deduplication and consolidation
- [ ] Web dashboard for monitoring instances
- [ ] Pre-built project templates (Doc DB, Stalwart, etc.)

## License

MIT
