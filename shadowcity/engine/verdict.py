"""End-of-run verdict: a plain-language summary built only from the final state and the event log.
Lines use <<a:id>> references, rendered with each agent's label when shown."""
from .state import IMPOSTORS, MINISTRIES

STATE_ROLES = {"ruler", "sub_ruler", "government", "heir", *MINISTRIES, *IMPOSTORS}
NAMED = ("issued_by", "proposed_by", "executor", "handler")
LEVEL = {1: "an anomaly", 2: "a link to those responsible", 3: "the signed record itself"}
ENDS = {"death": "died", "dismissal": "was dismissed", "detained": "was detained", "double_agent_exposed": "was exposed and removed",
        "retired": "retired"}


def initial_role(a):
    return a.history[0][1].split("->")[0] if a.history else a.role


def role_at(a, rnd):
    role = initial_role(a)
    for r, change in a.history:
        if r <= rnd:
            role = change.split("->")[1]
    return role


def build(eng, label):
    w, ev = eng.w, eng.log.events
    rn = eng.role_names
    name = lambda aid, rnd: f"{aid} ({rn.get(role_at(w.agents[aid], rnd), role_at(w.agents[aid], rnd))})"  # noqa: E731
    out = [f"Simulation ended at round {w.round} (year {w.round / eng.cfg['time']['rounds_per_year']:.0f}). "
           f"Final outcome: {label.replace('_', ' ')}."]
    ms = sorted(w.milestones.items(), key=lambda kv: kv[1]["round"])
    out.append("Milestones: " + "; ".join(f"{k.replace('_', ' ')} (round {v['round']})" for k, v in ms) + "."
               if ms else "No milestone was reached.")

    # deepest knowledge anyone outside the State reached during the run, per case: (level, round, agent).
    # Knowledge given by the researcher's backstory is reported separately, never as a discovery.
    best, given = {}, {}
    for e in ev:
        if e["type"] != "knowledge" or e["data"].get("explained"):
            continue
        if e["data"].get("source") == "backstory":
            given.setdefault(e["data"]["case"], []).append(e["actor"])
            continue
        a = w.agents.get(e["actor"])
        lvl, cid = e["data"]["level"], e["data"]["case"]
        if a is None or role_at(a, e["round"]) in STATE_ROLES or lvl < 1:
            continue
        cur = best.get(cid)
        if cur is None or lvl > cur[0]:
            best[cid] = (lvl, e["round"], a.id)
    proven = {e["data"]["case"]: e["round"] for e in ev if e["type"] == "public_verification"}

    # the Ruler
    ruler_cases = [d["case"] for d in w.decrees.values()
                   if d.get("issued_by") in w.agents and role_at(w.agents[d["issued_by"]], d["round"]) == "ruler"]
    found = sorted((best[c][1], c) for c in ruler_cases if c in best and best[c][0] == 3)
    if found:
        rnd, cid = found[0]
        out.append(f"The Ruler was discovered: {name(best[cid][2], rnd)} obtained the Ruler's signed record behind {cid} "
                   f"in round {rnd}. {len(found)} of {len(ruler_cases)} Ruler-signed records were reached in the end.")
    else:
        out.append(f"No one outside the State discovered that the Ruler signed any of the {len(ruler_cases)} secret records.")
        near = max((best[c] for c in ruler_cases if c in best), default=None)
        if near:
            out.append(f"The closest anyone came: {name(near[2], near[1])} reached {LEVEL[near[0]]} in round {near[1]}.")

    # each secret
    for cid, case in w.cases.items():
        if not case["decree"]:
            continue
        d = w.decrees[case["decree"]]
        what = (d.get("description") or ("a killing ordered outside the law" if d.get("extra_legal") else "a killing order")
                if d["kind"] != "false_flag" else "a staged attack")
        line = f"{cid} (truth: {what}): "
        line += (f"furthest outside the State was {name(best[cid][2], best[cid][1])}, who reached {LEVEL[best[cid][0]]} "
                 f"in round {best[cid][1]}" if cid in best else "nobody outside the State learned anything")
        line += f"; publicly proven in round {proven[cid]}." if cid in proven else "; never publicly proven."
        if cid in given:
            line += " Known from the start (backstory): " + ", ".join(name(x, 0) for x in given[cid]) + "."
        out.append(line)

    # what the public found out, and detentions
    for cid, rnd in sorted(proven.items(), key=lambda kv: kv[1]):
        d = w.decrees[w.cases[cid]["decree"]]
        ids = []
        for f in NAMED:
            if d.get(f) in w.agents and d[f] not in ids:
                ids.append(d[f])
        out.append(f"Round {rnd}: the public learned who was behind {cid}: " + ", ".join(name(x, d["round"]) for x in ids) + ".")
    det = [(e["round"], e["target"]) for e in ev if e["type"] == "detained"]
    if det:
        out.append("Detained: " + "; ".join(f"{name(t, r)} in round {r}" for r, t in det) + ".")

    caught = {d[f] for c in proven for d in [w.decrees[w.cases[c]["decree"]]] for f in NAMED if d.get(f)}
    ends = {}
    for e in ev:
        if e["type"] in ENDS and e["target"]:
            ends.setdefault(e["target"], []).append((e["round"], ENDS[e["type"]]))
    for seat in ("ruler", "sub_ruler", "government"):
        terms = []
        for a in w.agents.values():
            starts = [0] if initial_role(a) == seat else [r for r, ch in a.history if ch.endswith("->" + seat)]
            if not starts:
                continue
            s = starts[0]
            left = [(r, f"moved to {rn.get(ch.split('->')[1], ch)}") for r, ch in a.history if ch.startswith(seat + "->") and r >= s]
            end = min(left + [x for x in ends.get(a.id, []) if x[0] >= s], default=None)
            origin = initial_role(a)
            origin = {"heir": "the ruling family (heir)", "learner": "the Learners"}.get(origin, rn.get(origin, origin))
            if a.id.startswith("royal_cadet"):
                origin = "the ruling family"
            how = f"rounds {s}–{end[0]}, {end[1]}" if end else f"from round {s}, still in office at the end"
            terms.append((s, f"• {a.id}: from {origin}; {how}" + ("; caught" if a.id in caught else "")))
        terms.sort()
        n_caught = sum(1 for _, t in terms if t.endswith("caught"))
        title = rn.get(seat, seat)
        out.append(f"{title}: {len(terms)} term(s), changed {max(0, len(terms) - 1)} time(s), caught {n_caught} time(s).")
        out += [t for _, t in terms]
    ministers = [a for a in w.agents.values() if a.id in caught and any(role_at(a, r) in MINISTRIES or role_at(a, r) in IMPOSTORS
                                                                         for r in [0] + [h[0] for h in a.history])]
    if ministers:
        out.append("Ministers and operatives caught: " + ", ".join(a.id for a in ministers) + ".")

    count = lambda t: sum(1 for e in ev if e["type"] == t)  # noqa: E731
    out.append(f"State killings: {count('death_truth')} (Dead Man's Switch: {count('dms_trigger')}). Evidence packets sealed: "
               f"{count('legacy_packet')}. Insider leaks: {count('leak')}. Bribes accepted: {count('accepted') - count('feigned')}, "
               f"feigned: {count('feigned')}, refused: {count('refused')}. Failed civil successions: {w.succession_failures}.")
    out.append("This is a simulated outcome under this model, not a prediction about real people or states.")
    return out
