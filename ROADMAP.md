# Clambake Roadmap — Agent Teams Architecture

## Vision
Clambake is a thin configuration, memory, and management layer on top of Claude Code's
native Agent Teams execution engine. It does NOT reimplemented orchestration — it adds
the pieces that are still missing: persistent memory, infrastructure awareness, and
structured audit trails.

## Architecture (Target)

```
+---------------------------------------------+
|  Claude Code Agent Teams  (execution engine) |
|  - Spawning, communication, dependencies     |
|  - .claude/commands/*.md define agent roles   |
+----------------+----------------------------+
                 | hooks write to v
+----------------+----------------------------+
|  Clambake  (thin layer)                      |
|  +-------------+  +----------------------+  |
|  | Memory (MCP) |  | Infra Awareness      |  |
|  | project_mem  |  | infra_state          |  |
|  | global_mem   |  | service health       |  |
|  | pgvector     |  | port registry        |  |
|  +-------------+  +----------------------+  |
|  +------------------------------------------+|
|  | Audit Trail (session_log)                ||
|  | hooks -> Postgres, not just stdout       ||
|  +------------------------------------------+|
+----------------------------------------------+
```

## What Was Removed (2026-03-12)
The following orchestration features were removed from clambake.py because
Claude Code Agent Teams handles them natively:

- **Task dispatch** (task-create, task-list, task-claim, task-done, task-fail)
- **Role management** (role-list, role-get, role-create, role-get-tools, role-seed)
- **Pipeline templates** (pipeline-seed, pipeline-list, pipeline-run, pipeline-status)
- **Agent worker scripts** (agent-worker.sh, spawn-claude.sh, launch-agents.sh, etc.)
- **Instance roles** (boss/worker/human role field on instances)

All removed code is preserved in `backup-pre-pivot/`.

The DB schema (schema.sql) retains the tables for backward compatibility.
They can be dropped in a future migration once the pivot is validated.

## What Was Kept
- **Memory system** — project_memory, global_memory, pgvector semantic search, MCP server
- **Infrastructure awareness** — infra_state, infra-warn, current_infra view
- **Messaging** — inter-instance messages (send, inbox, read)
- **Session audit** — session_log, instance registration, heartbeats
- **Convenience commands** — up, down, checkin, status, digest, embed-backfill, project-list

## Agent Roles (.claude/commands/)
Five agent roles defined as Claude Code custom commands:

| Role | File | Responsibility |
|------|------|----------------|
| Architect | `architect.md` | Design, specs, architecture decisions. Read-only. |
| Backend | `backend.md` | Server-side implementation: Python, SQL, APIs. |
| Frontend | `frontend.md` | UI implementation: React, HTML/CSS, client-side JS. |
| DevOps | `devops.md` | Docker, Traefik, infrastructure, deployment. |
| QA | `qa.md` | Testing, code review, bug reporting. |

Usage: `/architect Design the new search API for Doc DB`

## Future Work

### Phase 1: Hook-Based Audit Trail
- Register `SubagentStop` hook that writes agent results to `clambake.session_log`
- Register `Stop` hook as a safety net for crash recovery
- Each hook entry captures: agent role, task summary, files modified, outcome
- This replaces the manual `clambake log` calls

### Phase 2: Context Loading
- Each agent role command auto-loads relevant CLAUDE.md, BUILD.md, ISSUES.md
- Clambake memory recall integrated into agent startup prompts
- The better these context docs are, the more autonomous each agent can be

### Phase 3: Checkpoint/Resume (if needed)
- Only build this if we hit concrete context-limit problems on long builds
- Borrow from LangGraph persistence pattern: save agent state to Postgres
- Resume from last checkpoint rather than restarting from scratch
- Do NOT build speculatively — wait for the concrete problem

### Phase 4: Clean Up Legacy Schema
- Drop unused tables: agent_roles, tasks, pipeline_templates
- Remove pipeline_run_id/pipeline_step columns from tasks
- Remove role column from instances
- Run as a migration script with rollback support
