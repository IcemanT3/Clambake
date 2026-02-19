#!/usr/bin/env python3
"""
Migrate existing markdown memory files into Clambake Postgres tables.

Sources:
- F:/Docker/doc-db-v2/ISSUES.md -> project_memory (doc-db-v2, type=issue/fix)
- F:/Claude App/Email Forensics/ISSUES.md -> project_memory (doc-db-v2, type=issue/fix)
- F:/Claude App/Email Forensics/MEMORY.md -> project_memory (doc-db-v2, type=feature/update)
- F:/Claude App/Email Forensics/CLAUDE.md -> project_memory (doc-db-v2, type=architecture)
- C:/Users/test/.claude/projects/F--/memory/MEMORY.md -> global_memory
- C:/Users/test/.claude/CLAUDE.md -> global_memory (infrastructure/preference sections)

Run once: python migrate_markdown.py
"""

import psycopg2
import sys

DB_HOST = "localhost"
DB_PORT = "5433"
DB_NAME = "docdb"
DB_USER = "postgres"
DB_PASS = "postgres"


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )


def insert_project(cur, project, memory_type, title, content, tags=None, files=None):
    cur.execute("""
        INSERT INTO clambake.project_memory
            (project, memory_type, title, content, tags, related_files, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, 'migration')
    """, (project, memory_type, title, content, tags or [], files or []))


def insert_global(cur, memory_type, title, content, tags=None):
    cur.execute("""
        INSERT INTO clambake.global_memory
            (memory_type, title, content, tags, created_by)
        VALUES (%s, %s, %s, %s, 'migration')
    """, (memory_type, title, content, tags or []))


