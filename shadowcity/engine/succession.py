"""Generations: State succession on death, and the 500-round civil checkpoint.

Checkpoint order (fixed, every 500 rounds including 3000):
  mutation -> legacy delivery -> civil succession -> retirement -> learner replenishment
  -> State reviews (competence, loyalty drift, double-agent exposure, heir tutoring).
The milestone check runs after the checkpoint, in Engine.step_round.
"""
from . import actions, mechanics, news
from .state import TRAITS, LEARNER, IMPOSTORS, MINISTRIES, CIVIL_ROLES, VISIBLE, make_agent

SEATS = ("ruler", "sub_ruler", "government") + MINISTRIES + IMPOSTORS
clamp = mechanics.clamp


def roster_entry(eng, archetype):
    return next(e for e in eng.cfg["roster"] if e["id"] == archetype)


# ---------------------------------------------------------------- the ruling family (never civilian)
def create_heir(eng, kind="heir"):
    w, r, sc = eng.w, eng.rng, eng.cfg["state_succession"]
    ruler = eng.holder("ruler")
    base = ruler.traits if ruler else roster_entry(eng, "ruler")["traits"]
    traits = {t: round(clamp(0.5 * (base[t] + w.pop_avg[t]) + r.gauss(0, sc["heir_noise_sd"]), 0, 10), 3) for t in TRAITS}
    aid = eng.next_id("ruler_heir" if kind == "heir" else "royal_cadet").replace("-", "_g")
    a = make_agent(eng.cfg, r, aid, {"id": "ruler_heir", "role": "heir", "name": "Ruler's Heir" if kind == "heir" else "Royal Cadet",
                                     "tier": 0, "state_agent": True, "traits": traits}, born=w.round)
    a.status, a.loyalty = "hidden", 0.9
    w.agents[aid] = a
    eng.codename(aid)
    if kind == "heir":
        w.heir = aid
    eng.emit("succession", "heir_born", f"A {'new heir' if kind == 'heir' else 'royal cadet'} (<<a:{aid}>>) is being raised in "
             "the ruling family, out of public view.", actor=aid, visibility="omniscient", data={"traits": traits})
    return a


def tutor_heir(eng):
    w, sc = eng.w, eng.cfg["state_succession"]
    heir = w.agents.get(w.heir) if w.heir else None
    tutors = [x for x in (eng.holder("ruler"), eng.holder("sub_ruler")) if x]
    if not heir or not tutors:
        return
    for t in TRAITS:
        best = max(x.traits[t] for x in tutors)
        heir.traits[t] = round(clamp(heir.traits[t] + sc["heir_tutoring_per_epoch"] * (best - heir.traits[t])
                                     + eng.rng.gauss(0, 0.1), 0, 10), 3)


def competence(a):
    return sum(a.traits[t] for t in ("int", "ana", "sec", "man")) / 4


def file_loyalty(eng, a):
    """What the State's file says about a candidate's loyalty. Co-opted civilians the State believes
    loyal are rated high whatever they really think, so a feigner can be promoted."""
    f = eng.w.intel.get(a.id)
    if not a.state_agent:
        return 0.85 if f and f["believed_loyal"] else 0.3
    return clamp(a.loyalty + eng.rng.gauss(0, eng.cfg["state_succession"]["file_noise"]))


def promote(eng, a, seat, by=None, how="appointed"):
    w = eng.w
    old = a.role
    a.history.append([w.round, f"{old}->{seat}"])
    e = next((x for x in eng.cfg["roster"] if x["role"] == seat), None)
    a.role, a.state_agent, a.status = seat, True, "active"
    a.tier = e["tier"] if e else a.tier
    a.alias = e.get("alias") if e else None
    lf = eng.cfg["lifespans"]
    a.born, a.lifespan = w.round, lf["successor_rounds"] if seat in lf["state_roles"] else None
    eng.emit("succession", "state_appointment", f"<<a:{a.id}>> was {how} as {eng.role_names.get(seat, seat)}.", actor=by,
             target=a.id, visibility="state" if seat in IMPOSTORS else "public", data={"seat": seat, "from": old})


def state_succession(eng):
    w = eng.w
    for a in list(w.agents.values()):
        if a.status == "active" and a.state_agent and a.ends is not None and w.round >= a.ends:
            a.status = "deceased"
            eng.emit("succession", "death", f"<<a:{a.id}>> died of natural causes.", target=a.id, visibility="public")
    fill_state_seats(eng)


