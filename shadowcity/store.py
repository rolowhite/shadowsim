"""SQLite persistence: the single source of truth both windows read.

Analyst notes live in their own table. Nothing in the engine reads this module.
The legacy queue and decree records live inside the engine state; this store only appends events,
and has no function that deletes events or packets.
"""
import json
import sqlite3
import threading
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created REAL, seed INTEGER, status TEXT, round INTEGER,
  config TEXT, premise TEXT, provider TEXT, secret TEXT, code_version TEXT, config_hash TEXT, final_label TEXT,
  milestones TEXT, summary TEXT, parent TEXT, flags TEXT);
CREATE TABLE IF NOT EXISTS events(run_id TEXT, seq INTEGER, id TEXT, round INTEGER, ts REAL, category TEXT, type TEXT,
  actor TEXT, target TEXT, visibility TEXT, summary TEXT, text TEXT, data TEXT, decided_by TEXT, prev_hash TEXT, hash TEXT,
  PRIMARY KEY(run_id, seq));
CREATE INDEX IF NOT EXISTS ev_round ON events(run_id, round);
CREATE TABLE IF NOT EXISTS views(run_id TEXT, name TEXT, round INTEGER, body TEXT, updated REAL, PRIMARY KEY(run_id, name));
CREATE TABLE IF NOT EXISTS metrics(run_id TEXT, round INTEGER, body TEXT, PRIMARY KEY(run_id, round));
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created REAL, updated REAL,
  text TEXT, confidence REAL, tags TEXT, links TEXT);
CREATE TABLE IF NOT EXISTS llm_calls(run_id TEXT, n INTEGER, decision_seq INTEGER, round INTEGER, agent TEXT, kind TEXT,
  model TEXT, prompt_hash TEXT, prompt TEXT, reply TEXT, latency REAL, ok INTEGER, error TEXT, attempt INTEGER,
  PRIMARY KEY(run_id, n));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
