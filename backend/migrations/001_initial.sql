CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE users (
 id uuid PRIMARY KEY, google_subject text UNIQUE NOT NULL, name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE conversations (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_owner ON conversations(user_id, created_at DESC);
CREATE TABLE ingestion_runs (
 id uuid PRIMARY KEY, source text NOT NULL, status text NOT NULL DEFAULT 'queued',
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 counts jsonb NOT NULL DEFAULT '{}', error_code text
);
CREATE TABLE source_artifacts (
 id uuid PRIMARY KEY, source text NOT NULL, path text NOT NULL, sha256 text NOT NULL,
 fetched_at timestamptz NOT NULL DEFAULT now(), pipeline_version text NOT NULL,
 local_path text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
 UNIQUE(source, path, sha256)
);
CREATE TABLE documents (
 id uuid PRIMARY KEY, identity_key text UNIQUE NOT NULL, number text NOT NULL, title text NOT NULL,
 kind text NOT NULL, scope text NOT NULL DEFAULT 'central'
);
CREATE TABLE document_versions (
 id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id), artifact_id uuid REFERENCES source_artifacts(id),
 content_hash text NOT NULL, raw_text text NOT NULL, source_url text NOT NULL,
 effective_from date, effective_to date, temporal_status text NOT NULL DEFAULT 'unknown',
 verified_at timestamptz, metadata jsonb NOT NULL DEFAULT '{}',
 review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_id, content_hash)
);
CREATE INDEX versions_document ON document_versions(document_id);
CREATE TABLE legal_units (
 id uuid PRIMARY KEY, version_id uuid NOT NULL REFERENCES document_versions(id),
 locator text NOT NULL, ordinal integer NOT NULL, raw_text text NOT NULL,
 start_offset integer NOT NULL, end_offset integer NOT NULL
);
CREATE TABLE chunks (
 id uuid PRIMARY KEY, unit_id uuid NOT NULL REFERENCES legal_units(id), version_id uuid NOT NULL REFERENCES document_versions(id),
 ordinal integer NOT NULL, raw_text text NOT NULL, normalized_text text NOT NULL, segmented_text text NOT NULL,
 token_count integer NOT NULL CHECK (token_count BETWEEN 1 AND 512),
 embedding vector(1024) NOT NULL, embedding_revision text NOT NULL,
 search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', segmented_text)) STORED,
 UNIQUE(unit_id, ordinal)
);
CREATE INDEX chunks_fulltext ON chunks USING gin(search_vector);
CREATE INDEX chunks_vector ON chunks USING hnsw(embedding vector_ip_ops);
CREATE INDEX chunks_version ON chunks(version_id);
CREATE TABLE document_relations (
 id uuid PRIMARY KEY, from_version uuid NOT NULL REFERENCES document_versions(id),
 to_document uuid REFERENCES documents(id), relation text NOT NULL,
 locator text, evidence jsonb NOT NULL
);
CREATE TABLE corpus_snapshots (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), run_id uuid REFERENCES ingestion_runs(id),
 source_checks jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE snapshot_members (
 snapshot_id uuid NOT NULL REFERENCES corpus_snapshots(id), document_id uuid NOT NULL REFERENCES documents(id),
 version_id uuid NOT NULL REFERENCES document_versions(id), PRIMARY KEY(snapshot_id, document_id)
);
CREATE TABLE active_corpus (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), snapshot_id uuid REFERENCES corpus_snapshots(id));
INSERT INTO active_corpus(singleton) VALUES(true);
CREATE TABLE source_checkpoints (
 source text PRIMARY KEY, checked_at timestamptz, cursor jsonb NOT NULL DEFAULT '{}', status text NOT NULL
);
CREATE TABLE queries (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 request jsonb NOT NULL, status text NOT NULL DEFAULT 'queued', progress text NOT NULL DEFAULT 'Đang chờ xử lý',
 result jsonb, snapshot_id uuid REFERENCES corpus_snapshots(id), error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX queries_conversation ON queries(conversation_id, created_at);
CREATE TABLE feedback (
 query_id uuid PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 helpful boolean NOT NULL, comment text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