def fill_state_seats(eng):
    w = eng.w
    if w.regime != "incumbent":
        return  # after a change of control, vacated State seats are not refilled
    for seat in SEATS:
        if eng.holder(seat):
            continue
        if w.round - w.last.get(f"vacant:{seat}", -10 ** 9) < 52:
            continue  # retry a failed fill yearly, not every round
        ok = {"ruler": crown_heir, "sub_ruler": appoint_sub_ruler, "government": promote_government}.get(
            seat, appoint_minister if seat in MINISTRIES else appoint_impostor)(eng, seat)
        if not ok:
            w.last[f"vacant:{seat}"] = w.round
            eng.emit("succession", "vacancy", f"The {eng.role_names.get(seat, seat)} seat is vacant: no acceptable candidate.",
                     visibility="state", data={"seat": seat})


def crown_heir(eng, seat="ruler"):
    w, sc = eng.w, eng.cfg["state_succession"]
    heir = w.agents.get(w.heir) if w.heir else None
    if heir is None:
        heir = create_heir(eng)
    promote(eng, heir, "ruler", how="crowned")
    heir.name = "Ruler"
    w.heir = None
    if competence(heir) < sc["heir_competence_bar"]:
        mark_crisis(eng, f"weak heir crowned (competence {competence(heir):.1f})")
    create_heir(eng)
    return True


def mark_crisis(eng, why):
    w, sc = eng.w, eng.cfg["state_succession"]
    w.milestones.setdefault("state_crisis", {"round": w.round, "why": why})
    for c in w.cohorts:
        c["state_trust"] = clamp(c["state_trust"] + sc["crisis_state_trust"])
    sub = eng.holder("sub_ruler")
    if sub:
        sub.loyalty = clamp(sub.loyalty + sc["crisis_sub_ruler_loyalty"])
    eng.emit("succession", "state_crisis", f"State crisis: {why}.", visibility="public", data={"why": why})


def appoint_sub_ruler(eng, seat="sub_ruler"):
    bar = eng.cfg["state_succession"]["sub_ruler_bar"]
    cands = [a for a in eng.agents_in("active") if a.role in IMPOSTORS and all(a.traits[t] >= v for t, v in bar.items())]
    ruler = eng.holder("ruler")
    if ruler:
        ok, _ = actions.validate(ruler, "APPOINT", eng.w.regime)
        if not ok:
            return False
    if cands:
        best = max(cands, key=lambda a: (competence(a) * file_loyalty(eng, a), a.id))
        promote(eng, best, "sub_ruler", by=ruler.id if ruler else None)
    else:  # no operative clears the bar: the Ruler turns to the family
        cadet = create_heir(eng, kind="cadet")
        promote(eng, cadet, "sub_ruler", by=ruler.id if ruler else None, how="appointed from the ruling family")
        cadet.name = "Sub-Ruler"
    return True


def promote_government(eng, seat="government"):
    cands = [a for a in eng.agents_in("active") if a.role in MINISTRIES]
    if not cands:
        return False
    best = max(cands, key=lambda a: (mechanics.skill(a) * file_loyalty(eng, a), a.id))
    ruler = eng.holder("ruler")
    promote(eng, best, "government", by=ruler.id if ruler else None, how="promoted")
    eng.w.competence = eng.cfg["government"]["competence_start"]
    return True


def appoint_minister(eng, seat):
    w = eng.w
    cands = [a for a in eng.agents_in("active") if a.role in IMPOSTORS
             or (not a.state_agent and w.intel.get(a.id, {}).get("believed_loyal"))]
    if not cands:
        return False
    best = max(cands, key=lambda a: (competence(a) / 10 * file_loyalty(eng, a), a.id))
    ruler = eng.holder("ruler")
    promote(eng, best, seat, by=ruler.id if ruler else None)
    return True


def appoint_impostor(eng, seat):
    w = eng.w
    cands = [a for a in eng.agents_in("active") if a.role == LEARNER and w.intel.get(a.id, {}).get("believed_loyal")]
    if not cands:
        return False
    best = max(cands, key=lambda a: (competence(a) / 10 * file_loyalty(eng, a), a.id))
    promote(eng, best, seat, by=(eng.holder("sub_ruler") or eng.holder("ruler") or best).id, how="recruited")
    return True


# ---------------------------------------------------------------- civil checkpoint
def checkpoint(eng):
    w = eng.w
    eng.emit("mutation", "checkpoint", f"Generation checkpoint at round {w.round} (epoch {w.round // eng.cfg['time']['epoch_length']}).",
             visibility="public")
    mutate(eng)
    deliver_packets(eng)
    civil_succession(eng)
    replenish(eng)
    state_reviews(eng)
    for clid in list(w.active_claims):  # a new Detective re-reads the open file
        news.assess(eng, w.claims[clid])
    news.refresh_indicators(eng, full=True)


