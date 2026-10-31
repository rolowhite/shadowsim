"""Shared run setup: config loading and overrides, provenance hashes, run summaries."""
import copy
import hashlib
import json
import math
import pathlib
import secrets
import time

from .engine import access, outcomes
from .engine.engine import Engine
from .engine.events import canon
from .llm import provider as llm

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "default.json"


def load_config(path=None):
    return json.loads(pathlib.Path(path or DEFAULT_CONFIG).read_text())


def apply_overrides(cfg, overrides):
    """overrides: {"a.b.c": value}. Unknown paths are rejected so a typo can't silently do nothing."""
    cfg = copy.deepcopy(cfg)
    for path, value in (overrides or {}).items():
        d, keys = cfg, path.split(".")
        for k in keys[:-1]:
            if not isinstance(d, dict) or k not in d:
                raise KeyError(f"unknown config path: {path}")
            d = d[k]
        if not isinstance(d, dict) or keys[-1] not in d:
            raise KeyError(f"unknown config path: {path}")
        old = d[keys[-1]]
        if isinstance(old, (int, float)) and not isinstance(old, bool) and not isinstance(value, (int, float)):
            raise ValueError(f"{path} expects a number")
        d[keys[-1]] = value
    return cfg


def parse_override_text(text):
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        try:
            out[k.strip()] = json.loads(v.strip())
        except json.JSONDecodeError:
            out[k.strip()] = v.strip()
    return out


def code_version():
    h = hashlib.sha256()
    for p in sorted((ROOT / "shadowcity").rglob("*.py")):
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def config_hash(cfg):
    return hashlib.sha256(canon(cfg).encode()).hexdigest()[:12]


def new_run_id(seed):
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-s{seed}-{secrets.token_hex(2)}"


def provider_info(cfg):
    """Resolve the configured provider. Returns (provider or None, info dict shown in both windows)."""
    lc = cfg["llm"]
    kind = lc.get("provider", "fallback")
    if kind == "fallback":
        return None, {"kind": "fallback", "label": "DETERMINISTIC FALLBACK (rule-based, not an LLM)", "model": None,
                      "mode": lc.get("mode"), "status": "fallback"}
    p = llm.make_provider(lc)
    ok, detail = p.health() if p else (False, "no provider reachable")
    if not ok:
        if kind == "auto":
            return None, {"kind": "fallback", "label": "DETERMINISTIC FALLBACK (no model reachable)", "model": None,
                          "mode": lc.get("mode"), "status": "fallback", "detail": detail}
        raise RuntimeError(f"LLM provider '{kind}' is not reachable ({detail}); start it or choose the fallback")
    return p, {"kind": p.name, "label": f"LLM {p.name}: {p.model}", "model": p.model, "url": p.url, "mode": lc.get("mode"),
               "temperature": lc["temperature"], "route": lc["route"], "status": "connected"}


def make_engine(cfg, seed, premise=None, secret=None, decider=None):
    return Engine(cfg, seed=seed, premise=premise, run_secret=secret, decider=decider)


def summary(eng):
    w = eng.w
    ev = eng.log.events
    count = lambda t: sum(1 for e in ev if e["type"] == t)  # noqa: E731
    ind = dict(w.indicators)
    return {"final": eng.final, "round": w.round, "milestones": {k: v["round"] for k, v in w.milestones.items()},
            "indicators": ind, "false_flags": sum(c["kind"] == "false_flag" for c in w.cases.values()),
            "incidents": sum(c["kind"] in ("genuine", "false_flag") for c in w.cases.values()), "genuine_share": access.genuine_share(eng), "claims": len(w.claims),
            "eliminations": sum(1 for a in w.agents.values() if a.status == "eliminated"),
            "packets": len(w.legacy_queue), "dms_triggers": count("dms_trigger"), "leaks": w.counters.get("leaks", 0),
            "offers": count("offer"), "accepted": sum(1 for a in w.agents.values() if a.corrupted),
            "feigned": sum(1 for a in w.agents.values() if a.double_agent),
            "succession_failures": w.succession_failures, "regime": w.regime,
            "decided_by": {k.split(":", 1)[1]: v for k, v in w.counters.items() if k.startswith("decided_by:")}}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - r) / d, 3), round((c + r) / d, 3))


LABELS = outcomes.FINAL_LABELS
