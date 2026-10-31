"""Information access, enforced in the backend.

- observation(): the only thing that ever reaches an agent's prompt. Only what that agent may know.
- desk_view(): the Investigation Desk. The Detective's own casebook plus published claims. No ground truth.
- desk_audit(): ground truth for the Truth Audit toggle. Served by a separate endpoint.
- control_view(): the operator's Control Room (facts about people + agent-held beliefs, badged).
- audit_view(): the omniscient ground-truth panel.
"""
from .state import VISIBLE, LEARNER, CIVIL_ROLES

LEVEL = {0: "none", 1: "anomaly", 2: "link", 3: "authorization"}
HIDDEN_KEYS = ("truth", "kind_truth", "signature", "decree_record", "spurious", "misattributed", "decoy")


def visible(e, a):
    v = e["visibility"]
    if v == "public":
        return True
    if v == "omniscient":
        return False
    if v == "state":
        return a.state_agent
    if v == "civil":
        return not a.state_agent
    return a.id in v


def p_state(k):
    """How strongly an agent believes the State was behind a case, from its own evidence only."""
    if k["level"] == 3:
        return 0.97
    if k["explained"]:
        return 0.1
    return {0: 0.3, 1: 0.55, 2: 0.75}[k["level"]]


def words(x, scale):
    return scale[min(len(scale) - 1, int(x * len(scale)))]


def observation(eng, aid):
    w = eng.w
    a = w.agents[aid]
    obs = {"round": w.round, "year": round(w.round / eng.cfg["time"]["rounds_per_year"], 1),
           "you": {"codename": eng.codename(aid), "role": a.role if a.role in eng.cfg["prompt_cards"] else LEARNER,
                   "fear": words(a.fear, ["calm", "uneasy", "afraid", "terrified"]),
                   "exhaustion": words(a.burnout, ["rested", "tired", "worn out", "burnt out"]),
                   "wealth": round(a.wealth, 2)},
           "your_cases": [], "recent": []}
    pv = eng.premise.get("visibility", "operator")
    if eng.premise.get("text") and (pv == "public" or (pv == "state" and a.state_agent)):
        obs["premise"] = eng.premise["text"]
    for cid, k in sorted(a.knowledge.items(), key=lambda kv: -kv[1]["round"])[:6]:
        case = w.cases[cid]
        obs["your_cases"].append({"case": cid, "official_cause": case["official_story"],
                                  "your_evidence": "supports official account" if k["explained"] else LEVEL[k["level"]],
                                  "linked_to": k["attr"], "decree": k["decree"] if k["level"] == 3 else None})
    for eid in a.memory[-6:]:
        e = eng.log.get(eid)
        if e:
            obs["recent"].append(f"r{e['round']}: {eng.render(e['summary'], viewer=a)}")
    if a.state_agent:
        from .mechanics import sanitized_report
        if a.role == "ruler":
            obs["report"] = sanitized_report(eng)
        else:
            top = sorted(((f["threat"], x) for x, f in w.intel.items() if w.agents[x].status == "active"), reverse=True)[:3]
            obs["security_file"] = [{"person": eng.label(x, a), "threat": round(t, 2)} for t, x in top if t > 0]
    return obs


def claim_public(eng, cl):
    return {"id": cl["id"], "round": cl["round"], "publisher": eng.label(cl["publisher"]) if cl["publisher"] else
            ("street" if cl["kind"] == "rumor" else "official statement"), "source": cl["source"], "kind": cl["kind"],
            "type": "rumor" if cl["kind"] == "rumor" else "claim", "case": cl["case"],
            "about": eng.label(cl["about"]) if cl["about"] else None, "asserts_state_involved": cl["asserts"] if cl["case"] else None,
            "level": LEVEL.get(cl["level"], cl["level"]), "cited": cl["cited"], "text": eng.render(cl["text"]),
            "reach": round(sum(cl["reach"].values()) / max(1, len(cl["reach"])), 3), "repeats": cl["repeats"],
            "status": cl["status"], "event": cl["event"]}


