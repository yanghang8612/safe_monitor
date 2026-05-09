-- advanced_search uses since_time (Unix seconds) rather than since_id, so
-- track the max created_at we've consumed per handle. Nullable; the polling
-- source defaults to (now - poll_interval) when missing to avoid pulling
-- the entire historical timeline on first cycle.
ALTER TABLE x_users ADD COLUMN last_seen_at_unix INTEGER;
