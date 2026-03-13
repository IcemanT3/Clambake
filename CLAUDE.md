# Clambake — Memory, Infrastructure & Audit Layer

## What This Is
Clambake is a thin layer that adds persistent memory (Postgres + pgvector), infrastructure
awareness, inter-instance messaging, and session audit trails to Claude Code. It does NOT
handle orchestration — Claude Code's native Agent Teams handles spawning, communication,
and dependency resolution.

## Setup
- **CLI**: `python F:/Docker/clambake/clambake.py <command>`
- **Database**: `docdb` on `localhost:5433`, schema: `clambake`
- **Requires**: `psycopg2-binary`, `requests` (pip install)
- **Embeddings**: Ollama `nomic-embed-text` at `localhost:11434` (optional, for semantic search)
- **MCP Server**: Exposes memory tools natively to Claude Code conversations
- **Enabled by default** — degrades gracefully if Postgres is down

## Session Protocol

```bash
# Start of session (one command does it all)
clambake up

# End of session
clambake down
```

`clambake up` auto-detects the project from your working directory, registers the instance,
checks inbox, loads project + global memories, and shows infrastructure warnings.

## Agent Roles
Agent roles are defined as `.claude/commands/*.md` files, invokable as slash commands:

| Command | Role | Does |
|---------|------|------|
| `/architect` | Architect | Design, specs, architecture decisions (read-only) |
| `/backend` | Backend Engineer | Python, SQL, APIs, business logic |
| `/frontend` | Frontend Engineer | React, HTML/CSS, client-side JS |
| `/devops` | DevOps | Docker, Traefik, infrastructure |
| `/qa` | QA/Validator | Testing, code review, bug reporting |

Usage: `/architect Design the new search API for Doc DB`

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

## Doc Compliance (MANDATORY)
Every project MUST have three standard docs:

| Doc | Purpose | Format |
|-----|---------|--------|
| **CLAUDE.md** | Architecture, tech stack, Docker setup, gotchas | Prose + tables |
| **BUILD.md** | Tech stack matrix, key files, database schema | Tables + code blocks |
| **ISSUES.md** | Numbered issues with problem/fix/status | `### PROJ-N: Title` sections |

## Architecture History
Clambake was originally a full orchestration framework with task dispatch, role management,
and pipeline templates. On 2026-03-12, orchestration was removed in favor of Claude Code's
native Agent Teams. The removed code is in `backup-pre-pivot/`. See `ROADMAP.md` for the
full migration plan and future work.