def desk_view(eng):
    w = eng.w
    det = eng.holder("detective")
    return {"round": w.round, "detective": eng.label(det.id) if det else None,
            "casebook": [dict(e) for e in w.casebook],
            "news": [claim_public(eng, cl) for cl in w.claims.values()],
            "incidents": [{"case": cid, "round": w.cases[cid]["round"], "official_cause": w.cases[cid]["official_story"],
                           "event": w.cases[cid]["event"]} for cid in w.case_order],
            "agents": [a.id for a in w.agents.values() if a.status != "hidden"]}


def desk_audit(eng):
    w = eng.w
    return {"claims": {cl["id"]: {"truth": cl["truth"], "case_was": w.cases[cl["case"]]["kind"] if cl["case"] else None}
                       for cl in w.claims.values()},
            "cases": {cid: {"kind": c["kind"], "true_cause": "State decree " + c["decree"] if c["decree"] else c["subtype"],
                            "ministry": c["ministry"] if c["decree"] else None} for cid, c in w.cases.items()}}


def control_view(eng):
    w = eng.w
    roster = []
    for a in w.agents.values():
        if a.status == "hidden":
            continue
        roster.append({"id": a.id, "label": eng.label(a.id), "role": a.role, "role_name": eng.role_names.get(a.role, a.role),
                       "archetype": a.archetype, "tier": a.tier, "traits": {t: round(v, 1) for t, v in a.traits.items()},
                       "loyalty": round(a.loyalty, 2), "fear": round(a.fear, 2), "burnout": round(a.burnout, 2),
                       "reputation": round(a.reputation, 2), "status": a.status, "born": a.born, "ends": a.ends,
                       "alias": a.alias,
                       "beliefs": [{"case": cid, "evidence": "explained" if k["explained"] else LEVEL[k["level"]],
                                    "linked_to": k["attr"], "p_state": p_state(k)} for cid, k in sorted(a.knowledge.items())]})
    return {"round": w.round, "epoch": w.round // eng.cfg["time"]["epoch_length"], "regime": w.regime,
            "indicators": dict(w.indicators), "indicator_help": eng.cfg["indicator_help"], "roster": roster,
            "milestones": {k: v["round"] for k, v in w.milestones.items()}, "final": eng.final,
            "legacy_queue": {"queued": sum(e["status"] == "queued" for e in w.legacy_queue),
                             "delivered": sum(e["status"] == "delivered" for e in w.legacy_queue)},
            "metrics": w.metrics[-300:]}


def audit_view(eng):
    from .events import verify
    w = eng.w
    return {"round": w.round,
            "cases": [dict(c) for c in w.cases.values()],
            "decrees": [dict(d, signature_valid=verify(eng.key, d)) for d in w.decrees.values()],
            "orders": list(w.orders.values()),
            "traces": list(w.traces.values()),
            "legacy_queue": w.legacy_queue,
            "state_file": {eng.label(k): v for k, v in w.intel.items()},
            "secret_flags": [{"id": a.id, "status": a.status, "corrupted": a.corrupted, "compromised": a.compromised,
                              "double_agent": a.double_agent, "flagged": a.flagged, "hidden": a.hidden,
                              "unmasked": a.unmasked} for a in w.agents.values()
                             if a.corrupted or a.compromised or a.double_agent or a.flagged or a.status == "hidden"],
            "hidden_family": [{"id": a.id, "traits": a.traits} for a in w.agents.values() if a.status == "hidden"],
            "claims_truth": {cl["id"]: cl["truth"] for cl in w.claims.values()},
            "milestones": w.milestones, "competence": round(w.competence, 3), "regime": w.regime,
            "decided_by": {k.split(":", 1)[1]: v for k, v in w.counters.items() if k.startswith("decided_by:")},
            "genuine_share": genuine_share(eng)}


def genuine_share(eng):
    cs = eng.w.cases.values()
    n = len(cs)
    return round(sum(c["kind"] == "genuine" for c in cs) / n, 3) if n else None
