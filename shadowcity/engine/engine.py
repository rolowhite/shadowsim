"""Shadow City engine.

The engine owns every rule. Deciders (LLM, fallback, replay) only pick one option from a list the
engine builds. The engine does no file or network I/O, never reads the wall clock for mechanics,
and never reads analyst notes.
"""
import hashlib
import pickle
import random
import re

from . import access, actions, mechanics, news, outcomes, scenario, succession, talk
from .events import EventLog
from .state import World, VISIBLE, LEARNER

REF = re.compile(r"<<a:([A-Za-z0-9_]+)>>")
MEMORY = 40


def utility(a, f, w):
    """One shared utility for every agent. Roles differ only in which options they get and in
    their own traits, hidden attributes and situation. Abilities never appear here; motives do."""
    t, h = a.traits, a.hidden
    return (w["gain"] * f.get("gain", 0) * t["greed"] / 10
            - w["risk"] * f.get("risk", 0) * (1 - h["risk_tolerance"]) * (0.5 + a.fear)
            - w["tie_risk"] * f.get("tie_risk", 0) * h["attachment"]
            + w["align"] * f.get("align", 0) * (2 * a.loyalty - 1)
            - w["harm"] * f.get("harm", 0) * h["conscience"]
            + w["public_good"] * f.get("public_good", 0) * h["conscience"]
            + w["info"] * f.get("info", 0) * t["cur"] / 10
            + w["reputation"] * f.get("rep", 0)
            + w["ally"] * f.get("help_ally", 0)
            + w["advice"] * f.get("advised", 0)
            - w["effort"] * f.get("effort", 0) * (0.3 + a.burnout))


def fallback_choice(req):
    best = max(req["options"], key=lambda o: o["score"])
    return {"key": best["key"], "decided_by": "fallback_rule", "reason": "highest utility under the shared rule"}