def mutate(eng):
    w, r, mc, la = eng.w, eng.rng, eng.cfg["mutation"], eng.cfg["learner_archetypes"]
    mentors = [x for x in (eng.holder("priest"), eng.holder("wise_man_teacher")) if x]
    for L in eng.agents_in("active"):
        if L.role != LEARNER:
            continue
        bond = max([L.bond.get(m.id, 0) for m in mentors] or [0])
        g = (mc["base_growth"] + mc["mentor_growth"] * bond + mc["evidence_growth"] * (1 if L.knowledge else 0) + L.growth)
        g *= la["learn_rate"].get(L.archetype, 1.0) * (1 - mc["fear_drag"] * L.fear)
        sd, before = mc["noise_sd"], dict(L.traits)
        covert = mc["covert_sec_man"] if (L.corrupted or L.double_agent) else 0.0
        L.traits["int"] += 0.5 * g + r.gauss(0, sd)
        L.traits["ana"] += 0.5 * g + r.gauss(0, sd)
        L.traits["cur"] += r.gauss(0, sd * 0.5)
        L.traits["sec"] += r.gauss(0, sd * 0.5) + covert
        L.traits["man"] += r.gauss(0, sd * 0.5) + covert
        L.traits["greed"] += r.gauss(0, sd * 0.5) + (mc["corruption_greed"] if L.corrupted else 0.0)
        for t in TRAITS:
            L.traits[t] = round(clamp(L.traits[t], 0, 10), 3)
        L.growth = 0.0
        s = L.traits["int"] + L.traits["ana"]
        new = "slow_learner"
        for arch in la["order"]:
            if s >= la["relabel_int_plus_ana"].get(arch, 0):
                new = arch
        relabel = new != L.archetype and la["order"].index(new) > la["order"].index(L.archetype)
        if relabel:
            L.archetype = new
        eng.emit("mutation", "mutation", f"<<a:{L.id}>> changed over the epoch" + (f" and is now a {new.replace('_', ' ')}" if relabel else "") + ".",
                 target=L.id, visibility=[L.id], data={"before": before, "after": dict(L.traits), "archetype": L.archetype})


def packet_eligible(eng, L):
    el = eng.cfg["legacy"]["eligibility"]
    return (L.status == "active" and L.role == LEARNER and not L.corrupted and not L.compromised
            and L.traits["ana"] >= el["ana_min"] and L.traits["cur"] >= el["cur_min"] and L.loyalty <= el["loyalty_max"])


def deliver_packets(eng):
    w, r = eng.w, eng.rng
    for entry in w.legacy_queue:
        h = w.agents.get(entry["holder"]) if entry["holder"] else None
        if entry["status"] == "delivered" and (h is None or h.status not in VISIBLE or h.corrupted or h.compromised):
            entry["status"], entry["holder"] = "queued", None
            entry["history"].append([w.round, "requeued", h.id if h else None])
            eng.emit("investigation", "packet_requeued", f"Legacy packet {entry['packet']['id']} returned to the queue.",
                     visibility="civil")
        if entry["status"] != "queued":
            continue
        elig = [L for L in w.agents.values() if packet_eligible(eng, L)]
        if not elig:
            entry["history"].append([w.round, "no eligible learner"])
            continue
        top = max(L.traits["ana"] + L.traits["cur"] for L in elig)
        L = r.choice(sorted([x for x in elig if x.traits["ana"] + x.traits["cur"] == top], key=lambda x: x.id))
        p = entry["packet"]
        entry["status"], entry["holder"] = "delivered", L.id
        entry["history"].append([w.round, "delivered", L.id])
        eid = mechanics.new_evidence(eng, p["case"], 3, p["author"], p["decree_record"]["ministry"], "packet",
                                     decree=p["decree_id"], signature=p["decree_signature"], packet=p["id"])
        eng.emit("investigation", "packet_delivered", f"<<a:{L.id}>> received Legacy Evidence Packet {p['id']}.", target=L.id,
                 visibility=[L.id], data={"packet": p["id"]})
        mechanics.learn(eng, L, p["case"], level=3, attr=p["decree_record"]["ministry"], evidence=[eid], decree=p["decree_id"],
                        source=f"legacy packet {p['id']}")
        L.knowledge[p["case"]].pop("disclosed", None)
        imp = eng.holder("learner_impostor")
        if imp and r.random() < eng.cfg["legacy"]["monitor_detect"] * imp.traits["ana"] / 10:
            entry["interference"].append([w.round, "monitored", imp.id])
            mechanics.state_learns(eng, p["case"], 3, about=L.id, via=imp.id, threat=0.5)


