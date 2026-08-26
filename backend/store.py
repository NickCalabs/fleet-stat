import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY,
  start_ts REAL, end_ts REAL, completion_start_ts REAL,
  model_group TEXT, model TEXT, api_base TEXT,
  key_hash TEXT, key_alias TEXT, harness TEXT,
  user_id TEXT, requester_ip TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
  spend REAL, call_type TEXT, status TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_start ON requests(start_ts);
CREATE INDEX IF NOT EXISTS idx_requests_harness ON requests(harness, start_ts);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS inventory (
  node TEXT NOT NULL, model_id TEXT NOT NULL, source TEXT,
  path TEXT, size_bytes INTEGER, mtime REAL,
  quant TEXT, params TEXT, updated_at REAL,
  PRIMARY KEY (node, model_id)
);
CREATE TABLE IF NOT EXISTS model_tags (
  node TEXT NOT NULL, model_id TEXT NOT NULL,
  status TEXT, notes TEXT, updated_at REAL,
  PRIMARY KEY (node, model_id)
);
"""

REQUEST_COLS = [
    "request_id", "start_ts", "end_ts", "completion_start_ts",
    "model_group", "model", "api_base",
    "key_hash", "key_alias", "harness",
    "user_id", "requester_ip",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "spend", "call_type", "status",
]


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._con.executescript(SCHEMA)

    def upsert_requests(self, rows):
        if not rows:
            return 0
        sql = "INSERT OR REPLACE INTO requests ({}) VALUES ({})".format(
            ",".join(REQUEST_COLS), ",".join("?" for _ in REQUEST_COLS)
        )
        vals = [[r.get(c) for c in REQUEST_COLS] for r in rows]
        with self._lock:
            self._con.executemany(sql, vals)
            self._con.commit()
        return len(rows)

    def get_meta(self, k, default=None):
        with self._lock:
            row = self._con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def set_meta(self, k, v):
        with self._lock:
            self._con.execute("INSERT OR REPLACE INTO meta (k,v) VALUES (?,?)", (k, str(v)))
            self._con.commit()

    def usage(self, since_ts, bucket_s, group_by="harness"):
        col = {"harness": "harness", "model": "model_group"}[group_by]
        sql = f"""
          SELECT CAST(start_ts / :b AS INTEGER) * :b AS bucket,
                 COALESCE({col}, 'other') AS grp,
                 COUNT(*) AS requests,
                 COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                 COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                 COALESCE(SUM(total_tokens), 0) AS total_tokens,
                 AVG(end_ts - start_ts) AS avg_latency_s,
                 COALESCE(SUM(spend), 0) AS spend
          FROM requests WHERE start_ts >= :since
          GROUP BY bucket, grp ORDER BY bucket
        """
        with self._lock:
            rows = self._con.execute(sql, {"b": bucket_s, "since": since_ts}).fetchall()
        return [dict(r) for r in rows]

    def totals(self, since_ts, group_by="harness"):
        col = {"harness": "harness", "model": "model_group"}[group_by]
        sql = f"""
          SELECT COALESCE({col}, 'other') AS grp,
                 COUNT(*) AS requests,
                 COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                 COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                 COALESCE(SUM(total_tokens), 0) AS total_tokens,
                 AVG(end_ts - start_ts) AS avg_latency_s,
                 AVG(CASE WHEN end_ts > start_ts AND completion_tokens > 0
                     THEN completion_tokens / (end_ts - start_ts) END) AS avg_gen_tps,
                 COALESCE(SUM(spend), 0) AS spend
          FROM requests WHERE start_ts >= :since
          GROUP BY grp ORDER BY total_tokens DESC
        """
        with self._lock:
            rows = self._con.execute(sql, {"since": since_ts}).fetchall()
        return [dict(r) for r in rows]

    def replace_inventory(self, node, source, rows):
        now = time.time()
        with self._lock:
            self._con.execute("DELETE FROM inventory WHERE node=? AND source=?",
                              (node, source))
            self._con.executemany(
                """INSERT OR REPLACE INTO inventory
                   (node, model_id, source, path, size_bytes, mtime, quant, params, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(node, r.get("id"), source, r.get("path"), r.get("size_bytes"),
                  r.get("mtime"), r.get("quant"), r.get("params"), now)
                 for r in rows if r.get("id")])
            self._con.commit()

    def get_library(self):
        with self._lock:
            inv = [dict(r) for r in self._con.execute(
                "SELECT * FROM inventory ORDER BY node, size_bytes DESC")]
            tags = {(r["node"], r["model_id"]): dict(r) for r in self._con.execute(
                "SELECT * FROM model_tags")}
        for r in inv:
            t = tags.get((r["node"], r["model_id"]), {})
            r["status"] = t.get("status") or "untested"
            r["notes"] = t.get("notes") or ""
        return inv

    def set_tag(self, node, model_id, status, notes):
        with self._lock:
            self._con.execute(
                """INSERT OR REPLACE INTO model_tags (node, model_id, status, notes, updated_at)
                   VALUES (?,?,?,?,?)""",
                (node, model_id, status, notes, time.time()))
            self._con.commit()

    def recent_requests(self, since_ts):
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM requests WHERE start_ts >= ? ORDER BY start_ts", (since_ts,)
            ).fetchall()
        return [dict(r) for r in rows]
