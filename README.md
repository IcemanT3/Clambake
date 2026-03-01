# Clambake

Multi-instance Claude Code coordination and persistent memory through Postgres.

---

## What It Does

Clambake solves three problems that arise when running Claude Code on a machine with multiple projects and sessions:

1. **Fragmented memory** — Knowledge discovered in one session is lost to the next. Project history scatters across dozens of markdown files.
2. **No coordination** — One instance restarts Docker while another is mid-build. Shared resources (ports, databases, config files) cause silent conflicts.
3. **No infrastructure awareness** — No single view of what services are running, what ports are in use, or what's degraded.

Clambake uses a shared Postgres database as the coordination layer. Every Claude Code session can register itself, share knowledge, send messages, and check infrastructure status — all through a single CLI.

## Architecture

```
Claude Code Instance A          Claude Code Instance B
        |                               |
   clambake up                     clambake up
        |                               |
        +---------- Postgres -----------+
                    (docdb)
                       |
              clambake schema
              +-----------------+
              | instances       |  Who's active, what they're doing
              | messages        |  Instance-to-instance communication
              | project_memory  |  Per-project knowledge (with pgvector)
              | global_memory   |  Cross-project knowledge (with pgvector)
              | infra_state     |  Live infrastructure status
              | tasks           |  Multi-agent task dispatch
              | agent_roles     |  Agent role definitions
              | session_log     |  Audit trail
              +-----------------+
```

### Design Principles

- **No orchestrator** — All instances are peers. The database is the shared brain.
- **Postgres, not files** — Concurrent access, structured queries, semantic search, single source of truth.
- **One command in, one command out** — `clambake up` to start, `clambake down` to finish.
- **Graceful degradation** — If Postgres is down, commands print a one-line warning and exit cleanly. Nothing crashes.
- **Token efficient** — Query only what's relevant via semantic search instead of loading entire markdown files into context.

### Three-Tier Memory Model

| Tier | Storage | Friction | Loaded |
|------|---------|----------|--------|
| **Static** | CLAUDE.md, ISSUES.md per project | Zero — Claude Code reads automatically | Always |
| **Dynamic** | Clambake Postgres (project_memory, global_memory) | Low — `clambake up` auto-loads | On session start |
| **Active** | Clambake Postgres (instances, messages, tasks) | Explicit — when multi-agent work is active | On demand |

---

## Setup

### Prerequisites

- Python 3.8+
- PostgreSQL with pgvector extension (the `ankane/pgvector` Docker image works)
- `psycopg2-binary` and `requests` Python packages
- Ollama with `nomic-embed-text` model (for semantic search — optional but recommended)

### Installation

```bash
# 1. Install Python dependencies
pip install psycopg2-binary requests

# 2. Ensure Postgres is running
docker start postgres

# 3. Initialize the schema
python F:/Docker/clambake/clambake.py init

# 4. Verify
python F:/Docker/clambake/clambake.py project-list
```

### Configuration

All configuration is via environment variables with sensible defaults:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLAMBAKE_DB_HOST` | `localhost` | Postgres host |
| `CLAMBAKE_DB_PORT` | `5433` | Postgres port |
| `CLAMBAKE_DB_NAME` | `docdb` | Database name |
| `CLAMBAKE_DB_USER` | `postgres` | Database user |
| `CLAMBAKE_DB_PASS` | `postgres` | Database password |
| `CLAMBAKE_ENABLED` | `1` | Master switch (`0` to disable) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint for embeddings |
| `CLAMBAKE_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model name |

The flag file `~/.clambake_enabled` overrides the `CLAMBAKE_ENABLED` env var if present. Use `clambake enable` / `clambake disable` to toggle it.

### Docker Container

Clambake also runs inside a Docker container (`clambake-orchestrator`) for multi-agent workflows. The host directory is bind-mounted at `/opt/clambake`, so file changes on the host are immediately visible inside the container without rebuilding.

