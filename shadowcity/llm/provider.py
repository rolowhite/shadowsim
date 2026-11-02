"""Model providers and deciders. The engine never imports this module.

- OllamaProvider / OpenAICompatProvider: one HTTP endpoint = one loaded model, reused for every agent.
- Scheduler: a single worker thread queues all requests to that one model.
- LLMDecider: routes configured decision kinds to the model; everything else, and the fallback, is the
  engine's shared rule, labeled `fallback_rule`. Strict mode never silently falls back on a failure.
- ReplayDecider: re-plays recorded decisions to reproduce a run exactly (then optionally goes live).
"""
import concurrent.futures as cf
import hashlib
import json
import queue
import threading
import time
import urllib.request

from . import prompts


class LLMFailure(Exception):
    pass


def _post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


class OllamaProvider:
    name = "ollama"

    def __init__(self, url, model):
        self.url, self.model = url.rstrip("/"), model

    def health(self):
        try:
            tags = _get(self.url + "/api/tags")
            names = [m.get("name") for m in tags.get("models", [])]
            return (self.model in names or any(n and n.startswith(self.model) for n in names)), f"models: {names[:5]}"
        except Exception as e:  # noqa: BLE001 - any failure means "not reachable"
            return False, str(e)

    def chat(self, messages, temperature, seed, max_tokens, timeout):
        out = _post(self.url + "/api/chat", {"model": self.model, "messages": messages, "stream": False, "format": "json",
                                              "options": {"temperature": temperature, "seed": seed, "num_predict": max_tokens}},
                    timeout)
        return out["message"]["content"]


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, url, model):
        self.url, self.model = url.rstrip("/"), model

    def health(self):
        try:
            _get(self.url + "/models")
            return True, "reachable"
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    def chat(self, messages, temperature, seed, max_tokens, timeout):
        out = _post(self.url + "/chat/completions", {"model": self.model, "messages": messages, "temperature": temperature,
                                                     "seed": seed, "max_tokens": max_tokens}, timeout)
        return out["choices"][0]["message"]["content"]


def make_provider(llm_cfg):
    kind = llm_cfg.get("provider", "fallback")
    if kind == "fallback":
        return None
    cands = []
    if kind in ("ollama", "auto"):
        cands.append(OllamaProvider(llm_cfg["ollama_url"], llm_cfg["model"]))
    if kind in ("openai_compat", "auto"):
        cands.append(OpenAICompatProvider(llm_cfg["openai_url"], llm_cfg["model"]))
    for p in cands:
        if kind != "auto" or p.health()[0]:
            return p
    return None


class Scheduler:
    """One worker, one queue, one model: requests never run concurrently against the GPU."""

    def __init__(self, provider):
        self.provider = provider
        self.q = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            fut, args = self.q.get()
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(self.provider.chat(*args))
                except Exception as e:  # noqa: BLE001
                    fut.set_exception(e)

    def submit(self, *args):
        fut = cf.Future()
        self.q.put((fut, args))
        return fut


class LLMDecider:
    def __init__(self, provider, llm_cfg, scheduler=None, strict=None):
        self.provider, self.cfg = provider, llm_cfg
        self.scheduler = scheduler or Scheduler(provider)
        self.strict = (llm_cfg.get("mode", "strict") == "strict") if strict is None else strict
        self.route = set(llm_cfg.get("route", []))
        self.calls = []  # drained by the runner into the llm_calls table

    def decide(self, req, eng):
        if req["kind"] not in self.route:
            return _fallback(req, "fallback_rule")
        messages, order = prompts.build(eng, req)
        phash = hashlib.sha256(json.dumps(messages).encode()).hexdigest()[:16]
        err = None
        for attempt in (1, 2):  # one retry
            t0 = time.time()
            text = None
            try:
                fut = self.scheduler.submit(messages, self.cfg["temperature"], eng.seed * 100003 + req["seq"],
                                            self.cfg["max_tokens"], self.cfg["timeout_s"])
                text = fut.result(timeout=self.cfg["timeout_s"] + 5)
                idx, reason, say = prompts.parse(text, order)
                call = self._record(req, eng, phash, messages, text, t0, True, None, attempt)
                return {"key": req["options"][idx]["key"], "decided_by": f"llm:{self.provider.name}:{self.provider.model}",
                        "reason": reason, "say": say, "llm_call": call}
            except Exception as e:  # noqa: BLE001 - timeouts, bad JSON, out-of-range options
                err = f"{type(e).__name__}: {e}"
                self._record(req, eng, phash, messages, text, t0, False, err, attempt)
        if self.strict:
            raise LLMFailure(f"decision {req['seq']} ({req['kind']}) failed twice: {err}")
        return _fallback(req, "fallback_after_llm_error")

    def _record(self, req, eng, phash, messages, text, t0, ok, err, attempt):
        n = len(self.calls) + 1
        self.calls.append({"decision_seq": req["seq"], "round": req["round"], "agent": req["agent"], "kind": req["kind"],
                           "model": f"{self.provider.name}:{self.provider.model}", "prompt_hash": phash,
                           "prompt": json.dumps(messages), "reply": text, "latency": round(time.time() - t0, 3),
                           "ok": ok, "error": err, "attempt": attempt})
        return n


def _fallback(req, label):
    best = max(req["options"], key=lambda o: o["score"])
    return {"key": best["key"], "decided_by": label, "reason": "highest utility under the shared rule"}


class ReplayMismatch(Exception):
    pass


class ReplayDecider:
    """Feeds back recorded decisions (from decision events) in order. When they run out it hands over to
    `then` (a live decider) or the engine fallback, so a run can be resumed after a restart."""

    def __init__(self, decisions, then=None):
        self.decisions, self.i, self.then = list(decisions), 0, then

    @property
    def exhausted(self):
        return self.i >= len(self.decisions)

    def decide(self, req, eng):
        if self.exhausted:
            return self.then.decide(req, eng) if self.then else _fallback(req, "fallback_rule")
        d = self.decisions[self.i]
        self.i += 1
        if d["seq"] != req["seq"] or d["kind"] != req["kind"] or d["actor"] != req["agent"]:
            raise ReplayMismatch(f"recorded #{d['seq']} {d['kind']}/{d['actor']} vs live #{req['seq']} {req['kind']}/{req['agent']}")
        return {"key": d["chosen"], "decided_by": d["decided_by"], "reason": d.get("reason", ""), "say": d.get("say")}


def decisions_from_events(events):
    return [{"seq": e["data"]["seq"], "kind": e["data"]["kind"], "actor": e["actor"], "chosen": e["data"]["chosen"],
             "decided_by": e["decided_by"], "reason": e["data"].get("reason", ""), "say": e["data"].get("say")}
            for e in events if e["category"] == "decision"]
