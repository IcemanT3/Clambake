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

    # --- Doc DB v2 Core Features (from code analysis) ---
    print("Migrating Doc DB v2 core features...")

    insert_project(cur, "doc-db-v2", "feature", "PDF ingestion pipeline",
        "Upload PDFs via drag-and-drop. Text extraction via PyMuPDF (fitz). "
        "OCR fallback for scanned PDFs via Ollama minicpm-v vision model. "
        "AI metadata extraction via qwen3:8b (title, doc_type, case_name, date, party). "
        "Sentence-boundary-aware chunking (1000 chars, 200 overlap). "
        "Embedding via nomic-embed-text (768-dim). SHA-256 dedup. "
        "Originals stored at /data/documents/{hash}.pdf.",
        ["pdf", "ingestion", "ocr", "embeddings"],
        ["backend/ingest.py", "backend/routes.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Hybrid search (FTS + vector + RRF)",
        "PostgreSQL full-text search (tsvector/tsquery, GIN indexes) combined with "
        "pgvector cosine similarity (HNSW index). Merged via Reciprocal Rank Fusion: "
        "score = sum(1/(60 + rank)). FTS uses AND for 1-4 words, OR for longer queries. "
        "Embedding prefix: 'search_query:' prepended for query embeddings.",
        ["search", "pgvector", "fts"],
        ["backend/search.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "RAG Q&A — standard and deep research modes",
        "Standard: query decomposition for complex questions (12+ words) into 3-6 sub-queries via Claude Haiku. "
        "Multi-angle search, SSE streaming via OpenRouter, inline [1][2] citations. Default model: claude-sonnet-4. "
        "Deep Research: 2-call pipeline (not multi-turn). Gemini Flash generates 5-8 search queries, "
        "multi-search with RRF boosting, top 5 docs read in full (30K chars each), 120K context budget, "
        "single synthesis call. 90% cost reduction vs traditional agentic RAG.",
        ["rag", "llm", "search", "openrouter"],
        ["backend/ask.py", "client/src/pages/ask.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Document library with inline editing",
        "Table view grouped by case name. Filter by doc_type and text search. "
        "Inline metadata editing (title, type, case, party, date). "
        "Bulk selection and delete. OCR confidence indicators. "
        "View PDF, View Text modal, Download. Upload from library page. "
        "Emails hidden by default unless filtering for 'email' type.",
        ["library", "frontend"],
        ["client/src/pages/library.tsx", "backend/routes.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Case brief generation",
        "Synthesizes all documents into structured prose brief. Sections: Case Overview, Parties, "
        "Factual Background, Claims, Pending Motions, Key Dates, Current Status. "
        "Uses first 6000 chars of each doc. SSE streaming with source sidebar. "
        "Cached in case_brief_cache table. Model selector (any OpenRouter model).",
        ["brief", "llm", "openrouter"],
        ["backend/brief.py", "client/src/pages/brief.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Court docket sync (Seminole + Lake County)",
        "Seminole: Playwright Firefox scraper with anti-bot stealth (randomized viewports, timezones, "
        "playwright-stealth patches, session-based PDF downloads, base64 PDF interception). "
        "Rate limited: 8 req/hr. Lake: REST API client (ShowCaseWeb/Equivant), JWT auth, 120 req/hr. "
        "Auto-detect county from case number. Tracked cases with status. "
        "New filing alerts (seen/unseen with badge counts). "
        "Background scheduler: full sync every 2hr, watchdog every 10min. "
        "Downloaded PDFs auto-ingested through full pipeline.",
        ["docket", "scraping", "playwright"],
        ["backend/docket.py", "backend/docket_lake.py", "backend/scheduler.py",
         "client/src/pages/dockets.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Per-document analysis",
        "Runs automatically after ingestion. Extracts: case_number, parties (name + role), "
        "summary, document_purpose, key_dates, relief_sought via Ollama. "
        "Stored in document_analysis table (one row per doc).",
        ["analysis", "ollama"],
        ["backend/analysis.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Inline citation system",
        "Parses [1], [2], [1, 3], [2, 5, 7] patterns in LLM responses. "
        "Renders as superscript clickable badges. Tooltip shows source title, doc_type, "
        "case_name, page range, content excerpt. Click opens document viewer.",
        ["citations", "frontend"],
        ["client/src/pages/ask.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "feature", "Document viewer with chunk highlighting",
        "Modal showing full document text. Intelligent chunk-to-document highlighting with "
        "multi-strategy matching (middle snippet, start snippet, offsets, page-based fallback). "
        "Auto-scroll to highlighted region. Email docs rendered with Calibri font.",
        ["viewer", "frontend"],
        ["client/src/components/document-viewer.tsx"])
    count += 1

    # --- Doc DB v2 Architecture Decisions ---
    print("Migrating Doc DB v2 architecture decisions...")

    insert_project(cur, "doc-db-v2", "decision", "Single container deployment",
        "Frontend and backend co-deployed. React built at Docker build time (Node 20 stage), "
        "served as static files by FastAPI (Python 3.12 stage). Simplifies deployment.",
        ["docker", "architecture"],
        ["Dockerfile", "docker-compose.yml"])
    count += 1

    insert_project(cur, "doc-db-v2", "decision", "No ORM — raw psycopg2 SQL",
        "All database operations use raw SQL with psycopg2. Connection pool (ThreadedConnectionPool, "
        "2-10 connections) with manual get/put pattern. Migrations as idempotent DDL in main.py:run_migrations().",
        ["database", "architecture"],
        ["backend/db.py", "backend/main.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "decision", "OpenRouter as LLM gateway",
        "All LLM calls go through OpenRouter API (using OpenAI client library pointed at "
        "openrouter.ai/api/v1). Supports any model. Avoids vendor lock-in.",
        ["llm", "openrouter"],
        ["backend/ask.py", "backend/brief.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "decision", "SSE for all streaming responses",
        "Server-Sent Events (not WebSocket) for ask, brief, and deep research. "
        "Format: {type: chunk|sources|thinking|error|done}. Terminator: data: [DONE]. "
        "Simpler, unidirectional, works through proxies.",
        ["streaming", "architecture"],
        ["backend/ask.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "decision", "wouter over react-router",
        "Lightweight routing (~1.3KB) sufficient for this SPA. "
        "shadcn/ui for components (copy-paste, not npm dep). "
        "TanStack React Query for data fetching/caching.",
        ["frontend", "routing"],
        ["client/src/App.tsx"])
    count += 1

    insert_project(cur, "doc-db-v2", "pattern", "Background task polling pattern",
        "Long operations (docket sync, email import/ingest) use module-level _status dicts "
        "that the frontend polls via GET endpoints. Not WebSocket push. "
        "Pattern: start_X() sets status, GET /api/X/status returns current state, frontend polls.",
        ["pattern", "async"],
        ["backend/email_routes.py", "backend/docket.py"])
    count += 1

    insert_project(cur, "doc-db-v2", "pattern", "Dedup strategy by source type",
        "PDFs: SHA-256 file hash. Emails: 'email:{message_id}' as file_hash. "
        "Docket entries: (case_id, entry_number) unique constraint. "
        "All use the documents.file_hash UNIQUE constraint for enforcement.",
        ["dedup", "pattern"],
        ["backend/ingest.py", "backend/email_ingest.py"])
    count += 1

    # --- Traefik Project Memory ---
    print("Migrating Traefik knowledge...")

    insert_project(cur, "traefik", "architecture", "Traefik reverse proxy setup",
        "CLI-based static config (no traefik.yml file). Docker-label-based dynamic config. "
        "Single entrypoint: web on port 80 (no HTTPS — local dev). "
        "Docker provider watches labels on containers in 'proxy' network. "
        "Dashboard: traefik.docker.lan (insecure, no auth).",
        ["traefik", "networking", "docker"],
        ["docker-compose.yml"])
    count += 1

    insert_project(cur, "traefik", "pattern", "Adding new services to Traefik",
        "1. Add container to 'proxy' network in its docker-compose.yml\n"
        "2. Add labels: traefik.enable=true, traefik.http.routers.NAME.rule=Host(`NAME.docker.lan`), "
        "traefik.http.services.NAME.loadbalancer.server.port=INTERNAL_PORT\n"
        "3. No Traefik restart needed — Docker provider auto-detects.\n"
        "4. CoreDNS already wildcards *.docker.lan to 127.0.0.1.",
        ["traefik", "howto"])
    count += 1

    # --- CoreDNS Project Memory ---
    print("Migrating CoreDNS knowledge...")

    insert_project(cur, "coredns", "architecture", "CoreDNS wildcard DNS",
        "Wildcard DNS for *.docker.lan -> 127.0.0.1. Uses template plugin (no zone files). "
        "Single zone: docker.lan:53, 60s TTL. Ports: 53/TCP and 53/UDP. "
        "Adding new *.docker.lan services requires zero CoreDNS changes — just add Traefik labels. "
        "Does NOT forward non-matching queries; OS must have fallback DNS.",
        ["dns", "networking"],
        ["Corefile", "docker-compose.yml"])
    count += 1

    # --- Global: Networking Stack ---
    insert_global(cur, "infrastructure", "Local networking stack (CoreDNS + Traefik)",
        "Browser -> CoreDNS (*.docker.lan -> 127.0.0.1) -> Traefik (port 80, Host header routing) -> container. "
        "Current routes: traefik.docker.lan (dashboard), doc-db-v2.docker.lan, mail.docker.lan, "
        "open-webui.docker.lan, qwen3-tts.docker.lan. "
        "Note: global CLAUDE.md says 'doc-db.docker.lan' but actual label is 'doc-db-v2.docker.lan'.",
        ["networking", "traefik", "coredns"])
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


def migrate_memory_files(conn):
    """Migrate unique content from all 7 auto-memory MEMORY.md files into Clambake Postgres.

    Content disposition (per plan):
    - C--Users-test/: Port registry, MCP config, API keys -> global_memory
    - C--Users-test--local-bin/: User profile -> global; Swarm issues -> project; Docker rebuild -> global
    - C--Users-test-OneDrive-Tridents/: User prefs -> global; wslrelay fix -> global; SSD/HDD rule -> global
    - F--/: JMAP gotchas -> already in stalwart-mail CLAUDE.md (skip); Ollama model path -> global
    - F--Jarus/: Self-contained -> project_memory (jarus)
    - F--NotebookLM/: Self-contained -> project_memory (notebooklm-connector)
    - F--Docker/: vLLM notes -> project_memory (vllm, status=deprecated)
    """
    cur = conn.cursor()
    count = 0

    # ========================================================================
    # 1. C--Users-test/ (Port registry, MCP config, API keys, Traefik, active projects)
    # ========================================================================
    print("Migrating C--Users-test/ MEMORY.md...")

    insert_global(cur, "infrastructure", "Port registry",
        "Key ports in use:\n"
        "53: CoreDNS (Docker, wildcard *.docker.lan -> 127.0.0.1)\n"
        "80: Traefik reverse proxy (Docker, routes *.docker.lan)\n"
        "3001: Open WebUI (Docker)\n"
        "3002: Paperless-AI (Docker)\n"
        "5433: PostgreSQL + pgvector (Docker, swarm-postgres)\n"
        "7860: Qwen3 TTS Gradio UI (Docker)\n"
        "8000: vLLM/Qwen3 TTS API (shared port)\n"
        "8002: Paperless-ngx (Docker)\n"
        "8080: Traefik Dashboard (Docker)\n"
        "8090: Stalwart Mail HTTP (Docker)\n"
        "8443: Stalwart Mail HTTPS (Docker)\n"
        "8501: Doc DB v2 (Docker)\n"
        "11434: Ollama (local)\n"
        "Full registry: F:/Docker/ports.md",
        ["ports", "networking"])
    count += 1

    insert_global(cur, "credential", "Claude Desktop MCP config",
        "Config file: C:/Users/test/AppData/Roaming/Claude/claude_desktop_config.json\n"
        "Configured servers: openai, filesystem, docdb",
        ["mcp", "claude-desktop"])
    count += 1

    insert_global(cur, "credential", "API key locations",
        "Anthropic API key: F:/Claude App/Swarm Orchestration/.env and F:/Docker/clambake/.env\n"
        "GitHub token: gh auth token\n"
        "OpenRouter API key: F:/Docker/doc-db-v2/.env\n"
        "OpenAI key: Claude Desktop config (MCP openai server)",
        ["api-keys"])
    count += 1

    insert_global(cur, "infrastructure", "Traefik reverse proxy setup",
        "CoreDNS (F:/Docker/coredns/) resolves all *.docker.lan to 127.0.0.1\n"
        "Traefik (F:/Docker/traefik/) on port 80 routes by hostname to containers\n"
        "Shared 'proxy' Docker network connects Traefik to services\n"
        "Services opt in via labels: traefik.enable=true + router rule + service port\n"
        "Hostnames: open-webui.docker.lan, doc-db-v2.docker.lan, qwen3-tts.docker.lan, "
        "traefik.docker.lan, mail.docker.lan\n"
        "Traefik v3.3 had Docker API issues; v3.6+ works with Docker Desktop 4.60+",
        ["traefik", "networking", "coredns"])
    count += 1

    # ========================================================================
    # 2. C--Users-test--local-bin/ (User profile, Swarm, Docker rebuild)
    # ========================================================================
    print("Migrating C--Users-test--local-bin/ MEMORY.md...")

    insert_global(cur, "preference", "User profile",
        "Non-developer user working in litigation. Has F: drive with project folders at F:\\Claude App\\. "
        "Uses OpenRouter for multi-model access. Has Docker Desktop on Windows with containers. "
        "Prefers direct, practical solutions.",
        ["user-profile"])
    count += 1

    insert_project(cur, "swarm-orchestration", "issue", "Issue #13: API key not reaching interactive Claude sessions",
        "Root cause: source /home/ubuntu/.env_claude inside nested tmux->runuser->bash-c quoting chain silently fails.\n"
        "Debug proof: debug log shows 'Could not resolve authentication method' — ANTHROPIC_API_KEY is empty.\n"
        "Why -p mode works: Single bash -c with no tmux quoting layer -> source works.\n"
        "FIX NEEDED: Patch tmux.ts to pass ANTHROPIC_API_KEY via env parameter instead of sourcing file.\n"
        "The env dict { OVERSTORY_AGENT_NAME: name } gets converted to export KEY=value — add API key there.",
        ["api-key", "tmux", "blocking"])
    count += 1

    insert_project(cur, "swarm-orchestration", "issue", "Issue #14: Missing agent definition files",
        "overstory init creates: coder, coordinator, designer, lead, merger, planner, qa. "
        "But agent-manifest.json expects: scout, builder, reviewer, supervisor, monitor. "
        "Fix: copy existing .md files (e.g., cp coder.md builder.md) — already done.",
        ["agents", "manifest"])
    count += 1

    insert_project(cur, "swarm-orchestration", "pattern", "Swarm orchestration key patterns",
        "//bin//bash in docker exec from Git Bash prevents Windows path mangling.\n"
        "Clean PATH: export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.bun/bin\n"
        "TMUX_TMPDIR=/tmp/overstory-tmux for cross-user tmux socket access.\n"
        "Coordinator/Lead agents are read-only -> spawn coders with --force-hierarchy.\n"
        "One-click scripts: start-swarm.sh, setup.sh, patch-tmux.py, patch-sling.py, dashboard.sh",
        ["tmux", "docker", "scripts"])
    count += 1

    insert_global(cur, "infrastructure", "Docker Desktop rebuild (2026-02-21)",
        "VHDX lost during C:->F: migration — all container state gone, rebuilt from scratch.\n"
        "VHDX IS ON WRONG DRIVE — Docker rebuilt on C: despite config pointing to F:.\n"
        "Post-reboot: must export/reimport WSL distro to F:\\Docker\\data\\wsl\\.\n"
        "8 containers running: postgres, coredns, traefik, stalwart-mail, doc-db-v2, open-webui, clambake, qwen3-tts.\n"
        "Postgres is Docker container (port 5433) with databases: docdb, mindmeld.\n"
        "Ollama is local Windows app (port 11434), not Docker.",
        ["docker", "vhdx", "rebuild"])
    count += 1

    # ========================================================================
    # 3. C--Users-test-OneDrive-Tridents/ (Prefs, wslrelay, SSD/HDD rule)
    # ========================================================================
    print("Migrating C--Users-test-OneDrive-Tridents/ MEMORY.md...")

    insert_global(cur, "preference", "Communication preferences",
        "Explain what you're about to do and why BEFORE doing it.\n"
        "Check in before making architectural decisions — don't just build.\n"
        "User is learning the system; treat changes as a teaching moment.\n"
        "Don't run ahead with multiple changes without confirming direction first.\n"
        "Only implement infrastructure that Claude Code can manage via CLI, MCP, API, or Playwright — no manual portal clicking.\n"
        "Cloudflare preferred over Azure portals for this reason.",
        ["communication", "workflow"])
    count += 1

    insert_global(cur, "tool", "wslrelay.exe can zombie on Windows Docker Desktop",
        "wslrelay.exe can zombie on Windows Docker Desktop — check with netstat and kill.\n"
        "Python is NOT available on the Windows host — only inside Docker containers.\n"
        "MSYS_NO_PATHCONV=1 required for docker exec paths in Git Bash.",
        ["wslrelay", "windows", "docker"])
    count += 1

    insert_global(cur, "infrastructure", "Drive performance rule: SSD vs HDD",
        "F: is mechanical HDD (WD 8TB), C: is SSD (Crucial 2TB) — explains slow transfers.\n"
        "Performance rule: disk-heavy apps (Postgres, Doc DB, Ollama) on C: SSD; "
        "API-bound workloads (Clambake agents) on F: HDD is fine.\n"
        "F: drive has ~6 TB free — use for heavy storage.\n"
        "Ollama models (51 GB) copied to F:/Ollama/models/ — need symlink setup.",
        ["ssd", "hdd", "performance", "storage"])
    count += 1

    # Mindmeld pipeline status from this file
    insert_project(cur, "mindmeld", "update", "Mindmeld pipeline status (built by Haiku agents)",
        "Tasks #1-7 DONE: Schema, .jsonl parser, classifier, embedder, RAG query, bulk ingest, QA testing.\n"
        "Task #8 IN PROGRESS: Parser JSONL bug fix (interrupted by Docker crash).\n"
        "Task #9 PENDING: Code review of all modules.\n"
        "Known issues: Embedding dim mismatch (schema says vector(1536), nomic-embed-text produces 768). "
        "Some files in /workspace/ root instead of /workspace/ingest/. "
        "QA said 'production ready' but tested synthetic data, not real transcripts.",
        ["pipeline", "agents", "status"])
    count += 1

    insert_project(cur, "mindmeld", "architecture", "Mindmeld project overview",
        "Corporate memory platform — ingests transcripts, emails, Claude sessions into Postgres.\n"
        "Database: localhost:5433, mindmeld database, mindmeld_agent/mindmeld_agent.\n"
        "Schema fully deployed, 0 data ingested.\n"
        "Project files: F:/Claude App/Mind Meld/ (schema, scripts, parsers).\n"
        "Build plan: F:/Claude App/Mind Meld/BUILD.md.",
        ["architecture", "postgres"])
    count += 1

    # ========================================================================
    # 4. F--/ (Ollama model path — JMAP gotchas skip, already in stalwart-mail)
    # ========================================================================
    print("Migrating F--/ MEMORY.md (Ollama path only, JMAP already covered)...")

    insert_global(cur, "infrastructure", "Ollama local setup and models",
        "Installed locally (not Docker): C:/Users/test/AppData/Local/Programs/Ollama/ollama\n"
        "Models stored on F drive: OLLAMA_MODELS=F:\\ollama\\models\n"
        "Models: nomic-embed-text (embeddings, 768-dim), minicpm-v (OCR), qwen3:8b (classification)\n"
        "Must be running for embeddings — start with 'ollama serve' if not running.\n"
        "Open WebUI (Docker, port 3001) is a frontend overlay for local Ollama.",
        ["ollama", "models", "embeddings"])
    count += 1

    insert_global(cur, "preference", "Build preferences",
        "Prefers zero-shot implementation when possible (build all phases at once).\n"
        "Wants build time vs debug time tracked.\n"
        "Prioritizes working features over perfect code.\n"
        "User sets priorities — don't go off on tangents.\n"
        "Update CLAUDE.md, ISSUES.md, MEMORY.md files after significant work.",
        ["workflow", "preferences"])
    count += 1

    # ========================================================================
    # 5. F--Jarus/ -> project_memory (jarus)
    # ========================================================================
    print("Migrating F--Jarus/ MEMORY.md -> jarus project...")

    insert_project(cur, "jarus", "architecture", "Jarus Integrator project overview",
        "Integration middleware connecting JARUS DXP to external SaaS for American Patriot Exchange (Apex).\n"
        "Originally planned to build custom engines from scratch; JARUS already provides 17 modules.\n"
        "Scope: configure JARUS (no-code) + build integration connectors (custom code).\n"
        "Architecture: JARUS DXP -> JARUS REST APIs/ACORD XML -> Integration Layer (custom) -> External SaaS.\n"
        "Effort: 40% JARUS config, 35% connectors, 15% sync engine, 10% testing.\n"
        "Company: American Patriot Exchange (Apex), FL homeowners only, HCI 9.99% ownership.",
        ["apex", "jarus", "insurance"],
        ["Jarus Technical Blueprint.md", "Apex Carrier Objectives.md"])
    count += 1

    insert_project(cur, "jarus", "decision", "Key Jarus decisions",
        "Maria Moller is the ultimate decision maker — her directives override all others.\n"
        "Rate changes done in JARUS Product Configurator (not Excel). Excel only for one-time migration.\n"
        "JARUS is system of record for policies. External tools get synced data.\n"
        "AAIS Homeowners template is the starting base product in JARUS.",
        ["decisions", "maria"])
    count += 1

    insert_project(cur, "jarus", "pattern", "Transcript processing workflow",
        "When new transcript added:\n"
        "1. Rename to YYYY-MM-DD - [Meeting Title] - Read.ai Transcript.txt in Transcripts/\n"
        "2. Read full transcript, filter out noise (weather, stocks, other clients)\n"
        "3. Extract decisions, modules, technical details, vendor choices, timelines\n"
        "4. Date-stamp everything — track what changed between meetings\n"
        "5. Maria's word is final — overrides Brandon, Greg, Sundar\n"
        "6. Update Apex Carrier Objectives.md\n"
        "3 transcripts processed: 2026-01-14, 2026-01-28, 2026-02-06",
        ["transcripts", "workflow"])
    count += 1

    # ========================================================================
    # 6. F--NotebookLM/ -> project_memory (notebooklm-connector)
    # ========================================================================
    print("Migrating F--NotebookLM/ MEMORY.md -> notebooklm-connector project...")

    insert_project(cur, "notebooklm-connector", "architecture", "NotebookLM Connector setup",
        "MCP CLI tool (notebooklm-mcp-cli v0.2.22 via uv) for Claude Code.\n"
        "Auth: postingcomputers@gmail.com, 115+ notebooks.\n"
        "Active notebook: Claude Code — Project Knowledge Base (16ade1ec-0ebe-4516-8350-e783082204ef).\n"
        "Windows workarounds: PYTHONIOENCODING=utf-8 for nlm CLI, close Chrome before nlm login, "
        "sessions expire ~20 min.\n"
        "Chat interface: chat.pyw (Gradio), Launch Chat.vbs (no console), "
        "default notebook: Arbitration for Employment (d1c063fe).",
        ["notebooklm", "mcp", "gradio"],
        ["chat.pyw", "Launch Chat.vbs"])
    count += 1

    insert_project(cur, "notebooklm-connector", "gotcha", "pythonw + Gradio/uvicorn crashes",
        "Must patch sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__ (all are None under pythonw). "
        "uvicorn's logging calls .isatty() on sys.__stdout__ and crashes otherwise. "
        "Also use os.startfile() not webbrowser.open() for opening browser from pythonw.",
        ["pythonw", "gradio", "uvicorn"])
    count += 1

    # ========================================================================
    # 7. F--Docker/ -> project_memory (vllm, deprecated)
    # ========================================================================
    print("Migrating F--Docker/ MEMORY.md -> vllm project (deprecated)...")

    insert_project(cur, "vllm", "issue", "vLLM Docker deployment blocked by WSL GPU init",
        "vllm/vllm-openai:latest image with pre-configured entrypoint.\n"
        "Must pass flags: --model <model> --host 0.0.0.0 --port 8000.\n"
        "BLOCKER: API server process hangs during GPU model loading.\n"
        "Logs freeze at: 'Using FLASH_ATTN attention backend'.\n"
        "Port 8000 never becomes listening despite GPU memory allocation.\n"
        "WSL Compatibility: pin_memory=False warning, possible CUDA deadlock.\n"
        "May need: --enforce-eager, disable cudagraph, or CPU mode.",
        ["vllm", "gpu", "wsl", "blocked"])
    count += 1

    conn.commit()
    print("\nMigrated %d memory-file entries into Clambake." % count)


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

        # Check existing migration counts
        cur.execute("SELECT COUNT(*) FROM clambake.project_memory WHERE created_by = 'migration'")
        proj_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM clambake.global_memory WHERE created_by = 'migration'")
        glob_count = cur.fetchone()[0]

        if proj_count > 0 or glob_count > 0:
            print("Existing migration entries: %d project, %d global" % (proj_count, glob_count))

        # Determine which migration to run
        import sys as _sys
        if len(_sys.argv) > 1 and _sys.argv[1] == "--memory-files":
            print("\n=== Migrating auto-memory MEMORY.md files ===")
            migrate_memory_files(conn)
        elif len(_sys.argv) > 1 and _sys.argv[1] == "--all":
            print("\n=== Running full migration (doc-db + memory files) ===")
            if proj_count > 0:
                resp = input("Re-run doc-db migration? This may add duplicates. (y/N): ")
                if resp.lower() == 'y':
                    migrate(conn)
            else:
                migrate(conn)
            migrate_memory_files(conn)
        else:
            if proj_count > 0:
                print("WARNING: Found %d existing doc-db entries." % proj_count)
                resp = input("Re-run migration? This will add duplicates. (y/N): ")
                if resp.lower() != 'y':
                    print("Aborted. Use --memory-files to migrate only MEMORY.md files.")
                    _sys.exit(0)
            migrate(conn)
    finally:
        conn.close()
