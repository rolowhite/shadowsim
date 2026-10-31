"""Local HTTP server: static windows + JSON API + one background runner thread per active run.

    python3 -m shadowcity.server [--port 8765] [--db data/shadowcity.db]

Both windows read the same SQLite database, so they stay in sync and survive refreshes and restarts.
"""
import argparse
import json
import pathlib
import re
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import runs
from .engine import access, scenario
from .llm import provider as llm
from .store import Store

WEB = runs.ROOT / "web"
SPEEDS = {"1": 1, "5": 5, "20": 20, "100": 100, "max": 0}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class Runner(threading.Thread):
    """Owns one Engine. Persists only completed rounds. On a strict LLM failure, rolls back the round and pauses."""

    def __init__(self, store, rid):
        super().__init__(daemon=True)
        self.store, self.rid = store, rid
        self.lock = threading.Lock()
        self.paused, self.steps, self.speed, self.alive = True, 0, 5, True
        row = store.get_run(rid, with_secret=True)
        self.cfg = row["config"]
        live = None
        self.provider, info = runs.provider_info(self.cfg) if row["provider"]["kind"] != "fallback" else (None, row["provider"])
        if self.provider:
            live = llm.LLMDecider(self.provider, self.cfg["llm"])
        self.live = live
        recorded = llm.decisions_from_events(store.decision_events(rid))
        self.replay = llm.ReplayDecider(recorded, then=live)
        self.eng = runs.make_engine(self.cfg, row["seed"], row["premise"], row["secret"], decider=self.replay)
        target = row["round"]
        while self.eng.w.round < target and not self.eng.finished:  # deterministic resume by replay
            self.eng.step_round()
        if target and self.eng.log.prev_hash != store.last_hash(rid):
            store.update_run(rid, status="error", flags=["resume hash mismatch: run cannot be continued faithfully"])
            self.alive = False
        self.eng.drain()
        self.calls_saved = len(self.live.calls) if self.live else 0
        self.persist(force=True)

    def command(self, cmd, speed=None):
        with self.lock:
            if cmd in ("start", "resume"):
                self.paused = False
            elif cmd == "pause":
                self.paused = True
            elif cmd == "step":
                self.paused, self.steps = True, self.steps + 1
            if speed is not None and str(speed) in SPEEDS:
                self.speed = SPEEDS[str(speed)]
        self.store.update_run(self.rid, status=self.status())

    def status(self):
        if self.eng.finished:
            return "finished"
        return "paused" if self.paused else "running"

    def run(self):
        last = 0.0
        while self.alive and not self.eng.finished:
            with self.lock:
                go = not self.paused or self.steps > 0
                if self.steps:
                    self.steps -= 1
            if not go:
                time.sleep(0.1)
                continue
            snap = self.eng.snapshot() if self.live else None
            try:
                self.eng.step_round()
            except llm.LLMFailure as e:
                self.eng.restore(snap)  # nothing from the failed round is kept or persisted
                self.paused = True
                row = self.store.get_run(self.rid)
                self.store.update_run(self.rid, status="paused_llm_error", flags=(row["flags"] or []) + [f"r{self.eng.w.round + 1}: {e}"])
                self.persist(force=True, status="paused_llm_error")
                continue
            except Exception:  # noqa: BLE001 - never leave a half-written run silently
                self.store.update_run(self.rid, status="error", flags=[traceback.format_exc()[-800:]])
                self.alive = False
                break
            now = time.time()
            if now - last > 0.5 or self.paused or self.eng.finished or self.speed and self.speed <= 5:
                self.persist()
                last = now
            if self.speed:
                time.sleep(1 / self.speed)
        self.persist(force=True)

    def persist(self, force=False, status=None):
        eng = self.eng
        self.store.append_events(self.rid, eng.drain())
        if self.live and len(self.live.calls) > self.calls_saved:
            self.store.add_llm_calls(self.rid, self.calls_saved, self.live.calls[self.calls_saved:])
            self.calls_saved = len(self.live.calls)
        self.store.put_views(self.rid, eng.w.round, {"control": access.control_view(eng), "audit": access.audit_view(eng),
                                                     "desk": access.desk_view(eng), "desk_audit": access.desk_audit(eng)})
        self.store.put_metrics(self.rid, eng.w.metrics[-50:])
        kw = {"round": eng.w.round, "status": status or self.status()}
        if eng.finished:
            s = runs.summary(eng)
            kw.update(final_label=eng.final, milestones=s["milestones"], summary=s)
        self.store.update_run(self.rid, **kw)


