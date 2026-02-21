-- Clambake: Multi-Instance Claude Code Coordination
-- Runs inside existing `docdb` database alongside Doc DB v2 tables
-- Uses a separate `clambake` schema to avoid conflicts

CREATE SCHEMA IF NOT EXISTS clambake;

-- ============================================================
-- 1. INSTANCES — Who's alive and what they're working on
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.instances (
    id              SERIAL PRIMARY KEY,
    instance_id     TEXT UNIQUE NOT NULL,        -- UUID, generated at session start
    project         TEXT NOT NULL,               -- e.g. 'doc-db-v2', 'stalwart-mail'
    working_dir     TEXT,                        -- e.g. 'F:/Docker/doc-db-v2'
    current_task    TEXT,                        -- free-text: what they're doing right now
    model           TEXT,                        -- e.g. 'opus', 'sonnet'
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'idle', 'busy', 'shutting_down')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-expire stale instances (no heartbeat in 30 min)
CREATE INDEX IF NOT EXISTS idx_instances_heartbeat
    ON clambake.instances (last_heartbeat);

-- ============================================================
-- 2. MESSAGES — Instance-to-instance communication
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.messages (
    id              SERIAL PRIMARY KEY,
    from_instance   TEXT NOT NULL,               -- instance_id or 'system'
    from_project    TEXT,                        -- sender's project for context
    to_target       TEXT NOT NULL,               -- instance_id, project name, or '@all'
    message_type    TEXT NOT NULL DEFAULT 'info'
                    CHECK (message_type IN ('info', 'warning', 'blocker', 'request', 'done')),
    subject         TEXT NOT NULL,
    body            TEXT,
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE INDEX IF NOT EXISTS idx_messages_to_unread
    ON clambake.messages (to_target, is_read)
    WHERE NOT is_read;

CREATE INDEX IF NOT EXISTS idx_messages_created
    ON clambake.messages (created_at DESC);

-- ============================================================
-- 3. PROJECT_MEMORY — Per-project knowledge base
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.project_memory (
    id              SERIAL PRIMARY KEY,
    project         TEXT NOT NULL,               -- e.g. 'doc-db-v2'
    memory_type     TEXT NOT NULL
                    CHECK (memory_type IN (
                        'architecture',  -- tech stack, design decisions
                        'feature',       -- what was built
                        'issue',         -- bugs/problems encountered
                        'fix',           -- solutions to issues
                        'decision',      -- why we chose X over Y
                        'pattern',       -- recurring patterns/conventions
                        'gotcha',        -- non-obvious traps
                        'update'         -- changelog entries
                    )),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'resolved', 'deprecated', 'superseded')),
    tags            TEXT[] DEFAULT '{}',
    related_files   TEXT[] DEFAULT '{}',          -- file paths this knowledge relates to
    created_by      TEXT DEFAULT 'human',         -- instance_id or 'human'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Optional: pgvector embedding for semantic search (768-dim, nomic-embed-text)
    embedding       vector(768)
);

CREATE INDEX IF NOT EXISTS idx_project_memory_project_type
    ON clambake.project_memory (project, memory_type);

