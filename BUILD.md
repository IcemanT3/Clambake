# Clambake -- BUILD.md

## Tech Stack
| Layer | Technology |
|-------|-----------|
| Language | Python 3.8+ |
| Database | PostgreSQL 15 + pgvector (schema: `clambake` in `docdb`) |
| Embeddings | Ollama `nomic-embed-text` (768-dim, optional) |
| Container | Ubuntu 24.04 + Claude Code CLI + tmux |
| Notifications | Microsoft Teams Bot (optional, `teams` profile) |
| Tunnel | Cloudflare Tunnel (optional, `teams` profile) |
| Python deps | `psycopg2-binary`, `requests` |

## Key Files
| File | Purpose |
|------|---------|
| `clambake.py` | CLI -- all commands (session, memory, tasks, pipelines, roles) |
| `schema.sql` | Database DDL for all tables and views |
| `agent-worker.sh` | Agent loop: claim task -> build --allowedTools -> run Claude -> mark done |
| `launch-tmux.sh` | Creates tmux session with dashboard + one window per agent role |
| `spawn-claude.sh` | Launches a single Claude Code instance with role-gated tools |
| `launch-agents.sh` | Alternative agent launcher |
| `start.cmd` | Windows menu launcher (planner, coders, full pipeline, etc.) |
| `kill-workers.sh` | Stops all running agent workers |
| `migrate_markdown.py` | One-time migration from markdown MEMORY.md files to Postgres |
| `docker-compose.yml` | Orchestrator container + Teams bot + Cloudflare tunnel |
| `Dockerfile` | Ubuntu 24.04 + Claude Code + tmux + non-root user |

## Database
Schema: `clambake` in database `docdb` on `localhost:5433`

| Table | Purpose |
|-------|---------|
| `instances` | Active Claude Code sessions (heartbeat, project, status) |
| `messages` | Inter-instance messages (expire 24h) |
| `project_memory` | Per-project knowledge with pgvector embeddings |
| `global_memory` | Cross-project knowledge with pgvector embeddings |
| `infra_state` | Live service status (expire 4h) |
| `tasks` | Multi-agent task dispatch with dependencies and pipeline tracking |
| `agent_roles` | 8 default roles with tool allowlists/denylists |
| `pipeline_templates` | 5 reusable multi-agent workflow definitions |
| `session_log` | Audit trail (cleaned after 90 days) |

Key views: `active_instances`, `unread_messages`, `recent_activity`, `available_tasks`, `current_infra`, `pipeline_runs`

## Docker
- **Orchestrator**: Custom build from Dockerfile (Ubuntu 24.04 + Claude Code + tmux)
  - Container: `clambake-orchestrator`
  - No exposed ports (CLI-only, connects to host Postgres)
  - Volumes: `F:/Docker/clambake:/opt/clambake` (live code), `F:/Claude App/Mind Meld:/workspace`
- **Teams Bot** (optional, `teams` profile): Port 3978, container `clambake-teams-bot`
- **Cloudflare Tunnel** (optional, `teams` profile): `cloudflare/cloudflared:latest`, container `clambake-cloudflared`

## Commands
```bash
# Host CLI (any Git Bash session)
clambake up                          # Start session
clambake down                        # End session
clambake status                      # Active instances
clambake recall --project X          # Query memory
clambake remember --project X ...    # Store memory

# Docker orchestrator
cd F:/Docker/clambake
docker compose up -d --build         # Build orchestrator
docker compose --profile teams up -d # With Teams bot

# Launch agents in tmux
MSYS_NO_PATHCONV=1 docker exec -it clambake-orchestrator \
  bash /opt/clambake/launch-tmux.sh <project> [roles...]

# Windows launcher
start.cmd
```

## Environment Variables
| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for agents |
| `CLAMBAKE_DB_HOST` | Postgres host (default: `localhost`) |
| `CLAMBAKE_DB_PORT` | Postgres port (default: `5433`) |
| `CLAMBAKE_DB_NAME` | Database name (default: `docdb`) |
| `CLAMBAKE_DB_USER` | Database user (default: `postgres`) |
| `CLAMBAKE_DB_PASS` | Database password (default: `postgres`) |
| `OLLAMA_BASE_URL` | Ollama endpoint (default: `http://localhost:11434`) |
| `CLAMBAKE_EMBEDDING_MODEL` | Embedding model (default: `nomic-embed-text`) |
| `TEAMS_BOT_APP_ID` | Teams bot app ID (optional) |
| `TEAMS_BOT_APP_PASSWORD` | Teams bot password (optional) |
| `CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare tunnel token (optional) |

## Architecture Notes
Clambake is a peer-to-peer coordination system with no central orchestrator process. All Claude Code instances communicate through a shared Postgres database. The CLI (`clambake.py`) handles session lifecycle, memory storage/retrieval with semantic search (pgvector), inter-instance messaging, infrastructure monitoring, and multi-agent task dispatch with role-based tool gating. Agents run inside a Docker container with tmux, each in a loop that claims tasks matching their role, launches Claude Code with enforced tool restrictions, and captures output for pipeline handoff.
