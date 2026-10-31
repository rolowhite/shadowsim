"""Claims, publication, cohort belief and the society indicators.

A claim's ground truth (`truth`), who published it, what it cites, how far it spread (`reach`) and
how much each cohort believes it are separate fields. Repetition raises reach with diminishing effect
and can never lift belief past the rumor cap; only an authenticated, Detective-verified claim can.
"""
import math

from . import mechanics
from .state import CIVIL_ROLES, LEARNER

SOURCE_OF_ROLE = {"real_journalist": "independent_press", "fraud_journalist": "sponsored_press",
                  "detective": "investigator"}
PROOF_KINDS = ("records", "archive", "leak_decree", "packet")


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def source_type(eng, publisher, kind):
    if kind == "rumor":
        return "street"
    a = eng.w.agents.get(publisher) if publisher else None
    if kind == "official" or a is None:
        return "official" if kind == "official" else "individual"
    return SOURCE_OF_ROLE.get(a.role, "official" if a.state_agent else "individual")


def publish(eng, publisher, case_id, asserts, level, cited, kind, text, about=None):
    w = eng.w
    clid = eng.next_id("CL")
    src = source_type(eng, publisher, kind)
    if about:
        t = w.agents[about]
        truth = bool(t.corrupted or t.compromised)
    else:
        truth = (w.cases[case_id]["kind"] == "false_flag") == bool(asserts)
    claim = {"id": clid, "round": w.round, "publisher": publisher, "source": src, "kind": kind, "case": case_id,
             "about": about, "asserts": bool(asserts), "level": level, "cited": list(cited), "text": text,
             "truth": truth, "reach": {c["id"]: 0.0 for c in w.cohorts}, "repeats": 0, "status": "unverified",
             "public_proof": False, "event": None}
    w.claims[clid] = claim
    w.active_claims.append(clid)
    e = eng.emit("news", "claim", text, actor=publisher, target=about, visibility="public",
                 data={"claim": clid, "case": case_id, "source": src, "kind": kind, "level": level, "cited": list(cited)})
    claim["event"] = e["id"]
    assess(eng, claim, during_publish=True)
    expose(eng, claim)
    pub = w.agents.get(publisher) if publisher else None
    if pub and asserts and case_id and not pub.state_agent:
        f = mechanics.intel(eng, pub.id)  # the State reads the papers
        f["threat"] = min(1.5, f["threat"] + 0.1 + 0.1 * level)
    if about:
        t = w.agents[about]
        t.reputation = clamp(t.reputation - 0.1)
    if kind == "rumor":
        for c in w.cohorts:
            c["gtrust"] = clamp(c["gtrust"] - eng.cfg["cohort_dynamics"]["rumor_trust_cost"])
    return claim


def expose(eng, claim):
    """One exposure (publication or repeat). Moves reach and each cohort's belief about the case."""
    w, nc = eng.w, eng.cfg["news"]
    src = claim["source"]
    boost = nc["sponsored_boost"] if src == "sponsored_press" and (
        eng.holder("fraud_businessman") or w.round < w.last.get("sponsored_boost_until", -1)) else 1.0
    for c in w.cohorts:
        inc = min(1.0, c["media"].get(src, 0.3) * nc["reach_base"][src] * boost)
        claim["reach"][c["id"]] = round(claim["reach"][c["id"]] + (1 - claim["reach"][c["id"]]) * inc, 4)
    cid = claim["case"]
    if cid is None:
        return
    case = w.cases[cid]
    strength = nc["verified_strength"] if claim["public_proof"] else nc["level_strength"][min(claim["level"], 3)]
    cap, target = nc["rumor_cap"], 1.0 if claim["asserts"] else 0.0
    for c in w.cohorts:
        inc = min(1.0, c["media"].get(src, 0.3) * nc["reach_base"][src] * boost)
        step = nc["belief_rate"] * inc * c["cred"].get(src, 0.5) * strength / (1 + claim["repeats"])
        if claim["asserts"]:
            step *= 1 - nc["fear_suppression"] * c["fear"]
        step = min(step, 1.0)
        b = c["belief"].get(cid, 0.4)
        nb = b + step * (target - b)
        if not case["publicly_verified"]:  # without proof, belief cannot pass the cap in the claim's direction
            nb = min(nb, max(b, cap)) if target == 1.0 else max(nb, min(b, 1 - cap))
        c["belief"][cid] = round(nb, 5)


def assess(eng, claim, during_publish=False):
    """The Detective's assessment. Only a cryptographically verified decree makes a claim 'verified'."""
    w = eng.w
    det = eng.holder("detective")
    if det is None:
        return  # no Detective: the last assessment stands
    if claim["case"] is None:
        st = "unverified"
    else:
        k = det.knowledge.get(claim["case"])
        if k and k["level"] == 3 and k.get("verified"):
            st = "verified" if claim["asserts"] else "contradicted"
        elif k and k["explained"]:
            st = "contradicted" if claim["asserts"] else "under_investigation"
        elif k and k["level"] >= 1:
            st = "under_investigation"
        else:
            st = "unverified"
    if st != claim["status"]:
        claim["status"] = st
        if claim["kind"] != "official" or st != "unverified":
            mechanics.casebook(eng, f"Assessment of {claim['id']} ({claim['source'].replace('_', ' ')}): {st.replace('_', ' ')}.",
                               claim["case"], claim["cited"], {"verified": 0.97, "contradicted": 0.7}.get(st, 0.4), st,
                               "verified" if st == "verified" else "inference", source=f"published claim {claim['id']}",
                               claim=claim["id"])
    proof = (st == "verified" and claim["level"] == 3
             and any(w.evidence[e]["kind"] in PROOF_KINDS for e in claim["cited"] if e in w.evidence))
    if proof and not claim["public_proof"]:
        claim["public_proof"] = True
        public_verification(eng, claim, re_expose=not during_publish)


