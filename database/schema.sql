PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pubmed_run_details (
    run_id INTEGER PRIMARY KEY REFERENCES search_runs(id),
    collected_count INTEGER NOT NULL,
    requested_limit INTEGER NOT NULL,
    query_translation TEXT NOT NULL,
    warnings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pubmed_fetch_failures (
    run_id INTEGER NOT NULL REFERENCES search_runs(id),
    pmid TEXT NOT NULL,
    error TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(run_id,pmid)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
    id INTEGER PRIMARY KEY,
    input_term TEXT NOT NULL,
    canonical_name TEXT,
    entity_type TEXT NOT NULL,
    mesh_id TEXT,
    pubchem_cid TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY,
    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    user_verified INTEGER NOT NULL DEFAULT 0 CHECK (user_verified IN (0, 1)),
    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    UNIQUE(term_id, normalized_alias, source)
);
CREATE TABLE IF NOT EXISTS project_terms (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE RESTRICT,
    role TEXT NOT NULL DEFAULT 'CONCEPT',
    priority INTEGER,
    PRIMARY KEY(project_id, term_id)
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    pmid TEXT NOT NULL UNIQUE,
    pmcid TEXT,
    doi TEXT,
    title TEXT,
    authors_json TEXT,
    journal TEXT,
    publication_year INTEGER,
    abstract TEXT,
    publication_types_json TEXT,
    mesh_terms_json TEXT,
    keywords_json TEXT,
    raw_payload_json TEXT,
    fetched_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    query TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    database_name TEXT NOT NULL,
    searched_at TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    app_version TEXT NOT NULL,
    history_server TEXT,
    history_key TEXT
);
CREATE TABLE IF NOT EXISTS search_run_pmids (
    search_run_id INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    pmid TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(search_run_id, pmid),
    UNIQUE(search_run_id, ordinal)
);
CREATE TABLE IF NOT EXISTS screenings (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'UNREVIEWED',
    exclusion_reason TEXT,
    notes TEXT,
    reviewed_at TEXT,
    UNIQUE(project_id, article_id)
);
CREATE TABLE IF NOT EXISTS provider_requests (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_key TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    error_code TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS export_runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    format TEXT NOT NULL,
    path TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL
);
-- Visual workspace history is separate from actual PubMed search-run history.
CREATE TABLE IF NOT EXISTS workspace_revisions (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    revision INTEGER NOT NULL,
    graph_json TEXT NOT NULL,
    query_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id, revision)
);
CREATE TABLE IF NOT EXISTS workspace_history_preferences (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    auto_prune INTEGER NOT NULL DEFAULT 0 CHECK (auto_prune IN (0, 1)),
    keep_latest INTEGER NOT NULL DEFAULT 20 CHECK (keep_latest BETWEEN 1 AND 1000)
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aliases_term ON aliases(term_id);
CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
CREATE INDEX IF NOT EXISTS idx_articles_pmcid ON articles(pmcid);
CREATE INDEX IF NOT EXISTS idx_search_runs_project_time ON search_runs(project_id, searched_at);
CREATE INDEX IF NOT EXISTS idx_run_pmids_pmid ON search_run_pmids(pmid);
CREATE INDEX IF NOT EXISTS idx_screenings_project_status ON screenings(project_id, status);
