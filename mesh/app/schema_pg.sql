-- GeekPark Mesh · PostgreSQL schema（MESH_DB_URL 启用时）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS users(
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE,
  display TEXT,
  pw_hash TEXT,
  role TEXT NOT NULL DEFAULT 'viewer',
  team TEXT,
  feishu_open_id TEXT UNIQUE,
  avatar_url TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS issues(
  id SERIAL PRIMARY KEY,
  slug TEXT UNIQUE,
  date_start TEXT,
  date_end TEXT,
  period_label TEXT,
  version TEXT DEFAULT 'v1.4',
  status TEXT DEFAULT 'draft',
  draft_json TEXT,
  published_json TEXT,
  published_items_snapshot TEXT,
  updated_at TEXT,
  published_at TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  embedding_status TEXT DEFAULT '',
  embedding_total INTEGER DEFAULT 0,
  embedding_done INTEGER DEFAULT 0,
  embedding_model TEXT DEFAULT '',
  embedding_at TEXT,
  embedding_error TEXT
);

CREATE TABLE IF NOT EXISTS sources(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  stype TEXT,
  team TEXT,
  title TEXT,
  filename TEXT,
  raw_path TEXT,
  text TEXT,
  meta TEXT,
  fetched_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  extracted INTEGER DEFAULT 0,
  channel TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS items(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  source_id INTEGER,
  team TEXT,
  stype TEXT,
  zone INTEGER,
  level TEXT,
  kind TEXT,
  text TEXT,
  entities TEXT,
  roles TEXT,
  signals TEXT,
  source_label TEXT,
  pointer TEXT,
  blocked INTEGER DEFAULT 0,
  owner_team TEXT,
  owner_provenance TEXT,
  llm_owner_team_hint TEXT,
  channel TEXT DEFAULT 'manual',
  merged_into INTEGER,
  source_labels TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS entities(
  id SERIAL PRIMARY KEY,
  name TEXT UNIQUE,
  kind TEXT,
  roles TEXT,
  first_issue TEXT,
  listed INTEGER DEFAULT 0,
  aliases TEXT
);

CREATE TABLE IF NOT EXISTS cards(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  team TEXT,
  card_json TEXT,
  status TEXT DEFAULT 'approved',
  reviewer TEXT,
  reviewed_at TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS edits(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  "user" TEXT,
  target TEXT,
  before TEXT,
  after TEXT,
  at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS mail_log(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  to_addr TEXT,
  subject TEXT,
  ok INTEGER,
  error TEXT,
  at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS versions(
  id SERIAL PRIMARY KEY,
  issue_id INTEGER,
  version TEXT,
  at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  by_user TEXT,
  cards_out INTEGER,
  edited_count INTEGER,
  url TEXT,
  snapshot_json TEXT
);

CREATE TABLE IF NOT EXISTS search_fts(
  id SERIAL PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  section TEXT,
  title TEXT,
  body TEXT,
  date_end TEXT,
  toks TEXT
);
CREATE INDEX IF NOT EXISTS idx_search_fts_slug ON search_fts(issue_slug);
CREATE INDEX IF NOT EXISTS idx_search_fts_toks_trgm ON search_fts USING gin (toks gin_trgm_ops);

CREATE TABLE IF NOT EXISTS entity_team_facts(
  id SERIAL PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  date_start TEXT,
  date_end TEXT,
  section TEXT NOT NULL,
  name TEXT NOT NULL,
  team TEXT NOT NULL,
  kind TEXT DEFAULT 'unknown',
  group_title TEXT,
  snippet TEXT,
  source_hint TEXT
);
CREATE INDEX IF NOT EXISTS idx_etf_team_name ON entity_team_facts(team, name);
CREATE INDEX IF NOT EXISTS idx_etf_name_team ON entity_team_facts(name, team);
CREATE INDEX IF NOT EXISTS idx_etf_date_team ON entity_team_facts(date_end, team);
CREATE INDEX IF NOT EXISTS idx_etf_section_team ON entity_team_facts(section, team);
CREATE INDEX IF NOT EXISTS idx_etf_slug ON entity_team_facts(issue_slug);

CREATE TABLE IF NOT EXISTS item_facts(
  id SERIAL PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_start TEXT,
  date_end TEXT,
  item_id INTEGER,
  source_id INTEGER,
  owner_team TEXT NOT NULL,
  stype TEXT,
  zone INTEGER,
  level TEXT,
  kind TEXT,
  primary_name TEXT,
  entities_json TEXT,
  roles_json TEXT,
  signals_json TEXT,
  text_snippet TEXT,
  source_label TEXT,
  channel TEXT DEFAULT 'manual',
  toks TEXT,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_if_slug ON item_facts(issue_slug);
CREATE INDEX IF NOT EXISTS idx_if_team ON item_facts(owner_team);
CREATE INDEX IF NOT EXISTS idx_if_name ON item_facts(primary_name);
CREATE INDEX IF NOT EXISTS idx_if_date ON item_facts(date_end);
CREATE INDEX IF NOT EXISTS idx_if_stype ON item_facts(stype);
CREATE INDEX IF NOT EXISTS idx_if_item ON item_facts(item_id);

CREATE TABLE IF NOT EXISTS item_facts_fts(
  id SERIAL PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  date_end TEXT,
  item_id INTEGER,
  source_id INTEGER,
  owner_team TEXT,
  stype TEXT,
  primary_name TEXT,
  text_snippet TEXT,
  source_label TEXT,
  level TEXT,
  kind TEXT,
  zone INTEGER,
  toks TEXT
);
CREATE INDEX IF NOT EXISTS idx_iff_toks_trgm ON item_facts_fts USING gin (toks gin_trgm_ops);

CREATE TABLE IF NOT EXISTS item_entity_facts(
  id SERIAL PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_start TEXT,
  date_end TEXT,
  item_id INTEGER NOT NULL,
  source_id INTEGER,
  owner_team TEXT NOT NULL,
  stype TEXT,
  entity_name TEXT NOT NULL,
  entity_kind TEXT DEFAULT 'unknown',
  text_snippet TEXT,
  source_label TEXT,
  channel TEXT DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_ief_slug ON item_entity_facts(issue_slug);
CREATE INDEX IF NOT EXISTS idx_ief_name ON item_entity_facts(entity_name);
CREATE INDEX IF NOT EXISTS idx_ief_team_name ON item_entity_facts(owner_team, entity_name);
CREATE INDEX IF NOT EXISTS idx_ief_date ON item_entity_facts(date_end);
CREATE INDEX IF NOT EXISTS idx_ief_item ON item_entity_facts(item_id);

CREATE TABLE IF NOT EXISTS feishu_chat_bindings(
  chat_id TEXT PRIMARY KEY,
  chat_type TEXT DEFAULT 'group',
  team TEXT,
  label TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sources_issue ON sources(issue_id);
CREATE INDEX IF NOT EXISTS idx_items_issue ON items(issue_id);
CREATE INDEX IF NOT EXISTS idx_items_owner ON items(issue_id, owner_team);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_cards_issue ON cards(issue_id);

CREATE TABLE IF NOT EXISTS chunk_index(
  chunk_id TEXT PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_end TEXT,
  layer TEXT NOT NULL,
  section TEXT,
  stype TEXT,
  owner_team TEXT,
  entity_name TEXT,
  title TEXT,
  body TEXT NOT NULL,
  source_label TEXT,
  item_id INTEGER,
  source_id INTEGER,
  toks TEXT,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_slug ON chunk_index(issue_slug);
CREATE INDEX IF NOT EXISTS idx_chunk_team ON chunk_index(owner_team);
CREATE INDEX IF NOT EXISTS idx_chunk_date ON chunk_index(date_end);
CREATE INDEX IF NOT EXISTS idx_chunk_layer ON chunk_index(layer);

CREATE TABLE IF NOT EXISTS chunk_embeddings(
  chunk_id TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector_json TEXT NOT NULL,
  updated_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS ask_sessions(
  id TEXT PRIMARY KEY,
  scope_key TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL,
  user_id INTEGER,
  feishu_open_id TEXT,
  chat_id TEXT,
  thread_id TEXT,
  team_scope TEXT,
  title TEXT,
  meta_json TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  updated_at TEXT,
  last_active_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_sess_user ON ask_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ask_sess_chat ON ask_sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_ask_sess_scope ON ask_sessions(scope_key);

CREATE TABLE IF NOT EXISTS ask_messages(
  id SERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  mode TEXT,
  n_context INTEGER,
  meta_json TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);
CREATE INDEX IF NOT EXISTS idx_ask_msg_sess ON ask_messages(session_id, id);

CREATE TABLE IF NOT EXISTS ask_log(
  id SERIAL PRIMARY KEY,
  session_id TEXT,
  user_id INTEGER,
  feishu_open_id TEXT,
  chat_id TEXT,
  thread_id TEXT,
  team_scope TEXT,
  query TEXT NOT NULL,
  mode TEXT,
  n_hits INTEGER,
  latency_ms INTEGER,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);
CREATE INDEX IF NOT EXISTS idx_ask_log_user ON ask_log(user_id, created_at);

CREATE TABLE IF NOT EXISTS user_ask_presets(
  id SERIAL PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'user',
  user_id INTEGER,
  target_team TEXT,
  title TEXT NOT NULL,
  question_template TEXT NOT NULL,
  schedule TEXT DEFAULT 'manual',
  schedule_time TEXT,
  schedule_dow INTEGER,
  team_scope TEXT,
  enabled INTEGER DEFAULT 1,
  created_by INTEGER,
  last_pushed_at TEXT,
  created_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_presets_user ON user_ask_presets(user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_presets_team ON user_ask_presets(target_team, enabled);

CREATE TABLE IF NOT EXISTS preset_push_log(
  id SERIAL PRIMARY KEY,
  preset_id INTEGER NOT NULL,
  user_id INTEGER,
  answer_snippet TEXT,
  ok INTEGER DEFAULT 1,
  error TEXT,
  pushed_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
);

CREATE TABLE IF NOT EXISTS mesh_jobs(
  job_kind TEXT NOT NULL,
  job_key TEXT NOT NULL,
  token INTEGER NOT NULL DEFAULT 0,
  running INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  payload_json TEXT,
  updated_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
  PRIMARY KEY (job_kind, job_key)
);

CREATE TABLE IF NOT EXISTS ask_rate_hits(
  rate_key TEXT NOT NULL,
  hit_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ask_rate_key ON ask_rate_hits(rate_key, hit_at);

CREATE TABLE IF NOT EXISTS mesh_llm_slots(
  slot_id INTEGER PRIMARY KEY,
  holder TEXT,
  taken_at DOUBLE PRECISION
);
