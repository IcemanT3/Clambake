# Clambake Protocol

## Overview

Clambake coordinates multiple Claude Code instances working on different projects through a shared Postgres database. Each instance registers itself, checks for messages from other instances, and stores knowledge that persists across sessions.

**No instance is the "orchestrator."** All instances are peers. The database is the shared brain.

## Instance Lifecycle

### 1. Session Start

```bash
# Register with Clambake (do this FIRST, before any work)
python F:/Docker/clambake/clambake.py register --project <project-name> --dir <working-dir> --model <model>

# Check inbox for messages from other instances
python F:/Docker/clambake/clambake.py inbox

# Load project context from memory
python F:/Docker/clambake/clambake.py recall --project <project-name>

# Load global knowledge (infrastructure, conventions)
python F:/Docker/clambake/clambake.py recall --global
```

This tells you:
- Who else is working and on what
- Any warnings or blockers from other instances
- What was previously built, what issues were found, what decisions were made

### 2. During Work

**Heartbeat** — Update your status periodically (especially when starting big tasks):
```bash
python F:/Docker/clambake/clambake.py heartbeat --task "Rebuilding email import pipeline" --status busy
```

**Before risky operations** — Warn other instances:
```bash
# Before Docker operations
python F:/Docker/clambake/clambake.py send --to @all --type warning \
    --subject "Restarting Docker containers" \
    --body "Taking down doc-db-v2 containers for rebuild. ETA 5 min."

# Before modifying shared files (docker-compose, .env, traefik config)
python F:/Docker/clambake/clambake.py send --to @all --type blocker \
    --subject "Editing docker-compose.yml for doc-db-v2" \
    --body "Adding new service. Will rebuild after."
```

**When you discover something** — Store it in memory:
```bash
# An issue you hit
python F:/Docker/clambake/clambake.py remember --project doc-db-v2 --type issue \
    --title "JMAP null filter crashes query" \
    --content "Stalwart returns 400 if filter:null is sent. Omit the key entirely."

# A fix for that issue
python F:/Docker/clambake/clambake.py remember --project doc-db-v2 --type fix \
    --title "JMAP null filter fix" \
    --content "Use 'if filter: params[\"filter\"] = filter' instead of always including it."

# An architecture decision
python F:/Docker/clambake/clambake.py remember --project doc-db-v2 --type decision \
    --title "Chose pypff over readpst" \
    --content "readpst is non-deterministic on large PSTs. pypff gives consistent results." \
    --tags "email,pst"

# A gotcha for future sessions
python F:/Docker/clambake/clambake.py remember --global --type infrastructure \
    --title "Ollama must be running for embeddings" \
    --content "Start with 'ollama serve' if not running. Models on F drive."
```

**Log significant actions:**
```bash
python F:/Docker/clambake/clambake.py log --action task_completed \
    --summary "Built email import pipeline with PST extraction" \
    --files "backend/email_import.py,backend/email_client.py"
```

### 3. Session End

```bash
# Log shutdown
python F:/Docker/clambake/clambake.py deregister
```

## Message Types

| Type | When to Use |
|------|-------------|
| `info` | General status updates, FYI |
| `warning` | About to do something that might affect others |
| `blocker` | Actively blocking — do NOT touch this resource |
| `request` | Need something from another instance |
| `done` | Finished a task others were waiting on |

## Memory Types

### Project Memory (`--project <name>`)

| Type | What to Store |
|------|---------------|
| `architecture` | Tech stack, design patterns, how components connect |
| `feature` | What was built, with enough detail for a new session to understand |
| `issue` | Bugs, errors, unexpected behavior encountered |
| `fix` | Solutions to issues (link to the issue by title) |
| `decision` | Why X was chosen over Y, with rationale |
| `pattern` | Recurring patterns, conventions, coding standards |
| `gotcha` | Non-obvious traps that waste time if you don't know about them |
| `update` | Changelog entries — what changed and when |

### Global Memory (`--global`)

| Type | What to Store |
|------|---------------|
| `infrastructure` | Docker setup, ports, networking, services |
| `convention` | Cross-project coding standards |
| `tool` | Ollama, Traefik, Git Bash quirks, etc. |
| `preference` | User workflow preferences |
| `credential` | Connection strings, endpoints (non-secret) |
| `lesson` | Cross-project learnings |

## Addressing Messages

- **`@all`** — Every active instance sees it
- **`<project-name>`** — Any instance working on that project (e.g. `doc-db-v2`)
- **`<instance-id>`** — Specific instance (from `clambake status`)

## Conflict Prevention Rules

1. **Before Docker rebuild/restart**: Send `warning` to `@all`, wait 10 seconds
2. **Before editing shared config** (docker-compose, .env, traefik): Send `blocker` to `@all`
3. **After completing Docker operation**: Send `done` to `@all`
4. **Before git push**: Check `clambake status` for other instances on same project
5. **If you see a `blocker` message**: Do NOT touch that resource until you see `done`

## Querying Memory

```bash
# All active issues for a project
python F:/Docker/clambake/clambake.py recall --project doc-db-v2 --type issue

# Search across all memory for a keyword
python F:/Docker/clambake/clambake.py recall --project doc-db-v2 --search "JMAP"

# All infrastructure knowledge
python F:/Docker/clambake/clambake.py recall --global --type infrastructure

# Recent activity across all projects
python F:/Docker/clambake/clambake.py status
```

## Updating Memory

```bash
# Mark an issue as resolved
python F:/Docker/clambake/clambake.py update-memory 42 --status resolved

# Update content of a memory entry
python F:/Docker/clambake/clambake.py update-memory 42 --content "Updated fix: use X instead of Y"

# Deprecate outdated knowledge
python F:/Docker/clambake/clambake.py update-memory 42 --status deprecated
```