EV_COLS = ("seq", "id", "round", "ts", "category", "type", "actor", "target", "visibility", "summary", "text", "data",
           "decided_by", "prev_hash", "hash")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def _x(self, sql, args=(), many=False):
        with self.lock:
            cur = self.db.executemany(sql, args) if many else self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # runs
    def create_run(self, rid, seed, config, premise, provider, secret, code_version, config_hash, parent=None):
        self._x("INSERT INTO runs(id, created, seed, status, round, config, premise, provider, secret, code_version, config_hash, "
                "parent, flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, time.time(), seed, "created", 0, json.dumps(config), json.dumps(premise), json.dumps(provider), secret,
                 code_version, config_hash, parent, "[]"))

    def update_run(self, rid, **kw):
        if not kw:
            return
        kw = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in kw.items()}
        self._x(f"UPDATE runs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), rid))

    def get_run(self, rid, with_secret=False):
        rows = self._q("SELECT * FROM runs WHERE id=?", (rid,))
        if not rows:
            return None
        r = rows[0]
        for k in ("config", "premise", "provider", "milestones", "summary", "flags"):
            r[k] = json.loads(r[k]) if r.get(k) else None
        if not with_secret:
            r.pop("secret", None)
        return r

    def list_runs(self):
        rows = self._q("SELECT id, created, seed, status, round, provider, code_version, config_hash, final_label, milestones, "
                       "summary, premise, parent, flags FROM runs ORDER BY created DESC")
        for r in rows:
            for k in ("provider", "milestones", "summary", "premise", "flags"):
                r[k] = json.loads(r[k]) if r.get(k) else None
        return rows

    # events (append-only)
    def append_events(self, rid, events):
        if events:
            self._x(f"INSERT OR IGNORE INTO events(run_id, {', '.join(EV_COLS)}) VALUES(?{', ?' * len(EV_COLS)})",
                    [(rid, *[json.dumps(e[c]) if c in ("visibility", "data") else e[c] for c in EV_COLS]) for e in events],
                    many=True)

    def events(self, rid, since=0, limit=500, include_omniscient=False, categories=None):
        sql = "SELECT * FROM events WHERE run_id=? AND seq>?"
        args = [rid, since]
        if not include_omniscient:
            sql += " AND visibility != '\"omniscient\"'"
        if categories:
            sql += f" AND category IN ({','.join('?' * len(categories))})"
            args += list(categories)
        sql += " ORDER BY seq LIMIT ?"
        rows = self._q(sql, (*args, limit))
        for r in rows:
            r["visibility"], r["data"] = json.loads(r["visibility"]), json.loads(r["data"])
        return rows

    def last_seq(self, rid):
        r = self._q("SELECT MAX(seq) AS m FROM events WHERE run_id=?", (rid,))
        return r[0]["m"] or 0

    def last_hash(self, rid):
        r = self._q("SELECT hash FROM events WHERE run_id=? ORDER BY seq DESC LIMIT 1", (rid,))
        return r[0]["hash"] if r else None

    def decision_events(self, rid):
        rows = self._q("SELECT * FROM events WHERE run_id=? AND category='decision' ORDER BY seq", (rid,))
        for r in rows:
            r["visibility"], r["data"] = json.loads(r["visibility"]), json.loads(r["data"])
        return rows

    # views and metrics
    def put_views(self, rid, rnd, views):
        now = time.time()
        self._x("INSERT OR REPLACE INTO views(run_id, name, round, body, updated) VALUES(?,?,?,?,?)",
                [(rid, k, rnd, json.dumps(v), now) for k, v in views.items()], many=True)

    def get_view(self, rid, name):
        r = self._q("SELECT body, round, updated FROM views WHERE run_id=? AND name=?", (rid, name))
        return (json.loads(r[0]["body"]), r[0]["round"], r[0]["updated"]) if r else (None, 0, 0)

    def put_metrics(self, rid, rows):
        self._x("INSERT OR REPLACE INTO metrics(run_id, round, body) VALUES(?,?,?)",
                [(rid, m["round"], json.dumps(m)) for m in rows], many=True)

    def metrics(self, rid):
        return [json.loads(r["body"]) for r in self._q("SELECT body FROM metrics WHERE run_id=? ORDER BY round", (rid,))]

    def add_llm_calls(self, rid, start, calls):
        self._x("INSERT OR REPLACE INTO llm_calls(run_id, n, decision_seq, round, agent, kind, model, prompt_hash, prompt, reply, "
                "latency, ok, error, attempt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(rid, start + i + 1, c["decision_seq"], c["round"], c["agent"], c["kind"], c["model"], c["prompt_hash"],
                  c["prompt"], c["reply"], c["latency"], int(c["ok"]), c["error"], c["attempt"]) for i, c in enumerate(calls)],
                many=True)

    def llm_calls(self, rid):
        return self._q("SELECT * FROM llm_calls WHERE run_id=? ORDER BY n", (rid,))

    # analyst notes: never read by the engine
    def notes(self, rid):
        rows = self._q("SELECT * FROM notes WHERE run_id=? ORDER BY created DESC", (rid,))
        for r in rows:
            r["tags"], r["links"] = json.loads(r["tags"] or "[]"), json.loads(r["links"] or "[]")
        return rows

    def add_note(self, rid, text, confidence, tags, links):
        now = time.time()
        cur = self._x("INSERT INTO notes(run_id, created, updated, text, confidence, tags, links) VALUES(?,?,?,?,?,?,?)",
                      (rid, now, now, text, confidence, json.dumps(tags), json.dumps(links)))
        return cur.lastrowid

    def update_note(self, rid, nid, text, confidence, tags, links):
        self._x("UPDATE notes SET text=?, confidence=?, tags=?, links=?, updated=? WHERE id=? AND run_id=?",
                (text, confidence, json.dumps(tags), json.dumps(links), time.time(), nid, rid))

    def delete_note(self, rid, nid):
        self._x("DELETE FROM notes WHERE id=? AND run_id=?", (nid, rid))

    # meta
    def meta(self, key, value=None):
        if value is None:
            r = self._q("SELECT value FROM meta WHERE key=?", (key,))
            return r[0]["value"] if r else None
        self._x("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
