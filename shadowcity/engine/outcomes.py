"""Explicit outcome conditions. Milestones are logged the first round they hold; the run continues to the
round limit (unless time.stop_on_outcome). The final label uses a fixed precedence so labels never overlap.
Rumor alone can never satisfy a truth condition: truth needs a publicly verified, authenticated decree."""
from .state import CIVIL_ROLES, LEARNER, STATE_LEADERSHIP, IMPOSTORS
from . import news

TERMINAL = ("change_after_verified_truth", "change_without_verified_truth", "civil_dark_age")
FINAL_LABELS = ("verified_disclosure_changed_control", "change_without_verified_truth", "truth_public", "dark_age",
                "state_retains", "resistance_survives_unresolved", "unresolved")


def believed(eng, cid):
    oc = eng.cfg["outcomes"]
    return sum(1 for c in eng.w.cohorts if c["belief"].get(cid, 0) >= oc["public_belief"]) >= oc["public_cohorts"]


def clean(a):
    return a.status == "active" and not a.corrupted and not a.compromised


def investigators(eng):
    """Living, uncorrupted people still able to pursue the truth: the investigative roles and packet holders."""
    w = eng.w
    out = [a for a in w.agents.values() if a.role in ("detective", "truth_seeker", "real_journalist") and clean(a)]
    holders = {e["holder"] for e in w.legacy_queue if e["status"] == "delivered"}
    out += [w.agents[h] for h in holders if h in w.agents and clean(w.agents[h]) and w.agents[h] not in out]
    return out


def resistance_alive(eng):
    w = eng.w
    holders = {e["holder"] for e in w.legacy_queue if e["status"] == "delivered"}
    for a in w.agents.values():
        if not clean(a) or a.state_agent:
            continue
        if a.id in holders:
            return True
        for cid, k in a.knowledge.items():
            if k["level"] >= 2 and not k["explained"] and w.cases[cid]["decree"]:
                return True
    return False


def defections(eng):
    w, oc = eng.w, eng.cfg["outcomes"]
    low = {a.id for a in w.agents.values() if a.status == "active" and a.state_agent
           and (a.role in STATE_LEADERSHIP or a.role in IMPOSTORS) and a.loyalty < oc["defection_loyalty"]}
    return len(low | set(w.counters.get("leakers", [])))


def dark_age_now(eng):
    w, oc = eng.w, eng.cfg["outcomes"]
    if w.succession_failures == 0 or w.indicators["fear"] < oc["dark_age_fear"]:
        return False
    if any(eng.holder(r) and clean(eng.holder(r)) for r in CIVIL_ROLES):
        return False
    learners = [a for a in w.agents.values() if a.status == "active" and a.role == LEARNER]
    return not any(news.eligible(eng, L, r) for L in learners for r in CIVIL_ROLES)


def mark(eng, name, **data):
    w = eng.w
    if name in w.milestones:
        return
    w.milestones[name] = dict(data, round=w.round)
    eng.emit("system", "milestone", f"Milestone reached: {name.replace('_', ' ')}.", visibility="omniscient",
             data=dict(data, milestone=name))
    if eng.cfg["time"]["stop_on_outcome"] and name in TERMINAL:
        eng.stopped = True


def change_of_control(eng, name):
    w = eng.w
    w.regime = "changed"
    mark(eng, name)
    eng.emit("government", "change_of_control", "Change of control: the State's coercive authority is suspended and "
             "public institutions are reorganized.", visibility="public", data={"path": name})


def check(eng):
    w, oc = eng.w, eng.cfg["outcomes"]
    ind = w.indicators
    t = w.milestones.get("truth_verified_public")
    if t is None:
        for cid, case in w.cases.items():
            if case["publicly_verified"] and case["decree"] and believed(eng, cid):
                mark(eng, "truth_verified_public", case=cid)
                t = w.milestones["truth_verified_public"]
                break
    if w.regime == "incumbent":
        leg = ind["state_legitimacy"]
        if t and w.round - t["round"] <= oc["change_watch"] and leg < oc["change_legitimacy"]:
            change_of_control(eng, "change_after_verified_truth")
        elif leg < oc["change_without_truth_legitimacy"] and (
                ind["solidarity"] >= oc["change_without_truth_solidarity"] or defections(eng) >= oc["insider_defections"]):
            # the other pathways (collapse with mass solidarity or insider defection) count as "after truth"
            # only if a verified disclosure already happened; the label records which
            change_of_control(eng, "change_after_verified_truth" if t else "change_without_verified_truth")
    c = w.counters
    c["no_investigator"] = 0 if investigators(eng) else c.get("no_investigator", 0) + 1
    if c["no_investigator"] >= oc["suppressed_rounds"]:
        mark(eng, "investigation_suppressed")
    c["dark"] = c.get("dark", 0) + 1 if dark_age_now(eng) else 0
    if c["dark"] >= oc["dark_age_rounds"]:
        mark(eng, "civil_dark_age")


def finalize(eng):
    w, oc = eng.w, eng.cfg["outcomes"]
    m = w.milestones
    if "change_after_verified_truth" in m:
        label = "verified_disclosure_changed_control"
    elif "change_without_verified_truth" in m:
        label = "change_without_verified_truth"
    elif "truth_verified_public" in m and believed(eng, m["truth_verified_public"]["case"]):
        label = "truth_public"
    elif w.counters.get("dark", 0) >= oc["dark_age_rounds"]:
        label = "dark_age"
    elif not resistance_alive(eng) and w.indicators["state_legitimacy"] >= oc["state_retains_legitimacy"]:
        label = "state_retains"
    elif resistance_alive(eng):
        label = "resistance_survives_unresolved"
    else:
        label = "unresolved"
    eng.emit("system", "final_outcome", f"Run ended at round {w.round}: {label.replace('_', ' ')}.", visibility="public",
             data={"label": label, "milestones": {k: v["round"] for k, v in m.items()}})
    return label