class App:
    def __init__(self, db):
        self.store = Store(db)
        self.runners = {}
        self.lock = threading.Lock()

    def runner(self, rid):
        with self.lock:
            r = self.runners.get(rid)
            if r is None:
                r = Runner(self.store, rid)
                self.runners[rid] = r
                if r.alive:
                    r.start()
            return r

    def create(self, body, parent=None, cfg=None):
        if cfg is None:
            cfg = runs.apply_overrides(runs.load_config(),
                                       body.get("overrides") or runs.parse_override_text(body.get("overrides_text")))
        if body.get("provider"):
            cfg["llm"]["provider"] = body["provider"]
        if body.get("model"):
            cfg["llm"]["model"] = body["model"]
        if "scenario" in body:  # the backstory form; validated here, before any run exists
            cfg["scenario"]["events"] = scenario.validate(body["scenario"] or [], cfg)
        seed = int(body.get("seed", 1))
        premise = {"text": str(body.get("premise", ""))[:2000], "visibility": body.get("premise_visibility", "operator")}
        if premise["visibility"] not in ("public", "state", "operator"):
            raise ValueError("premise visibility must be public, state or operator")
        _, info = runs.provider_info(cfg)
        rid = runs.new_run_id(seed)
        self.store.create_run(rid, seed, cfg, premise, info, secrets.token_hex(16), runs.code_version(),
                              runs.config_hash(cfg), parent=parent)
        self.store.meta("current_run", rid)
        self.runner(rid)
        return rid