```yaml
# docker-compose.yml (key sections)
services:
  orchestrator:
    build: .
    container_name: clambake-orchestrator
    environment:
      CLAMBAKE_DB_HOST: host.docker.internal
      CLAMBAKE_DB_PORT: "5433"
      OLLAMA_BASE_URL: http://host.docker.internal:11434
    volumes:
      - "F:/Docker/clambake:/opt/clambake"   # Live code mount
      - "F:/Claude App/Mind Meld:/workspace"  # Agent workspace
```

---

## Usage

### Session Lifecycle

Every Claude Code session follows this pattern:

```bash
# Start of session — one command does everything:
# registers instance, checks inbox, loads project + global memories, shows infra warnings
clambake up

# ... do your work ...

# End of session — deregisters and logs shutdown
clambake down
```

`clambake up` auto-detects the project name from the current working directory. For example, running from `F:/Docker/doc-db-v2/` automatically sets the project to `doc-db-v2`. You can override this with `--project <name>`.

Output from `clambake up` looks like:

```
=== CLAMBAKE UP ===
  Instance: dd4e9adf-09e | Project: doc-db-v2

--- Active Instances ---
  [active] stalwart-mail — idle (a1b2c3d4-e5f)

--- Inbox (2 unread) ---
  [warning] stalwart-mail — Restarting Stalwart for cert update

--- [!] Infrastructure Warnings ---
  [DEGRADED] ollama — Model loading, embeddings unavailable for ~2min

--- Project Memories (doc-db-v2) ---
  #4 [gotcha] [0.68] JMAP Email/query rejects null filter
  #2 [fix] [0.65] Stalwart account ID changes across installs

--- Global Knowledge ---
  #15 [infrastructure] Port registry
  #20 [infrastructure] Docker Desktop rebuild (2026-02-21)

=== READY ===
```

### Storing Knowledge

When you discover something worth remembering across sessions:

```bash
# Project-specific knowledge
clambake remember --project doc-db-v2 \
  --type gotcha \
  --title "JMAP Message-IDs lack angle brackets" \
  --content "Stalwart JMAP returns Message-IDs without <> brackets. Python email parser includes them. Strip before comparing." \
  --tags "jmap,stalwart"

# Cross-project knowledge
clambake remember --global \
  --type infrastructure \
  --title "Port 8002 is Paperless-ngx" \
  --content "Paperless-ngx webserver on port 8002. Has its own internal Postgres, does not share swarm-postgres."
```

**Project memory types**: architecture, feature, issue, fix, decision, pattern, gotcha, update

**Global memory types**: infrastructure, convention, tool, preference, credential, lesson

Every entry is automatically embedded with nomic-embed-text (768-dim) for semantic search. If Ollama is down, the entry is stored as text-only and can be backfilled later with `clambake embed-backfill`.

### Querying Knowledge

```bash
# Semantic search (uses pgvector cosine similarity)
clambake recall --project doc-db-v2 --search "email import"
clambake recall --global --search "which port is paperless"

# Filter by type
clambake recall --project doc-db-v2 --type gotcha

# Text-only search (skip embeddings)
clambake recall --global --search "port" --text-only

# All project memories
clambake recall --project doc-db-v2 --limit 50
```

### Infrastructure Monitoring

Report and check live service status:

```bash
# Report a service status
clambake infra-warn --service postgres --status up --port 5433 --container postgres \
  --message "pgvector enabled, docdb+mindmeld databases"

clambake infra-warn --service ollama --status degraded --port 11434 \
  --message "Model loading, embeddings unavailable for ~2min"

# View current status
clambake infra
```

Infra entries auto-expire after 4 hours. Statuses: `up`, `down`, `degraded`, `warning`.

### Coordinating with Other Instances

```bash
# See who's active
clambake status

# Send a warning before risky operations
clambake send --to @all --type warning --subject "Restarting Docker" --body "Rebuilding doc-db-v2 container"

# Send completion notice
clambake send --to @all --type done --subject "Docker rebuild complete"

# Check your inbox
clambake inbox

# Read a specific message
clambake read 42
```

**Message types**: info, warning, blocker, request, done

Messages expire after 24 hours. Target can be an instance ID, a project name, or `@all`.

### Updating and Managing Memory

