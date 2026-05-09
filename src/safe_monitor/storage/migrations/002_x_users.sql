CREATE TABLE IF NOT EXISTS x_users (
  user_id        TEXT PRIMARY KEY,
  handle         TEXT NOT NULL UNIQUE,
  tier           TEXT NOT NULL,                   -- 'S' | 'A' | 'B' | 'C' | 'D' | 'E'
  last_seen_id   TEXT,
  added_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_status (
  source     TEXT PRIMARY KEY,
  degraded   INTEGER NOT NULL DEFAULT 0,
  reason     TEXT,
  changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