class Engine:
    def __init__(self, cfg, seed=0, premise=None, run_secret=None, decider=None):
        self.cfg = cfg
        self.seed = int(seed)
        self.premise = premise or {"text": "", "visibility": "operator"}
        self.rng = random.Random(self.seed)
        secret = run_secret if run_secret is not None else f"seed-{self.seed}"
        self.key = hashlib.sha256(f"shadowcity:{secret}".encode()).digest()
        self.decider = decider
        self.log = EventLog()
        self.drained = 0
        self.decision_seq = 0
        self.stopped = False
        self.final = None
        self.verdict = []
        self.w = World(cfg, self.rng)
        self.role_names = {e["role"]: e["name"] for e in cfg["roster"]}
        self.role_names.update({"learner": "Learner", "heir": "Ruler's Heir", "citizen": "Citizen"})
        self.codes = {}
        ids = list(self.w.agents)
        random.Random(f"codes:{self.seed}").shuffle(ids)
        for aid in ids:
            self.codename(aid)
        mechanics.setup(self)
        succession.create_heir(self)
        scenario.apply(self)  # the researcher's backstory, after the heir exists
        news.refresh_indicators(self, full=True)

    # ---------- identity and rendering ----------
    def codename(self, aid):
        if aid not in self.codes:
            self.codes[aid] = f"P{len(self.codes) + 1:02d}"
        return self.codes[aid]

    def label(self, aid, viewer=None):
        a = self.w.agents.get(aid)
        if a is None:
            return str(aid)
        if viewer is None:  # operator display: stable id, plus current role when it differs
            if a.role in (LEARNER, a.archetype) or a.id == a.role:
                return a.id
            return f"{a.id} [{self.role_names.get(a.role, a.role)}]"
        role = a.role
        if a.alias and not viewer.state_agent and aid not in viewer.unmasked:
            role = a.alias
        return f"{self.codename(aid)} ({self.cfg['public_titles'].get(role, role.replace('_', ' '))})"

    def render(self, text, viewer=None):
        return REF.sub(lambda m: self.label(m.group(1), viewer), text)

    def next_id(self, prefix):
        n = self.w.ids.get(prefix, 0) + 1
        self.w.ids[prefix] = n
        return f"{prefix}-{n:04d}"

    # ---------- lookups ----------
    def holder(self, role):
        for a in self.w.agents.values():
            if a.role == role and a.status == "active":
                return a
        return None

    def agents_in(self, *statuses):
        return [a for a in self.w.agents.values() if a.status in statuses]

    def visible_count(self):
        return sum(1 for a in self.w.agents.values() if a.status in VISIBLE)

    # ---------- events ----------
    def emit(self, category, type_, summary, actor=None, target=None, visibility="public", data=None,
             decided_by=None):
        e = self.log.append(self.w.round, category, type_, actor, target, visibility, data, decided_by, summary,
                            self.render(summary))
        if visibility != "omniscient":
            for a in self.w.agents.values():
                if a.status in VISIBLE and access.visible(e, a):
                    a.memory.append(e["id"])
                    if len(a.memory) > MEMORY:
                        del a.memory[0]
        return e

    def reject(self, agent, action, why):
        self.emit("system", "action_rejected", f"Rejected {action} by <<a:{agent.id}>>: {why}",
                  actor=agent.id, visibility="omniscient", data={"action": action, "reason": why})

    def drain(self):
        out = self.log.events[self.drained:]
        self.drained = len(self.log.events)
        return out

    # ---------- decisions ----------
    def _choose(self, kind, agent, options, context="", listener=None):
        wts, sd = self.cfg["utility"]["weights"], self.cfg["utility"]["noise_sd"]
        for o in options:  # noise is drawn here so every decider consumes the RNG identically
            o["score"] = round(utility(agent, o.get("features", {}), wts) + self.rng.gauss(0, sd), 6)
        self.decision_seq += 1
        req = {"seq": self.decision_seq, "kind": kind, "agent": agent.id, "round": self.w.round,
               "options": options, "context": context, "listener": listener}
        res = fallback_choice(req) if self.decider is None else self.decider.decide(req, self)
        chosen = next((o for o in options if o["key"] == res["key"]), None)
        if chosen is None:  # deciders must return a listed key; never trust them blindly
            res = dict(fallback_choice(req), decided_by="fallback_after_invalid_choice")
            chosen = next(o for o in options if o["key"] == res["key"])
        by = res["decided_by"]
        c = self.w.counters
        c["decided_by:" + by] = c.get("decided_by:" + by, 0) + 1
        data = {"kind": kind, "seq": req["seq"], "chosen": chosen["key"], "reason": res.get("reason", ""),
                "scores": {o["key"]: o["score"] for o in options}, "llm_call": res.get("llm_call")}
        if res.get("say"):
            data["say"] = res["say"]
        self.emit("decision", kind, f"<<a:{agent.id}>> chose {chosen['key']} ({kind})", actor=agent.id,
                  target=chosen.get("target"), visibility="omniscient", decided_by=by, data=data)
        return res, chosen

    def decide(self, kind, agent, options, context="", to=None, advisor=None, ctx=None):
        """Optional advice first: the advisor runs the same options through their own utility and says it;
        the advised option gets a bonus scaled by the decider's trust in the advisor and the advisor's Man."""
        advised = None
        if advisor is not None and advisor.id != agent.id and advisor.status == "active":
            adv_opts = [dict(o, features=dict(o.get("features", {}))) for o in options]
            a_res, a_ch = self._choose("advise_" + kind, advisor, adv_opts, context, listener=agent.id)
            advised = a_ch["key"]
            talk.advice(self, advisor, agent, kind, a_ch, a_res.get("say"), ctx)
            bonus = round(agent.t(advisor.id) * (0.5 + 0.5 * advisor.traits["man"] / 10), 4)
            for o in options:
                if o["key"] == advised:
                    o["features"] = dict(o.get("features", {}), advised=bonus)
        listeners = [x for x in ([advisor.id] if advised else []) + list(to or []) if x]
        res, chosen = self._choose(kind, agent, options, context, listener=listeners[0] if listeners else None)
        talk.reply(self, agent, listeners, kind, chosen, res.get("say"), ctx, advised_key=advised)
        ok, why = actions.validate(agent, chosen["action"], self.w.regime)
        if not ok:
            self.reject(agent, chosen["action"], why)
            return None
        return dict(chosen, decided_by=res["decided_by"])

    # ---------- loop ----------
    @property
    def finished(self):
        return self.stopped or self.w.round >= self.cfg["time"]["round_limit"]

    def step_round(self):
        if self.finished:
            return False
        w = self.w
        w.round += 1
        order = [a.id for a in self.agents_in("active")]
        self.rng.shuffle(order)  # no fixed first mover
        mechanics.begin_round(self)
        for aid in order:
            a = w.agents[aid]
            if a.active:
                mechanics.turn(self, a)
        news.tick(self)
        succession.state_succession(self)
        if w.round % self.cfg["time"]["epoch_length"] == 0:
            succession.checkpoint(self)
        outcomes.check(self)
        if w.round % self.cfg["time"]["metrics_every"] == 0 or w.round == 1:
            news.refresh_indicators(self, full=True)
            w.metrics.append(dict(w.indicators, round=w.round))
        if self.finished and self.final is None:
            self.final = outcomes.finalize(self)
        return True

    def run(self, rounds=None):
        n = 0
        while not self.finished and (rounds is None or n < rounds):
            self.step_round()
            n += 1
        return self.final

    # ---------- snapshots (used to roll back a round when a strict LLM run fails mid-round) ----------
    def snapshot(self):
        return pickle.dumps((self.w, self.rng.getstate(), len(self.log.events), self.log.prev_hash,
                             self.decision_seq, self.codes, self.stopped, self.final))

    def restore(self, blob):
        self.w, rs, n, prev, self.decision_seq, self.codes, self.stopped, self.final = pickle.loads(blob)
        self.rng.setstate(rs)
        del self.log.events[n:]
        self.log.prev_hash = prev
        self.drained = min(self.drained, n)