```bash
# Update content or status of an existing memory
clambake update-memory 15 --status resolved
clambake update-memory 15 --content "Updated: now using pypff instead of readpst"
clambake update-memory 15 --global --title "New title"

# List all projects with memory counts
clambake project-list

# Generate embeddings for entries that don't have them
clambake embed-backfill

# Activity summary
clambake digest --hours 24
```

---

## Multi-Agent Orchestration

Clambake includes a task dispatch system for running multiple Claude Code agents in parallel, each with a defined role and **enforced tool restrictions**.

### Agent Roles (Tool-Gated)

Eight roles with enforced tool-gating via Claude Code's `--allowedTools` flag. Agents physically cannot use tools outside their allowlist — this is enforced at spawn time, not just advisory.

| Role | Read | Glob | Grep | Edit | Write | Bash | Purpose |
|------|:----:|:----:|:----:|:----:|:-----:|:----:|---------|
| **scout** | Y | Y | Y | - | - | git only | Read-only codebase recon |
| **planner** | Y | Y | Y | - | - | git only | Architecture specs, task dispatch |
| **plan-reviewer** | Y | Y | Y | - | - | git only | Critiques plans for feasibility |
| **coder** | Y | Y | Y | Y | Y | full | Implements code per spec |
| **qa** | Y | Y | Y | Y | Y | full | Writes tests, reports bugs |
| **reviewer** | Y | Y | Y | - | - | full | Code review, can run tests |
| **documenter** | Y | Y | Y | Y | Y | - | Documentation only, no shell |
| **red-team** | Y | Y | Y | - | - | full | Security testing, files fix tasks |

```bash
# Seed all 8 roles with tool-gating
clambake role-seed

# View roles and their tool restrictions
clambake role-list
clambake role-get scout

# Check what tools a role gets (used internally by agent-worker.sh)
clambake role-get-tools scout --format allowlist
# Output: Read Glob Grep Bash(git:*)

# Create a custom role
clambake role-create --name devops --description "Infrastructure automation" \
  --prompt "You manage Docker, CI/CD, and deployment..." \
  --capabilities "docker,ci,deploy" \
  --tool-allowlist "Read,Glob,Grep,Bash"
```

### Task Dispatch

```bash
# Create a task assigned to a role
clambake task-create --project mindmeld --title "Implement embedding pipeline" \
  --role coder --description "Build the chunking and embedding logic per spec..." \
  --file-scope "ingest/embedder.py,ingest/chunker.py"

# List tasks
clambake task-list --project mindmeld
clambake task-list --available --role coder

# Claim and complete tasks (agents do this automatically)
clambake task-claim 5
clambake task-done 5 --result "Built embedder with batch processing"
clambake task-fail 5 --result "Ollama not reachable"

# Get a single task field (used internally by agent-worker.sh)
clambake task-get-meta 5 --field pipeline_run_id
```

Tasks support dependencies (`--depends-on 1,2,3`), priority levels, file scope isolation, and pipeline tracking. Stale claims (no heartbeat for 5 min) are automatically released back to `pending`.

### Pipeline Templates

Pipelines chain agents together in a predefined sequence. Each step's output flows to the next step as `$PREV_RESULT`.

Five default templates:

| Pipeline | Steps | Use Case |
|----------|-------|----------|
| `plan-build-review` | planner -> coder -> reviewer | Standard dev cycle |
| `full-pipeline` | scout -> planner -> coder -> qa -> reviewer | Complete cycle with recon |
| `plan-review-plan` | planner -> plan-reviewer -> planner | Iterative planning |
| `build-test` | coder -> qa | Quick build and test |
| `security-audit` | scout -> red-team -> documenter | Security focused |

