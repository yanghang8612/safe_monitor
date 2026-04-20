CREATE TABLE IF NOT EXISTS fingerprints (
  fingerprint    TEXT PRIMARY KEY,
  source         TEXT NOT NULL,
  first_seen_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  event_count    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_fp_first_seen ON fingerprints(first_seen_at);

CREATE TABLE IF NOT EXISTS checkpoints (
  source     TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  cursor     TEXT,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_log (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  source           TEXT NOT NULL,
  received_at      DATETIME NOT NULL,
  published_at     DATETIME,
  severity         TEXT NOT NULL,
  title            TEXT NOT NULL,
  url              TEXT,
  raw_json         TEXT NOT NULL,
  filter_decision  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_received ON event_log(received_at);
CREATE INDEX IF NOT EXISTS idx_event_source   ON event_log(source);

CREATE TABLE IF NOT EXISTS failed_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id       INTEGER NOT NULL,
  last_error     TEXT,
  retry_count    INTEGER NOT NULL DEFAULT 0,
  next_retry_at  DATETIME NOT NULL,
  FOREIGN KEY (event_id) REFERENCES event_log(id)
);

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
);
