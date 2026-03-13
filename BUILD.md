# Clambake -- BUILD.md

## Tech Stack
| Layer | Technology |
|-------|-----------|
| Language | Python 3.8+ |
| Database | PostgreSQL 15 + pgvector (schema: `clambake` in `docdb`) |
| Embeddings | Ollama `nomic-embed-text` (768-dim, optional) |
| MCP Server | FastMCP (stdio transport) for native Claude Code integration |
| Agent Roles | `.claude/commands/*.md` (Claude Code custom commands) |
| Python deps | `psycopg2-binary`, `requests` |
| MCP deps | `mcp[cli]`, `asyncpg` |

## Key Files
| File | Purpose |
|------|---------|
| `clambake.py` | CLI -- memory, infra, messaging, session lifecycle |
| `schema.sql` | Database DDL for all tables and views |
| `mcp_server/server.py` | MCP server entry point |
| `mcp_server/tools/memory.py` | MCP memory tools (store, search, update, delete, checkpoint) |
| `mcp_server/tools/projects.py` | MCP project listing tools |
| `mcp_server/db.py` | Async Postgres connection pool + CLI fallback |
| `.claude/commands/architect.md` | Architect agent role (design, specs) |
| `.claude/commands/backend.md` | Backend engineer role (Python, SQL, APIs) |
| `.claude/commands/frontend.md` | Frontend engineer role (React, HTML/CSS) |
| `.claude/commands/devops.md` | DevOps role (Docker, Traefik, infra) |
| `.claude/commands/qa.md` | QA/Validator role (testing, review) |
| `ROADMAP.md` | Architecture vision and future plans |
| `backup-pre-pivot/` | Pre-pivot code backup (orchestration, pipelines, agent workers) |

## Database
Schema: `clambake` in database `docdb` on `localhost:5433`

### Active Tables
| Table | Purpose |
|-------|---------|
| `instances` | Active Claude Code sessions (heartbeat, project, status) |
| `messages` | Inter-instance messages (expire 24h) |
| `project_memory` | Per-project knowledge with pgvector embeddings |
| `global_memory` | Cross-project knowledge with pgvector embeddings |
| `infra_state` | Live service status (expire 4h) |
| `session_log` | Audit trail (cleaned after 90 days) |

### Legacy Tables (retained for backward compat, to be dropped)
| Table | Purpose |
|-------|---------|
| `tasks` | Multi-agent task dispatch (replaced by Agent Teams) |
| `agent_roles` | Role definitions (replaced by .claude/commands/) |
| `pipeline_templates` | Workflow definitions (replaced by Agent Teams) |

Key views: `active_instances`, `unread_messages`, `recent_activity`, `current_infra`

## Commands
```bash
# Session lifecycle
clambake up                          # Start session
clambake down                        # End session
clambake checkin                     # Lightweight heartbeat (used by hooks)
clambake status                      # Active instances + messages

# Memory
clambake remember --project X --type T --title "..." --content "..."
clambake recall --project X [--search "query"]
clambake recall --global [--search "query"]
clambake update-memory ID [--content "..."] [--status S]
clambake embed-backfill              # Generate missing embeddings

# Messaging
clambake send --to @all --type warning --subject "..."
clambake inbox

# Infrastructure
clambake infra                       # View status
clambake infra-warn --service X --status S --message "..."

# Agent roles (slash commands)
/architect <task description>
/backend <task description>
/frontend <task description>
/devops <task description>
/qa <task description>
```

## Environment Variables
| Variable | Purpose |
|----------|---------|
| `CLAMBAKE_DB_HOST` | Postgres host (default: `localhost`) |
| `CLAMBAKE_DB_PORT` | Postgres port (default: `5433`) |
| `CLAMBAKE_DB_NAME` | Database name (default: `docdb`) |
| `CLAMBAKE_DB_USER` | Database user (default: `postgres`) |
| `CLAMBAKE_DB_PASS` | Database password (default: `postgres`) |
| `OLLAMA_BASE_URL` | Ollama endpoint (default: `http://localhost:11434`) |
| `CLAMBAKE_EMBEDDING_MODEL` | Embedding model (default: `nomic-embed-text`) |

## Architecture
Clambake is a peer-to-peer coordination system. All Claude Code instances communicate
through a shared Postgres database. The CLI handles session lifecycle, memory with
semantic search (pgvector), messaging, and infrastructure monitoring. Orchestration
(agent spawning, task dispatch, dependencies) is handled by Claude Code's native
Agent Teams system. Agent roles are defined as `.claude/commands/*.md` custom commands.