```bash
# Seed the 5 default pipeline templates
clambake pipeline-seed

# List available pipelines
clambake pipeline-list

# Run a pipeline — creates chained tasks automatically
clambake pipeline-run --pipeline plan-build-review --project mindmeld \
  --input "Add JWT authentication to the API"

# Output:
# PIPELINE RUN: pipe-a1b2c3d4
#   Template: plan-build-review (Standard dev cycle)
#   Project: mindmeld
#   Tasks created: 3
#     Step 1: #10 [planner] Plan: Add JWT authentication (ready)
#     Step 2: #11 [coder] Build: Add JWT authentication (depends on #10)
#     Step 3: #12 [reviewer] Review: Add JWT authentication (depends on #11)

# Check pipeline progress
clambake pipeline-status                      # all recent runs
clambake pipeline-status pipe-a1b2c3d4        # specific run

# Get previous step's result (used internally by agent-worker.sh)
clambake pipeline-prev-result 11
```

**How it works:**
1. `pipeline-run` creates tasks with `depends_on` chaining — step 2 depends on step 1, etc.
2. The `available_tasks` view ensures only unblocked tasks are claimable.
3. When an agent claims a pipeline task (step > 1), `agent-worker.sh` resolves `$PREV_RESULT` by fetching the previous step's result text.
4. Each agent's output is captured and stored as the task result, enabling handoff to the next step.

### Running Agents

The `launch-tmux.sh` script creates a tmux session inside the Docker container with one window per agent:

```bash
# From host (Git Bash):
MSYS_NO_PATHCONV=1 docker exec -it clambake-orchestrator \
  bash /opt/clambake/launch-tmux.sh mindmeld planner

# Core 4 roles (default if no roles specified):
MSYS_NO_PATHCONV=1 docker exec -it clambake-orchestrator \
  bash /opt/clambake/launch-tmux.sh mindmeld

# Full pipeline (5 roles):
MSYS_NO_PATHCONV=1 docker exec -it clambake-orchestrator \
  bash /opt/clambake/launch-tmux.sh mindmeld scout planner coder qa reviewer

# Security audit (3 roles):
MSYS_NO_PATHCONV=1 docker exec -it clambake-orchestrator \
  bash /opt/clambake/launch-tmux.sh mindmeld scout red-team documenter
```

Or use the Windows menu launcher (`start.cmd`):

```
1 = Planner only
2 = 3 Coders
3 = All core agents (planner + coder + qa + reviewer)
4 = Reattach to existing tmux session
5 = Full pipeline (scout + planner + coder + qa + reviewer)
6 = Security audit (scout + red-team + documenter)
```

Each agent window runs `agent-worker.sh`, which loops: check for available tasks matching its role, claim one, build `--allowedTools` flags from the role's tool_allowlist, launch Claude Code with the task spec, capture output for pipeline handoff, mark done/failed, repeat.

**Safety settings**: MAX_TURNS=50 (prevents runaway agents), Claude runs with `--permission-mode bypassPermissions` inside the container, tool-gating enforced per role.

---

## CLI Reference

### Session Commands

| Command | Purpose |
|---------|---------|
| `up [--project X] [--dir Y]` | One-command startup (register + inbox + recall + infra) |
| `down [--summary "..."]` | One-command shutdown (deregister + log) |
| `register --project X [--dir Y]` | Register instance (legacy, use `up` instead) |
| `deregister` | Unregister instance (legacy, use `down` instead) |
| `heartbeat [--task "..."] [--status S]` | Update heartbeat and current task |

### Information Commands

| Command | Purpose |
|---------|---------|
| `status` | Active instances, recent messages, recent activity |
| `infra` | Live infrastructure status |
| `project-list` | All projects with memory counts |
| `digest [--hours N]` | Activity summary for last N hours |

### Memory Commands

| Command | Purpose |
|---------|---------|
| `remember --project X --type T --title "..." --content "..."` | Store project knowledge |
| `remember --global --type T --title "..." --content "..."` | Store global knowledge |
| `recall --project X [--search Q] [--type T] [--limit N]` | Query project memory |
| `recall --global [--search Q] [--type T] [--limit N]` | Query global memory |
| `update-memory ID [--content "..."] [--status S] [--title "..."]` | Update existing entry |
| `embed-backfill` | Generate embeddings for entries missing them |

### Communication Commands

| Command | Purpose |
|---------|---------|
| `send --to X --subject "..." [--body "..."] [--type T]` | Send a message |
| `inbox [--all]` | Check unread messages (--all includes read) |
| `read ID` | Read and mark a message |
| `infra-warn --service X --status S [--message "..."]` | Report service status |