def assess_case(eng, cid):
    for clid in list(eng.w.active_claims):
        cl = eng.w.claims[clid]
        if cl["case"] == cid:
            assess(eng, cl)


def public_verification(eng, claim, re_expose=True):
    w, nc, cd = eng.w, eng.cfg["news"], eng.cfg["cohort_dynamics"]
    case = w.cases[claim["case"]]
    first = not case["publicly_verified"]
    case["publicly_verified"] = True
    if first:
        eng.emit("news", "public_verification",
                 f"Authenticated evidence on {case['id']} is now public: the decree signature checks out (claim {claim['id']}).",
                 actor=claim["publisher"], visibility="public", data={"case": case["id"], "claim": claim["id"]})
        for other in w.claims.values():
            if other["case"] != case["id"] or other["id"] == claim["id"]:
                continue
            delta = nc["credibility_step"] if other["asserts"] == claim["asserts"] else -nc["credibility_step"]
            for c in w.cohorts:
                c["cred"][other["source"]] = clamp(c["cred"].get(other["source"], 0.5) + delta)
        for c in w.cohorts:
            c["cred"][claim["source"]] = clamp(c["cred"].get(claim["source"], 0.5) + nc["credibility_step"])
            c["gtrust"] = clamp(c["gtrust"] + cd["verification_trust_gain"])
    if re_expose:
        expose(eng, claim)


def tick(eng):
    w, nc = eng.w, eng.cfg["news"]
    r = eng.rng
    if w.case_order and r.random() < mechanics.per_round(eng, nc["rumor_per_year"]):
        cid = r.choice(w.case_order[-10:])
        distrust = sum((1 - c["state_trust"]) * c["weight"] for c in w.cohorts)
        asserts = r.random() < 0.3 + 0.4 * distrust
        publish(eng, None, cid, asserts, 0, [], "rumor",
                f"Rumor: people say {cid} was {'a State operation' if asserts else 'exactly what officials said'}.")
    keep = []
    for clid in w.active_claims:
        cl = w.claims[clid]
        if w.round - cl["round"] > nc["active_window"] and not cl["public_proof"]:
            continue
        keep.append(clid)
        if r.random() < nc["republish_rate"] * (nc["verified_republish_mult"] if cl["public_proof"] else 1):
            cl["repeats"] += 1
            expose(eng, cl)
    w.active_claims = keep
    cohort_tick(eng)
    refresh_indicators(eng)


def cohort_tick(eng):
    w, cd, base, nc = eng.w, eng.cfg["cohort_dynamics"], eng.cfg["cohort_baseline"], eng.cfg["news"]
    rec = {cid: 1.0 if case["publicly_verified"] else math.exp(-(w.round - case["round"]) / nc["wrongdoing_decay_rounds"])
           for cid, case in w.cases.items()}
    for c in w.cohorts:
        W = max([b * rec[cid] for cid, b in c["belief"].items()] or [0.0])
        c["W"] = W
        target = base["state_trust"] - cd["wrongdoing_weight"] * max(0.0, W - cd["wrongdoing_floor"])
        c["state_trust"] = clamp(c["state_trust"] + cd["relax"] * (target - c["state_trust"]))
        c["fear"] = clamp(c["fear"] + cd["fear_relax"] * (base["fear"] - c["fear"]))
        sol_t = clamp(base["solidarity"] + c["push_solidarity"] - cd["fear_solidarity"] * (c["fear"] - base["fear"]))
        c["solidarity"] = clamp(c["solidarity"] + cd["relax"] * (sol_t - c["solidarity"]))
        c["push_solidarity"] *= 0.999
        c["gtrust"] = clamp(c["gtrust"] + cd["relax"] * (base["generalized_trust"] - c["gtrust"]))


def eligible(eng, L, role):
    req = eng.cfg["civil_succession"]["eligibility"][role]
    return all(L.traits[t] >= v for t, v in req.items())


def refresh_indicators(eng, full=False):
    w = eng.w
    cs = w.cohorts
    tw = sum(c["weight"] for c in cs)

    def avg(f):
        return round(sum(f(c) * c["weight"] for c in cs) / tw, 4)

    ind = w.indicators
    ind["public_trust"] = avg(lambda c: c["gtrust"])
    ind["fear"] = avg(lambda c: c["fear"])
    ind["media_credibility"] = avg(lambda c: (c["cred"].get("independent_press", 0.5) + c["cred"].get("sponsored_press", 0.5)) / 2)
    ind["state_legitimacy"] = avg(lambda c: c["state_trust"])
    ind["solidarity"] = avg(lambda c: c["solidarity"])
    if full or "evidence_exposure" not in ind:
        ff = [cid for cid, case in w.cases.items() if case["kind"] == "false_flag"]
        civ = [a for a in w.agents.values() if a.status == "active" and not a.state_agent]
        if ff:
            ind["evidence_exposure"] = round(sum(max([a.knowledge[cid]["level"] for a in civ if cid in a.knowledge
                                                      and not a.knowledge[cid]["explained"]] or [0]) for cid in ff) / (3 * len(ff)), 4)
        else:
            ind["evidence_exposure"] = 0.0
        learners = [a for a in w.agents.values() if a.status == "active" and a.role == LEARNER]
        ind["succession_readiness"] = round(sum(any(eligible(eng, L, r) for L in learners) for r in CIVIL_ROLES) / len(CIVIL_ROLES), 4)
