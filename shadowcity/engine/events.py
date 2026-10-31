"""Append-only, hash-chained event log and HMAC signing.

Each event gets a stable id (ev-NNNNNN) and a hash over (previous hash + canonical event).
The wall-clock timestamp is added after hashing, so the chain is reproducible from the seed.
"""
import hashlib
import hmac
import json
import time


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sign(key: bytes, record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "signature"}
    return hmac.new(key, canon(body).encode(), hashlib.sha256).hexdigest()


def verify(key: bytes, record: dict) -> bool:
    return hmac.compare_digest(sign(key, record), record.get("signature", ""))


class EventLog:
    def __init__(self):
        self.events = []
        self.prev_hash = "0" * 64

    def append(self, round_, category, type_, actor=None, target=None, visibility="public",
               data=None, decided_by=None, summary="", text=""):
        seq = len(self.events) + 1
        ev = {"seq": seq, "id": f"ev-{seq:06d}", "round": round_, "category": category, "type": type_,
              "actor": actor, "target": target, "visibility": visibility, "summary": summary,
              "text": text or summary, "data": data or {}, "decided_by": decided_by}
        h = hashlib.sha256((self.prev_hash + canon(ev)).encode()).hexdigest()
        ev["prev_hash"], ev["hash"], ev["ts"] = self.prev_hash, h, time.time()
        self.prev_hash = h
        self.events.append(ev)
        return ev

    def get(self, event_id):
        seq = int(event_id.split("-")[1])
        return self.events[seq - 1] if 0 < seq <= len(self.events) else None


def verify_chain(events):
    """Recompute the chain; returns the first bad seq or None."""
    prev = "0" * 64
    for e in events:
        body = {k: e[k] for k in ("seq", "id", "round", "category", "type", "actor", "target",
                                  "visibility", "summary", "text", "data", "decided_by")}
        if e["prev_hash"] != prev or hashlib.sha256((prev + canon(body)).encode()).hexdigest() != e["hash"]:
            return e["seq"]
        prev = e["hash"]
    return None