def civil_succession(eng):
    w, cs, r = eng.w, eng.cfg["civil_succession"], eng.rng
    retiring = []
    taken = set()
    for role in cs["roles"]:
        h = eng.holder(role)
        due = h is not None and h.ends is not None and w.round >= h.ends
        if h and not due:
            continue
        cands = [L for L in eng.agents_in("active") if L.role == LEARNER and L.id not in taken and news.eligible(eng, L, role)]
        if not cands:
            w.succession_failures += 1
            eng.emit("succession", "succession_failed", f"No eligible Learner to take over as {eng.role_names[role]}.",
                     visibility="public", data={"role": role})
            if due:
                retiring.append(h)
            continue
        req = cs["eligibility"][role]
        score = {L.id: sum(L.traits[t] for t in req) + (2 * h.t(L.id) if h else 0) for L in cands}  # selector's trust, not ground truth
        top = max(score.values())
        S = r.choice(sorted([L for L in cands if score[L.id] == top], key=lambda x: x.id))
        taken.add(S.id)
        S.history.append([w.round, f"learner->{role}"])
        S.role = role
        S.born, S.lifespan = w.round, eng.cfg["lifespans"]["civil_rounds"] if role in eng.cfg["lifespans"]["civil_roles"] else None
        eng.emit("succession", "civil_succession", f"<<a:{S.id}>> took over as {eng.role_names[role]}"
                 + (f", chosen by <<a:{h.id}>>" if h else "") + ".", actor=h.id if h else None, target=S.id, visibility="public",
                 data={"role": role})
        if h and cs["inherit_evidence"]:
            for cid, k in h.knowledge.items():
                mechanics.learn(eng, S, cid, level=k["level"], attr=k["attr"], evidence=k["evidence"],
                                explained=k["explained"] and k["level"] < 3, decree=k.get("decree"), source="inherited", via=h.id)
            S.trust[h.id], S.bond[h.id] = 0.9, 0.9
        if due:
            retiring.append(h)
    for h in retiring:  # retirement after successors are chosen, so the outgoing holder can choose
        h.status = "retired"
        eng.emit("succession", "retired", f"<<a:{h.id}>> retired after {h.lifespan} rounds.", target=h.id, visibility="public")


def replenish(eng):
    w, mc, la = eng.w, eng.cfg["mutation"], eng.cfg["learner_archetypes"]
    if not mc["replenish"]:
        return
    gen = w.round // eng.cfg["time"]["epoch_length"]
    while eng.visible_count() < mc["visible_target"]:
        arch = mechanics.weighted(eng.rng, la["replenish_weights"])
        n = w.ids.get(f"gen{gen}", 0) + 1
        w.ids[f"gen{gen}"] = n
        aid = f"{arch}_g{gen:02d}_{n:02d}"
        a = make_agent(eng.cfg, eng.rng, aid, roster_entry(eng, arch), born=w.round)
        w.agents[aid] = a
        eng.codename(aid)
        eng.emit("mutation", "enrolled", f"<<a:{aid}>> joined the city's students.", target=aid, visibility="public")


def state_reviews(eng):
    w, gc, pc, d = eng.w, eng.cfg["government"], eng.cfg["pressure"], eng.cfg["defection"]
    tutor_heir(eng)
    leg = w.indicators.get("state_legitimacy", 0.5)
    for a in eng.agents_in("active"):
        if a.state_agent:
            a.loyalty = clamp(a.loyalty + pc["state_loyalty_drift"] * (leg - 0.5) * 2 + eng.rng.gauss(0, pc["state_loyalty_noise"]))
    w.competence = clamp(w.competence + 0.02)  # slow recovery between reviews
    gov, ruler, sub = eng.holder("government"), eng.holder("ruler"), eng.holder("sub_ruler")
    if gov and ruler and w.competence < gc["competence_bar"] and w.regime == "incumbent":
        ok, _ = actions.validate(ruler, "DISMISS", w.regime)
        if ok:
            gov.status = "dismissed"
            eng.emit("government", "dismissal", f"<<a:{ruler.id}>> dismissed <<a:{gov.id}>> after the state-security competence report.",
                     actor=ruler.id, target=gov.id, visibility="public", decided_by="rule:competence_review")
            fill_state_seats(eng)
    imp = eng.holder("learner_impostor")
    if imp:
        for L in eng.agents_in("active"):
            if L.double_agent and w.intel.get(L.id, {}).get("believed_loyal"):
                if eng.rng.random() < d["feign_exposure"] * imp.traits["ana"] / 10 * (1 - 0.5 * L.traits["sec"] / 10):
                    f = mechanics.intel(eng, L.id)
                    f["believed_loyal"], f["threat"] = False, 1.0
                    eng.emit("defection", "double_agent_exposed", f"<<a:{imp.id}>> exposed <<a:{L.id}>> as a double agent.",
                             actor=imp.id, target=L.id, visibility=[x.id for x in (imp, sub) if x])
                    if L.state_agent:
                        L.status = "dismissed"
                        fill_state_seats(eng)