### Task Commands

| Command | Purpose |
|---------|---------|
| `task-create --project X --title "..." [--role R] [--description "..."]` | Create task |
| `task-list [--project X] [--status S] [--role R] [--available]` | List tasks |
| `task-claim ID` | Claim a pending task |
| `task-done ID [--result "..."]` | Mark task completed |
| `task-fail ID [--result "..."]` | Mark task failed |

### Role Commands

| Command | Purpose |
|---------|---------|
| `role-seed` | Create/update 8 default roles with tool-gating |
| `role-list` | List all roles with tool restrictions |
| `role-get NAME` | Show role details, tools, + system prompt |
| `role-get-tools NAME [--format F]` | Get tool allowlist (formats: `allowlist`, `json`, `lines`) |
| `role-create --name X --description "..." --prompt "..." [--tool-allowlist T] [--tool-denylist T]` | Create/update role |

### Pipeline Commands

| Command | Purpose |
|---------|---------|
| `pipeline-seed` | Create/update 5 default pipeline templates |
| `pipeline-list` | List all templates with step chains |
| `pipeline-run --pipeline P --project X --input "..."` | Create chained tasks from template |
| `pipeline-status [RUN_ID]` | Show pipeline progress (all or specific) |
| `pipeline-prev-result TASK_ID` | Get previous step's result (internal) |
| `task-get-meta TASK_ID --field F` | Get single task field (internal) |

### Admin Commands

| Command | Purpose |
|---------|---------|
| `init` | Initialize schema in Postgres |
| `enable` / `on` | Enable Clambake (persists via flag file) |
| `disable` / `off` | Disable (all commands become silent no-ops) |
| `cleanup` | Remove stale instances, expired messages, old logs |

---

## Database Schema

All tables live in the `clambake` schema within the `docdb` database.

| Table | Rows (typical) | Purpose |
|-------|----------------|---------|
| `instances` | 0-5 | Active Claude Code sessions |
| `messages` | 10-50 | Inter-instance messages (expire after 24h) |
| `project_memory` | 50-500 | Per-project knowledge with pgvector embeddings |
| `global_memory` | 20-50 | Cross-project knowledge with pgvector embeddings |
| `infra_state` | 5-10 | Live service status (expire after 4h) |
| `tasks` | 10-100 | Multi-agent task dispatch (with pipeline tracking) |
| `agent_roles` | 8 | Role definitions with system prompts + tool allowlists |
| `pipeline_templates` | 5 | Reusable multi-agent workflow definitions |
| `session_log` | 100+ | Audit trail (cleaned after 90 days) |
| `conversation_references` | 0-5 | Teams Bot proactive messaging |
| `notification_log` | 0-50 | Prevents duplicate Teams notifications |

Key views: `active_instances`, `unread_messages`, `recent_activity`, `available_tasks`, `current_infra`, `pipeline_runs`.

Cleanup function (`clambake.cleanup()`) runs automatically on registration and removes: instances with no heartbeat for 2 hours, expired messages, logs older than 90 days, expired infra entries, and releases tasks claimed by dead instances.

---

## File Structure

```
F:/Docker/clambake/
  clambake.py          # CLI — all commands
  schema.sql           # Database DDL
  migrate_markdown.py  # Migration from markdown MEMORY.md files to Postgres
  agent-worker.sh      # Agent loop: claim task -> run Claude -> mark done
  launch-tmux.sh       # Creates tmux session with dashboard + agent windows
  launch-agents.sh     # Alternative launcher
  start.cmd            # Windows menu launcher
  docker-compose.yml   # Orchestrator container + Teams bot
  Dockerfile           # Ubuntu 24.04 + Claude Code + tmux
  .env                 # API keys (not committed)
  CLAUDE.md            # In-session quick reference for Claude Code
  PROTOCOL.md          # Full coordination protocol
  ISSUES.md            # Known issues and fixes
  teams-bot/           # Teams Bot for notifications (optional)
```

## License

MIT