CREATE INDEX IF NOT EXISTS idx_project_memory_status
    ON clambake.project_memory (status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_project_memory_tags
    ON clambake.project_memory USING GIN (tags);

-- HNSW index for semantic search (only if embeddings are populated)
-- CREATE INDEX IF NOT EXISTS idx_project_memory_embedding
--     ON clambake.project_memory USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 4. GLOBAL_MEMORY — Cross-project shared knowledge
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.global_memory (
    id              SERIAL PRIMARY KEY,
    memory_type     TEXT NOT NULL
                    CHECK (memory_type IN (
                        'infrastructure', -- Docker, ports, networking
                        'convention',     -- coding standards, naming
                        'tool',           -- Ollama, Traefik, etc.
                        'preference',     -- user workflow preferences
                        'credential',     -- connection strings (non-secret)
                        'lesson'          -- cross-project learnings
                    )),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT[] DEFAULT '{}',
    created_by      TEXT DEFAULT 'human',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_global_memory_type
    ON clambake.global_memory (memory_type);

-- ============================================================
-- 5. SESSION_LOG — Audit trail of what happened
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.session_log (
    id              SERIAL PRIMARY KEY,
    instance_id     TEXT NOT NULL,
    project         TEXT NOT NULL,
    action          TEXT NOT NULL
                    CHECK (action IN (
                        'started',
                        'task_started',
                        'task_completed',
                        'issue_found',
                        'issue_resolved',
                        'docker_operation',
                        'file_modified',
                        'shutdown'
                    )),
    summary         TEXT NOT NULL,
    files_modified  TEXT[] DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_log_project
    ON clambake.session_log (project, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_session_log_instance
    ON clambake.session_log (instance_id, created_at DESC);

-- ============================================================
-- VIEWS — Convenience queries
-- ============================================================

-- Active instances (heartbeat within last 30 minutes)
CREATE OR REPLACE VIEW clambake.active_instances AS
SELECT instance_id, project, working_dir, current_task, model, status,
       started_at, last_heartbeat,
       EXTRACT(EPOCH FROM (NOW() - last_heartbeat))::int AS seconds_since_heartbeat
FROM clambake.instances
WHERE last_heartbeat > NOW() - INTERVAL '30 minutes'
ORDER BY project, started_at;

-- Unread messages per target
CREATE OR REPLACE VIEW clambake.unread_messages AS
SELECT m.*, i.project AS sender_project
FROM clambake.messages m
LEFT JOIN clambake.instances i ON i.instance_id = m.from_instance
WHERE NOT m.is_read
  AND (m.expires_at IS NULL OR m.expires_at > NOW())
ORDER BY m.created_at DESC;

-- Recent project activity (last 7 days)
CREATE OR REPLACE VIEW clambake.recent_activity AS
SELECT project,
       action,
       summary,
       files_modified,
       created_at,
       instance_id
FROM clambake.session_log
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;

-- ============================================================
-- 6. AGENT_ROLES — Specialized agent definitions (stored in DB, not CLAUDE.md)
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.agent_roles (
    id              SERIAL PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,           -- e.g. 'coder', 'qa', 'reviewer', 'planner'
    description     TEXT NOT NULL,                  -- short description of what this role does
    system_prompt   TEXT NOT NULL,                  -- the full system prompt / CLAUDE.md content
    capabilities    TEXT[] DEFAULT '{}',            -- e.g. '{write_code, run_tests, review}'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 7. TASKS — Dispatchable work items for multi-agent orchestration
-- ============================================================
CREATE TABLE IF NOT EXISTS clambake.tasks (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,                           -- detailed spec / requirements
    project         TEXT NOT NULL,                  -- e.g. 'mindmeld'
    priority        INT NOT NULL DEFAULT 0,         -- higher = more important
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'claimed', 'in_progress', 'done', 'failed')),
    assigned_role   TEXT REFERENCES clambake.agent_roles(name),  -- which role should do this
    assigned_instance TEXT,                         -- instance_id that claimed it
    file_scope      TEXT[] DEFAULT '{}',            -- files this task owns (non-overlapping)
    depends_on      INT[] DEFAULT '{}',             -- task IDs that must complete first
    result          TEXT,                           -- output/notes when done or failure reason
    created_by      TEXT NOT NULL DEFAULT 'human',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON clambake.tasks (status) WHERE status IN ('pending', 'claimed', 'in_progress');

CREATE INDEX IF NOT EXISTS idx_tasks_project
    ON clambake.tasks (project, status);

CREATE INDEX IF NOT EXISTS idx_tasks_assigned
    ON clambake.tasks (assigned_instance) WHERE assigned_instance IS NOT NULL;

-- View: available tasks (pending, dependencies met)
CREATE OR REPLACE VIEW clambake.available_tasks AS
SELECT t.*
FROM clambake.tasks t
WHERE t.status = 'pending'
  AND NOT EXISTS (
    SELECT 1 FROM unnest(t.depends_on) AS dep_id
    JOIN clambake.tasks d ON d.id = dep_id
    WHERE d.status NOT IN ('done')
  )
ORDER BY t.priority DESC, t.created_at ASC;

-- ============================================================
-- CLEANUP FUNCTION — Remove stale data
-- ============================================================
CREATE OR REPLACE FUNCTION clambake.cleanup() RETURNS void AS $$
BEGIN
    -- Mark instances with no heartbeat in 2 hours as gone
    DELETE FROM clambake.instances
    WHERE last_heartbeat < NOW() - INTERVAL '2 hours';

    -- Delete expired messages
    DELETE FROM clambake.messages
    WHERE expires_at < NOW();

    -- Delete session logs older than 90 days
    DELETE FROM clambake.session_log
    WHERE created_at < NOW() - INTERVAL '90 days';
END;
$$ LANGUAGE plpgsql;