def migrate(conn):
    cur = conn.cursor()
    count = 0

    # --- Doc DB v2 / Email Forensics Issues ---
    print("Migrating Doc DB v2 issues...")

    insert_project(cur, "doc-db-v2", "gotcha", "Docker image name for Stalwart",
        "Wrong: stalwartlabs/mail-server:latest (does not exist). "
        "Correct: stalwartlabs/stalwart:latest",
        ["stalwart", "docker"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "JMAP session endpoint redirects",
        "/.well-known/jmap returns HTTP 307 -> /jmap/session. "
        "Fix: use allow_redirects=True on requests.get()",
        ["jmap", "stalwart"],
        ["backend/email_client.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "JMAP Email/query rejects null filter",
        "Stalwart returns 400 Bad Request if 'filter': null is sent. "
        "Fix: omit the filter key entirely when no filter conditions exist.",
        ["jmap", "stalwart"],
        ["backend/email_client.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "Stalwart account ID changes across installs",
        "Fresh Stalwart install uses account ID 'd333333' for admin, but this changes. "
        "Always discover dynamically via get_account_id(), never hardcode.",
        ["stalwart", "jmap"],
        ["backend/email_client.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "JMAP header filter for Message-ID is broken",
        "Email/query with filter: {header: ['Message-ID', value]} always returns empty. "
        "Fix: use get_all_stalwart_message_ids() to bulk-preload and check in-memory.",
        ["jmap", "stalwart"],
        ["backend/email_client.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "JMAP Message-IDs lack angle brackets",
        "Stalwart JMAP returns Message-IDs without <> brackets. "
        "Python email parser includes them. Strip before comparing: msg_id.strip().strip('<>')",
        ["jmap", "stalwart", "email"],
        ["backend/email_import.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "decision", "Chose pypff over readpst for PST extraction",
        "readpst is non-deterministic on large PSTs (1,632 to 6,459 EMLs per run, misses folders). "
        "Replaced with pypff (libpff) which gives deterministic, consistent results. "
        "Installed via pff-tools and python3-pypff system packages in Dockerfile.",
        ["email", "pst"],
        ["backend/email_import.py", "Dockerfile"])
    count += 1

    insert_project(cur, "doc-db-v2", "gotcha", "Null attachment names crash ingest",
        "att.get('name', 'attachment') returns None when name key is explicitly null. "
        "Causes .lower() to crash. Fix: use att.get('name') or 'attachment'",
        ["email"],
        ["backend/email_import.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "architecture", "Docker network for Stalwart",
        "doc-db-v2 and stalwart-mail must share a Docker network for container DNS. "
        "Added stalwart-mail_default as external network in docker-compose.yml. "
        "Doc DB connects container-to-container at http://stalwart-mail:8080",
        ["docker", "stalwart", "networking"],
        ["docker-compose.yml"])
    count += 1

    # --- Email Forensics Features ---
    print("Migrating Email Forensics build history...")

    insert_project(cur, "doc-db-v2", "feature", "Email module — PST import pipeline",
        "Phase 1: Stalwart Mail Server Docker setup + PST import via pypff. "
        "Extracts emails from .pst files, imports to Stalwart via JMAP, tracks in pst_imports table. "
        "Built 2026-02-18 in zero-shot pass.",
        ["email", "pst", "stalwart"],
        ["backend/email_import.py", "backend/email_client.py", "backend/email_routes.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Email module — React three-panel UI",
        "Phase 2: Three-panel email client (folders, messages, reading pane) + import panel. "
        "Built 2026-02-18.",
        ["email", "react", "frontend"],
        ["client/src/pages/email.tsx", "client/src/lib/api.ts"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Email module — RAG integration",
        "Phase 3: Email text + attachments ingested into pgvector RAG pipeline. "
        "Emails stored as documents with doc_type='email', metadata_json has JMAP fields.",
        ["email", "rag", "pgvector"],
        ["backend/email_ingest.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "PST removal and import history",
        "Delete PST imports + emails from Stalwart + RAG documents. "
        "delete_emails() in email_client.py (batched by 50). "
        "pst_imports table tracks email_ids JSONB for deletion support. "
        "UI: import history list with confirm-to-delete flow.",
        ["email", "pst"],
        ["backend/email_client.py", "client/src/pages/email.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Email import progress meter",
        "Progress bar with percentage, filename, imported/total count, current folder, failed count. "
        "1-second poll interval during active imports.",
        ["email", "frontend"],
        ["client/src/pages/email.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "update", "Stalwart HTTPS setup",
        "Self-signed TLS cert on port 8443 for web admin login. "
        "Stalwart web admin OAuth requires HTTPS, no workaround via config. "
        "Generate cert: docker exec stalwart-mail sh -c 'openssl req -x509 ...'",
        ["stalwart", "tls"],
        [])
    count += 1

    # --- Email Forensics Architecture ---
    insert_project(cur, "doc-db-v2", "architecture", "Email module architecture",
        "Stalwart Mail Server (F:/Docker/stalwart-mail/) — JMAP email store, port 8090 HTTP / 8443 HTTPS. "
        "Backend: email_client.py (JMAP), email_import.py (PST pipeline), email_routes.py (FastAPI), email_ingest.py (RAG). "
        "Frontend: email.tsx (three-panel), api.ts (types). "
        "DB tables: pst_imports, documents (doc_type='email'). "
        "Stalwart connects container-to-container at http://stalwart-mail:8080.",
        ["email", "stalwart", "architecture"],
        ["backend/email_client.py", "backend/email_import.py", "backend/email_routes.py",
         "backend/email_ingest.py", "client/src/pages/email.tsx"])
    count += 1

    # --- Stalwart Mail Project Memory ---
    print("Migrating Stalwart Mail knowledge...")

    insert_project(cur, "stalwart-mail", "architecture", "Stalwart Mail Server setup",
        "Docker image: stalwartlabs/stalwart:latest. Named volume stalwart-data at /opt/stalwart/. "
        "Port 8090 (HTTP API), 8443 (HTTPS web admin). Admin: admin/changeme. "
        "JMAP endpoint: http://localhost:8090/jmap. "
        "Account ID discovered via /.well-known/jmap session endpoint.",
        ["docker", "jmap"])
    count += 1

    insert_project(cur, "stalwart-mail", "pattern", "Stalwart setup checklist after fresh install",
        "1. docker compose up -d from F:/Docker/stalwart-mail/\n"
        "2. Wait for startup log: 'Your administrator account is admin with password changeme'\n"
        "3. Generate TLS cert: docker exec stalwart-mail sh -c 'openssl req -x509 ...'\n"
        "4. Add cert to config.toml: [certificate.default] section\n"
        "5. Create real admin account: POST /api/principal with name=admin, roles=['admin']\n"
        "6. Restart: docker restart stalwart-mail\n"
        "7. Web admin accessible at https://localhost:8443",
        ["setup", "checklist"])
    count += 1

    # --- Global Memory ---
    print("Migrating global memory...")

    insert_global(cur, "infrastructure", "Ollama setup",
        "Installed locally (not Docker): C:/Users/test/AppData/Local/Programs/Ollama/ollama. "
        "Models stored on F drive: OLLAMA_MODELS=F:\\ollama\\models. "
        "Models: nomic-embed-text (embeddings, 768-dim), minicpm-v (OCR), qwen3:8b (classification). "
        "Must be running for embeddings — start with 'ollama serve' if not running.",
        ["ollama", "embeddings", "models"])
    count += 1

    insert_global(cur, "infrastructure", "Docker environment",
        "Docker Desktop for Windows. host.docker.internal works natively. "
        "Docker folder: F:/Docker/. Port registry: F:/Docker/ports.md. "
        "Git Bash on Windows mangles docker exec paths — use MSYS_NO_PATHCONV=1 prefix.",
        ["docker", "windows"])
    count += 1

    insert_global(cur, "infrastructure", "Postgres database (swarm-postgres)",
        "Container: swarm-postgres, image: ankane/pgvector:latest. "
        "Host port: 5433, internal: 5432. Database: docdb. User/pass: postgres/postgres. "
        "pgvector extension enabled. Named volume: swarmorchestration_postgres_data. "
        "Used by Doc DB v2 and Clambake.",
        ["postgres", "pgvector", "database"])
    count += 1

    insert_global(cur, "preference", "User workflow preferences",
        "Prefers zero-shot implementation (build all phases at once). "
        "Wants build time vs debug time tracked. "
        "Prioritizes working features over perfect code. "
        "User sets priorities — don't go off on tangents. "
        "Uses Wispr Flow (voice dictation) — phrasing is conversational.",
        ["workflow"])
    count += 1

    insert_global(cur, "convention", "Doc DB builds only in Docker",
        "No local node_modules. Verify changes via docker compose up -d --build. "
        "Frontend: React + Vite. Backend: FastAPI (Python). "
        "Traefik route: doc-db.docker.lan.",
        ["doc-db", "docker"])
    count += 1

    insert_global(cur, "tool", "Traefik reverse proxy",
        "Traefik handles routing for Docker services. "
        "doc-db.docker.lan -> Doc DB v2. mail.docker.lan -> Stalwart Mail. "
        "Config at F:/Docker/traefik/.",
        ["traefik", "networking"])
    count += 1

    conn.commit()
    print("\nMigrated %d entries into Clambake." % count)
    print("Run 'python clambake.py recall --project doc-db-v2' to verify.")


if __name__ == "__main__":
    conn = get_conn()
    try:
        # Check if schema exists
        cur = conn.cursor()
        cur.execute("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name = 'clambake'
        """)
        if not cur.fetchone():
            print("ERROR: Clambake schema not found. Run 'python clambake.py init' first.")
            sys.exit(1)

        # Check if already migrated
        cur.execute("SELECT COUNT(*) FROM clambake.project_memory WHERE created_by = 'migration'")
        existing = cur.fetchone()[0]
        if existing > 0:
            print("WARNING: Found %d existing migration entries." % existing)
            resp = input("Re-run migration? This will add duplicates. (y/N): ")
            if resp.lower() != 'y':
                print("Aborted.")
                sys.exit(0)

        migrate(conn)
    finally:
        conn.close()
