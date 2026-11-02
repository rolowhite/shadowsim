"""LLM failure handling, provider HTTP path, persistence and restart-resume."""
import json
import pathlib
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shadowcity import runs, server
from shadowcity.engine.engine import Engine
from shadowcity.llm import provider as llm

CFG = runs.load_config()


class Stub:
    name, model = "stub", "test-3b"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, temperature, seed, max_tokens, timeout):
        return self.reply(messages) if callable(self.reply) else self.reply


def lcfg(**kw):
    c = dict(CFG["llm"], timeout_s=5)
    c.update(kw)
    return c


class Deciders(unittest.TestCase):
    def test_valid_replies_are_labeled_llm(self):
        d = llm.LLMDecider(Stub('{"option": 1, "reason": "ok"}'), lcfg())
        e = Engine(CFG, seed=3, decider=d)
        e.run(400)
        by = {k for k in e.w.counters if k.startswith("decided_by:")}
        self.assertIn("decided_by:llm:stub:test-3b", by)
        self.assertTrue(d.calls and all(c["ok"] for c in d.calls))

    def test_strict_failure_rolls_back_cleanly(self):
        d = llm.LLMDecider(Stub("I refuse to answer in JSON"), lcfg(mode="strict"))
        e = Engine(CFG, seed=3)
        e.run(25)  # fallback for a while
        e.decider = d
        for _ in range(500):
            snap, before = e.snapshot(), (e.w.round, e.log.prev_hash, len(e.log.events))
            try:
                e.step_round()
            except llm.LLMFailure:
                e.restore(snap)
                self.assertEqual((e.w.round, e.log.prev_hash, len(e.log.events)), before)
                return
        self.fail("expected a strict LLM failure")

    def test_lenient_failure_is_labeled_fallback(self):
        d = llm.LLMDecider(Stub('{"option": 99}'), lcfg(mode="lenient"))
        e = Engine(CFG, seed=3, decider=d)
        e.run(400)
        self.assertIn("decided_by:fallback_after_llm_error", e.w.counters)
        self.assertFalse(any(k.startswith("decided_by:llm:") for k in e.w.counters))

    def test_unrouted_kinds_use_rule(self):
        d = llm.LLMDecider(Stub('{"option": 1}'), lcfg(route=[]))
        e = Engine(CFG, seed=3, decider=d)
        e.run(300)
        self.assertEqual({k for k in e.w.counters if k.startswith("decided_by:")}, {"decided_by:fallback_rule"})


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"models": [{"name": "llama3.2:3b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["format"] == "json" and body["stream"] is False
        self._send({"message": {"content": '{"option": 2, "reason": "stub"}'}})


class Provider(unittest.TestCase):
    def test_ollama_http_path(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = llm.OllamaProvider(f"http://127.0.0.1:{srv.server_port}", "llama3.2:3b")
            self.assertTrue(p.health()[0])
            e = Engine(CFG, seed=4, decider=llm.LLMDecider(p, lcfg()))
            e.run(200)
            self.assertIn("decided_by:llm:ollama:llama3.2:3b", e.w.counters)
        finally:
            srv.shutdown()


class Persistence(unittest.TestCase):
    def test_runner_notes_reset_and_resume(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(pathlib.Path(d) / "t.db")
            app = server.App(db)
            rid = app.create({"seed": 5, "premise": "test", "premise_visibility": "public"})
            r = app.runner(rid)
            r.command("start", "max")
            deadline = time.time() + 20
            while app.store.get_run(rid)["round"] < 300 and time.time() < deadline:
                time.sleep(0.1)
            r.command("pause")
            deadline = time.time() + 5  # wait for the runner to finish and persist its last round
            while (app.store.last_hash(rid) != r.eng.log.prev_hash or app.store.get_run(rid)["round"] != r.eng.w.round) \
                    and time.time() < deadline:
                time.sleep(0.05)
            row = app.store.get_run(rid)
            self.assertGreaterEqual(row["round"], 300)
            nid = app.store.add_note(rid, "hypothesis", 0.4, ["t"], ["INC-0001"])
            app.store.update_note(rid, nid, "revised", 0.7, ["t"], ["INC-0001", "detective"])
            self.assertEqual(app.store.notes(rid)[0]["text"], "revised")
            self.assertEqual(app.store.last_hash(rid), r.eng.log.prev_hash)
            # restart: a new App on the same DB replays the run and must land on the same hash
            app2 = server.App(db)
            r2 = app2.runner(rid)
            self.assertTrue(r2.alive)
            self.assertEqual(r2.eng.w.round, app.store.get_run(rid)["round"])
            self.assertEqual(r2.eng.log.prev_hash, app2.store.last_hash(rid))
            self.assertEqual(app2.store.notes(rid)[0]["confidence"], 0.7)
            desk = app2.store.get_view(rid, "desk")[0]
            self.assertNotIn('"truth"', json.dumps(desk))


if __name__ == "__main__":
    unittest.main()


class CsvExport(unittest.TestCase):
    def test_three_csvs_respect_audit(self):
        import csv as _csv
        import io as _io
        with tempfile.TemporaryDirectory() as d:
            app = server.App(str(pathlib.Path(d) / "t.db"))
            rid = app.create({"seed": 3})
            r = app.runner(rid)
            r.command("start", "max")
            deadline = time.time() + 20
            while app.store.get_run(rid)["round"] < 600 and time.time() < deadline:
                time.sleep(0.1)
            r.command("pause")
            time.sleep(0.5)

            def read(kind, audit=False):
                data = server.csv_export(app.store, rid, kind, audit)
                text = data.decode("utf-8")
                self.assertTrue(text.startswith("﻿"))
                return list(_csv.reader(_io.StringIO(text[1:])))

            cb, news, tl = read("casebook"), read("news"), read("timeline")
            self.assertEqual(cb[0][:3], ["id", "round", "source"])
            self.assertGreater(len(news), 1)
            self.assertFalse(any(h.startswith("truth_audit") for h in news[0]))
            self.assertIn("truth_audit_claim_true", read("news", audit=True)[0])
            vis = tl[0].index("visibility")
            self.assertGreater(len(tl), 1)
            self.assertNotIn("omniscient", {row[vis] for row in tl[1:]})
            self.assertIn("omniscient", {row[vis] for row in read("timeline", audit=True)[1:]})
            self.assertIsNone(server.csv_export(app.store, rid, "secrets"))