class Handler(BaseHTTPRequestHandler):
    app = None

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if method == "GET" and u.path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", "/control")
                self.end_headers()
                return
            if method == "GET" and u.path in ("/control", "/desk", "/runs"):
                return self.send(200, (WEB / f"{u.path[1:]}.html").read_bytes(), TYPES[".html"])
            if method == "GET" and u.path.startswith("/static/"):
                p = (WEB / u.path[len("/static/"):]).resolve()
                if WEB not in p.parents or not p.is_file():
                    return self.send(404, {"error": "not found"})
                return self.send(200, p.read_bytes(), TYPES.get(p.suffix, "application/octet-stream"))
            if u.path.startswith("/api/"):
                return self.api(method, u.path[5:].strip("/").split("/"), q)
            self.send(404, {"error": "not found"})
        except (KeyError, ValueError, RuntimeError) as e:
            self.send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def api(self, method, parts, q):
        app, st = self.app, self.app.store
        if parts == ["config"]:
            cfg = runs.load_config()
            return self.send(200, {"config": cfg, "code_version": runs.code_version()})
        if parts == ["model"]:
            cfg = runs.load_config()
            cfg["llm"]["provider"] = q.get("provider", "auto")
            try:
                _, info = runs.provider_info(cfg)
            except RuntimeError as e:
                info = {"status": "unreachable", "label": str(e)}
            return self.send(200, info)
        if parts == ["current"]:
            if method == "POST":
                st.meta("current_run", self.body()["id"])
            return self.send(200, {"id": st.meta("current_run")})
        if parts == ["runs"]:
            if method == "POST":
                return self.send(200, {"id": app.create(self.body())})
            return self.send(200, st.list_runs())
        if parts == ["compare"]:
            return self.send(200, compare(st.list_runs()))
        rid = parts[1] if len(parts) > 1 and parts[0] == "runs" else None
        if not rid or not re.fullmatch(r"run-[A-Za-z0-9-]+", rid):
            return self.send(404, {"error": "unknown endpoint"})
        row = st.get_run(rid)
        if row is None:
            return self.send(404, {"error": "unknown run"})
        sub = parts[2:]
        meta = {k: row[k] for k in ("id", "seed", "status", "round", "provider", "premise", "code_version", "config_hash",
                                    "final_label", "flags", "parent")}
        if sub == ["control"] and method == "POST":
            b = self.body()
            if b.get("cmd") == "reset":
                old = app.runner(rid)
                old.command("pause")
                if row["status"] != "finished":
                    st.update_run(rid, status="reset")
                full = st.get_run(rid)
                new = app.create({"seed": row["seed"], "premise": row["premise"]["text"],
                                  "premise_visibility": row["premise"]["visibility"]}, parent=rid, cfg=full["config"])
                return self.send(200, {"id": new})
            r = app.runner(rid)
            r.command(b.get("cmd", ""), b.get("speed"))
            return self.send(200, {"status": r.status(), "speed": r.speed})
        if sub == ["state"]:
            view, rnd, upd = st.get_view(rid, "control")
            r = app.runners.get(rid)
            return self.send(200, {"run": meta, "view": view, "speed": r.speed if r else None, "updated": upd,
                                   "current": st.meta("current_run")})
        if sub == ["events"]:
            cats = [c for c in q.get("cat", "").split(",") if c] or None
            return self.send(200, st.events(rid, int(q.get("since", 0)), min(int(q.get("limit", 500)), 2000),
                                            include_omniscient=q.get("audit") == "1", categories=cats))
        if sub == ["audit"]:
            return self.send(200, st.get_view(rid, "audit")[0])
        if sub == ["desk"]:
            view, rnd, upd = st.get_view(rid, "desk")
            return self.send(200, {"run": meta, "view": view, "updated": upd, "current": st.meta("current_run")})
        if sub == ["desk", "audit"]:
            return self.send(200, st.get_view(rid, "desk_audit")[0])
        if sub == ["metrics"]:
            return self.send(200, st.metrics(rid))
        if sub[:1] == ["notes"]:
            if method == "GET":
                return self.send(200, st.notes(rid))
            b = self.body() if method in ("POST", "PUT") else {}
            args = (str(b.get("text", ""))[:10000], float(b.get("confidence", 0.5)),
                    [str(t)[:40] for t in b.get("tags", [])][:20], [str(x)[:60] for x in b.get("links", [])][:50])
            if method == "POST":
                return self.send(200, {"id": st.add_note(rid, *args)})
            if method == "PUT" and len(sub) == 2:
                st.update_note(rid, int(sub[1]), *args)
                return self.send(200, {"ok": True})
            if method == "DELETE" and len(sub) == 2:
                st.delete_note(rid, int(sub[1]))
                return self.send(200, {"ok": True})
        if sub == ["export"]:
            full = st.get_run(rid)
            out = {"run": full, "events": st.events(rid, 0, 10 ** 9, include_omniscient=True), "notes": st.notes(rid),
                   "metrics": st.metrics(rid), "llm_calls": st.llm_calls(rid),
                   "final_views": {n: st.get_view(rid, n)[0] for n in ("control", "audit", "desk", "desk_audit")}}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="{rid}.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send(404, {"error": "unknown endpoint"})


def compare(rows):
    out = []
    for r in rows:
        s = r.get("summary") or {}
        ind = s.get("indicators", {})
        out.append({"id": r["id"], "seed": r["seed"], "status": r["status"], "round": r["round"],
                    "provider": (r.get("provider") or {}).get("label"), "code_version": r["code_version"],
                    "config_hash": r["config_hash"], "final": r["final_label"], "milestones": r.get("milestones") or {},
                    "legitimacy": ind.get("state_legitimacy"), "fear": ind.get("fear"), "exposure": ind.get("evidence_exposure"),
                    "false_flags": s.get("false_flags"), "genuine_share": s.get("genuine_share"),
                    "eliminations": s.get("eliminations"), "premise": (r.get("premise") or {}).get("text", "")[:80]})
    return out


def main():
    ap = argparse.ArgumentParser(description="Shadow City local server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=str(runs.ROOT / "data" / "shadowcity.db"))
    a = ap.parse_args()
    pathlib.Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    Handler.app = App(a.db)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Shadow City running: Control Room http://{a.host}:{a.port}/control  |  Investigation Desk http://{a.host}:{a.port}/desk")
    srv.serve_forever()


if __name__ == "__main__":
    main()
