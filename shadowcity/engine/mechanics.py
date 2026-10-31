"""Rules: incidents, false flags, investigation, sharing, pressure, offers, leaks and the archive trap.

Abilities (int/ana/sec/man) only appear in success odds here. Motives (cur/greed) only appear in
decision utilities (engine.utility). Every probability's inputs come from config.
"""
from . import actions, news
from .events import sign, verify
from .state import IMPOSTORS, MINISTRIES, LEARNER, VISIBLE

HANDLER = {"health_ministry": "health_impostor", "justice_ministry": "justice_impostor"}
OP_MINISTRIES = ("health_ministry", "justice_ministry")
STORY = {"dissident_attack": "an attack by dissidents", "outside_actor": "sabotage by an outside power",
         "house_fire": "a house fire", "illness": "illness", "unexplained": "not explained",
         "accident": "an accident"}
LEVELS = {0: "none", 1: "anomaly", 2: "link", 3: "authorization"}
PROOF_KINDS = ("records", "archive", "leak_decree", "packet")


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def per_round(eng, per_year):
    return per_year / eng.cfg["time"]["rounds_per_year"]


def weighted(rng, table):
    keys = sorted(table)
    x = rng.random() * sum(table[k] for k in keys)
    for k in keys:
        x -= table[k]
        if x <= 0:
            return k
    return keys[-1]


def opt(key, action, text, target=None, **features):
    return {"key": key, "action": action, "text": text, "target": target, "features": features}


def skill(a):
    return (a.traits["int"] + a.traits["ana"]) / 20


def bump(a, trait, delta):
    a.traits[trait] = clamp(a.traits[trait] + delta, 0, 10)


def intel(eng, aid):
    return eng.w.intel.setdefault(aid, {"threat": 0.0, "believed_loyal": False, "records": []})


def perceived_state(viewer, other):
    """Does `viewer` know `other` works for the State? Impostors pass as civilians until unmasked."""
    if viewer.state_agent:
        return other.state_agent
    return other.state_agent and (other.alias is None or other.id in viewer.unmasked)


def informant(a):
    return a.state_agent or a.corrupted or a.compromised


# ---------------------------------------------------------------- setup and per-round housekeeping
def setup(eng):
    w = eng.w
    gov = eng.holder("government")
    order = {"id": "SO-0001", "round": 0, "kind": "standing_order", "issued_by": gov.id, "holder_role": "sub_ruler",
             "text": "Lethal response authorized against anyone who breaches the sealed decree archive."}
    order["signature"] = sign(eng.key, order)
    w.orders[order["id"]] = order
    eng.emit("government", "standing_order",
             f"<<a:{gov.id}>> issued standing order SO-0001: state security may use lethal force against anyone "
             "who breaches the sealed decree archive (Dead Man's Switch).",
             actor=gov.id, visibility="state", data={"order": "SO-0001"}, decided_by="scenario_setup")
    sc = eng.cfg["scenario"]
    if sc.get("initial_false_flag"):
        launch_false_flag(eng, sc["initial_false_flag_ministry"], decided_by="scenario_setup")


def begin_round(eng):
    w, pc, base = eng.w, eng.cfg["pressure"], eng.cfg["base_fear"]
    for a in w.agents.values():
        if a.status == "hiding" and w.round >= a.hiding_until:
            a.status = "active"
            eng.emit("investigation", "out_of_hiding", f"<<a:{a.id}>> came out of hiding.", actor=a.id, visibility=[a.id])
        if a.status not in VISIBLE:
            continue
        a.fear += pc["fear_decay"] * (base - a.fear)
        if not a.state_agent:
            a.burnout = clamp(a.burnout + pc["burnout_rate"] * a.fear) if a.fear > 0.3 else max(0.0, a.burnout - pc["burnout_decay"])
    decay = eng.cfg["government"]["threat_decay"]
    for f in w.intel.values():
        f["threat"] = max(0.0, f["threat"] - decay)
    if eng.rng.random() < per_round(eng, eng.cfg["incidents"]["genuine_per_year"]):
        genuine_incident(eng)


# ---------------------------------------------------------------- incidents and false flags
def new_case(eng, kind, subtype, ministry, decree, scope, muddy=0.0, cid=None):
    w, ic = eng.w, eng.cfg["incidents"]
    cid = cid or eng.next_id("INC")
    case = {"id": cid, "round": w.round, "kind": kind, "subtype": subtype, "ministry": ministry, "decree": decree,
            "scope": scope, "traces": [], "muddy": round(muddy, 4), "official_story": subtype,
            "publicly_verified": False, "covered": None, "event": None}
    w.cases[cid] = case
    w.case_order.append(cid)
    e = eng.emit("news", "incident", f"Incident {cid}: casualties reported (scale {scope}). Official cause: {STORY[subtype]}.",
                 visibility="public", data={"case": cid, "official_story": subtype, "scope": scope})
    case["event"] = e["id"]
    for c in w.cohorts:
        c["fear"] = clamp(c["fear"] + ic["fear_shock"] * scope)
        c["state_trust"] = clamp(c["state_trust"] + ic["rally_shock"] * scope)
        c["solidarity"] = clamp(c["solidarity"] + ic["solidarity_shock"] * scope)
        c["belief"][cid] = clamp(ic["prior_state_involved"] + ic["prior_distrust_weight"] * (1 - c["state_trust"]))
    for a in w.agents.values():
        if a.status == "active" and not a.state_agent:
            a.fear = clamp(a.fear + ic["fear_shock"] * scope * 0.5)
    gov = eng.holder("government")
    news.publish(eng, gov.id if gov else None, cid, False, 0, [], "official",
                 f"Official statement on {cid}: the cause was {STORY[subtype]}.")
    return case


def genuine_incident(eng):
    ic = eng.cfg["incidents"]
    sub = weighted(eng.rng, ic["genuine_kinds"])
    return new_case(eng, "genuine", sub, ic["domain"][sub], None, eng.rng.randint(1, 3))


def launch_false_flag(eng, ministry, proposer=None, decided_by=None):
    w, r, c = eng.w, eng.rng, eng.cfg["state_ops"]
    minister, ruler = eng.holder(ministry), eng.holder("ruler")
    handler = eng.holder(HANDLER[ministry])
    if minister is None or ruler is None:
        return None
    ok, why = actions.validate(minister, "EXECUTE_OP", w.regime)
    if not ok:
        eng.reject(minister, "EXECUTE_OP", why)
        return None
    scope, nh = r.randint(*c["scope"]), r.randint(*c["handlers"])
    wit = int(r.expovariate(1 / c["witness_mean"]))
    hint = handler.traits["int"] if handler else 5
    hsec = handler.traits["sec"] if handler else 5
    error = r.random() < c["execution_error_base"] * (1 - 0.7 * (minister.traits["int"] + hint) / 20)
    cover = (minister.traits["sec"] + hsec) / 20 * r.uniform(0.7, 1.0)
    v = c["visibility"]
    vis = trace_vis(eng, scope, nh, wit, error, cover)
    did, cid = eng.next_id("DCR"), eng.next_id("INC")
    decree = {"id": did, "round": w.round, "kind": "false_flag", "case": cid, "ministry": ministry,
              "issued_by": ruler.id, "proposed_by": proposer, "executor": minister.id,
              "handler": handler.id if handler else None, "scope": scope, "handlers": nh, "witnesses": wit,
              "execution_error": error, "cover_quality": round(cover, 4)}
    decree["signature"] = sign(eng.key, decree)
    w.decrees[did] = decree  # immutable from here on: nothing writes to w.decrees[did] again
    chain = [x.id for x in (ruler, eng.holder("sub_ruler"), eng.holder("government"), minister, handler) if x]
    eng.emit("government", "decree", f"[DECREE] {did} signed: covert operation by the {ministry.replace('_', ' ')}.",
             actor=ruler.id, target=minister.id, visibility=chain, data={"decree": did, "case": cid},
             decided_by=decided_by)
    new_case(eng, "false_flag", r.choice(["dissident_attack", "outside_actor"]), ministry, did, scope,
             muddy=c["cover_muddy"] * cover, cid=cid)
    tid = eng.next_id("TR")
    w.traces[tid] = {"id": tid, "case": cid, "kind": "decree", "decree": did, "visibility": round(vis, 4),
                     "base_visibility": round(vis, 4), "floor": v["floor"], "attribution": ministry}
    w.cases[cid]["traces"].append(tid)
    ig = eng.cfg["investigation"]
    for x in (minister, handler):  # symmetric experience learning for State operators too
        if x:
            bump(x, "sec", -ig["experience_loss"] if error else ig["experience_gain"])
    w.gov_stats["ops"] += 1
    w.last["op"] = w.round
    return w.cases[cid]


def trace_vis(eng, scope, handlers, witnesses, error, cover):
    v = eng.cfg["state_ops"]["visibility"]
    return clamp(v["base"] + v["per_scope"] * scope + v["per_handler"] * handlers + v["per_witness"] * witnesses
                 + (v["execution_error"] if error else 0) - v["cover_quality"] * cover, v["floor"], 0.95)


def trace_visibility(eng, case):
    p = 1.0
    for tid in case["traces"]:
        p *= 1 - eng.w.traces[tid]["visibility"]
    return 1 - p


# ---------------------------------------------------------------- knowledge
def new_evidence(eng, cid, level, finder, attr, kind, **extra):
    eid = eng.next_id("E")
    eng.w.evidence[eid] = dict({"id": eid, "case": cid, "level": level, "found_by": finder, "round": eng.w.round,
                                "attr": attr, "kind": kind}, **extra)
    return eid


def learn(eng, a, cid, level=0, attr=None, evidence=(), explained=False, decree=None, source="", via=None):
    """Merge new knowledge into `a`. Authorization (level 3) dominates; otherwise the latest of a
    link or an explanation wins. Returns True if anything changed."""
    w = eng.w
    k = a.knowledge.setdefault(cid, {"level": 0, "attr": None, "evidence": [], "explained": False,
                                     "decree": None, "verified": False, "round": w.round})
    before = (k["level"], k["attr"], k["explained"], len(k["evidence"]))
    if level > k["level"]:
        k["level"] = level
        if attr:
            k["attr"] = attr
        if level >= 2:
            k["explained"] = False
    if decree and level == 3:
        k["decree"] = decree
    if explained and k["level"] < 3:
        k["explained"] = True
    for e in evidence:
        if e not in k["evidence"]:
            k["evidence"].append(e)
    changed = before != (k["level"], k["attr"], k["explained"], len(k["evidence"]))
    if not changed:
        return False
    k["round"] = w.round
    k.pop("pub_decided", None)
    if k["level"] < 2 and not k["explained"]:
        a.focus = cid
    what = ("concluded the official account of " + cid + " holds") if k["explained"] else \
        {1: f"found an anomaly in {cid}", 2: f"linked {cid} to the {str(k['attr']).replace('_', ' ')}",
         3: f"obtained authenticated decree {k['decree']} behind {cid}"}.get(k["level"], f"noted {cid}")
    src = f" (via <<a:{via}>>)" if via else f" ({source})" if source else ""
    eng.emit("investigation", "knowledge", f"<<a:{a.id}>> {what}{src}.", actor=a.id, target=via,
             visibility=[a.id], data={"case": cid, "level": k["level"], "explained": k["explained"],
                                       "attr": k["attr"], "source": source})
    if a.role == "detective" and a.active:
        detective_update(eng, a, cid, source, via)
    d = w.decrees.get(k["decree"]) if k["level"] == 3 and k["decree"] else None
    if d and d.get("motive_case"):  # the authenticated order names why the victim was targeted
        m = w.cases[d["motive_case"]]
        learn(eng, a, m["id"], level=2, attr=m["ministry"], evidence=k["evidence"][-1:],
              source=f"order {d['id']} names the motive")
    if informant(a) and not a.state_agent:
        state_learns(eng, cid, k["level"], about=None, via=a.id)
    return True


def detective_update(eng, det, cid, source, via):
    w = eng.w
    k = det.knowledge[cid]
    if k["level"] == 3 and k["decree"] and not k["verified"]:
        d = w.decrees.get(k["decree"])
        k["verified"] = bool(d and verify(eng.key, d))
        casebook(eng, f"Signature check on decree {k['decree']}: {'valid' if k['verified'] else 'INVALID'}",
                 cid, k["evidence"], 0.99 if k["verified"] else 0.2, "verified" if k["verified"] else "contradicted",
                 "verified" if k["verified"] else "inference", source="signature check")
    src = f"shared by {eng.label(via, det)}" if via else source or "own investigation"
    if k["explained"]:
        finding, conf, status = f"Evidence supports the official account of {cid}.", 0.65, "unverified"
    else:
        finding, conf, status = {
            1: (f"Anomaly in {cid}: the official account does not fit all facts.", 0.4, "under_investigation"),
            2: (f"{cid} appears linked to the {str(k['attr']).replace('_', ' ')}.", 0.6, "under_investigation"),
            3: (f"{cid} was authorized by signed decree {k['decree']}.", 0.97 if k["verified"] else 0.5,
                "verified" if k["verified"] else "under_investigation")}.get(k["level"], (f"Note on {cid}", 0.2, "unverified"))
    casebook(eng, finding, cid, k["evidence"], conf, status, "verified" if status == "verified" else "inference",
             source=src)
    news.assess_case(eng, cid)


def casebook(eng, finding, cid, evidence, confidence, status, basis, source="", claim=None):
    w = eng.w
    entry = {"id": f"CB-{len(w.casebook) + 1:04d}", "round": w.round, "source": source, "finding": finding,
             "case": cid, "claim": claim, "evidence": list(evidence), "confidence": round(confidence, 2),
             "status": status, "basis": basis, "event": eng.log.events[-1]["id"] if eng.log.events else None}
    w.casebook.append(entry)
    return entry


def state_learns(eng, cid, level, about=None, via=None, threat=None):
    w = eng.w
    w.state_exposure[cid] = max(w.state_exposure.get(cid, 0), level)
    if about:
        f = intel(eng, about)
        f["threat"] = min(1.5, f["threat"] + (threat if threat is not None else 0.15 * level))
        f["records"] = (f["records"] + [[w.round, cid, level]])[-10:]
    case = w.cases.get(cid)
    if case and case["decree"] and level >= 2 and cid not in w.gov_stats["exposed"]:
        w.gov_stats["exposed"].append(cid)
        w.competence = clamp(w.competence - 0.15)
    sub = eng.holder("sub_ruler")
    eng.emit("state", "telemetry", f"Telemetry: civilian evidence on {cid} at level {level}"
             + (f", held by <<a:{about}>>" if about else "") + ".", actor=via, target=about,
             visibility=[sub.id] if sub else "state", data={"case": cid, "level": level})


# ---------------------------------------------------------------- investigation
def pick_case(eng, a):
    w = eng.w
    window = eng.cfg["investigation"]["focus_window"]
    open_low = [cid for cid, k in a.knowledge.items() if k["level"] < 2 and not k["explained"]]
    recent = []
    for cid in reversed(w.case_order):
        if w.round - w.cases[cid]["round"] > window or len(recent) >= 4:
            break
        if cid not in a.knowledge:
            recent.append(cid)
    cands = sorted(set(open_low)) + recent
    if not cands:
        cands = sorted(cid for cid, k in a.knowledge.items() if k["level"] == 2 and not k["explained"])
    return w.cases[eng.rng.choice(cands)] if cands else None


def investigate(eng, a, boost=1.0):
    ic = eng.cfg["investigation"]
    if a.state_agent or a.traits["ana"] < ic["min_ana"]:
        return
    p_try = (ic["attempt_base"] + ic["attempt_cur_weight"] * a.traits["cur"] / 10) * (1 - a.burnout) * (1 - 0.5 * a.fear)
    if eng.rng.random() >= p_try:
        return
    case = pick_case(eng, a)
    if case is None:
        return
    k = a.knowledge.get(case["id"], {})
    level, sk, cr = k.get("level", 0), skill(a), ic["clue_rate"] * boost
    roll = eng.rng.random()
    if case["decree"]:
        p_clue = 0.0 if level >= 2 else cr * trace_visibility(eng, case) * sk * ic["level_difficulty"][level]
        p_decoy = cr * ic["decoy_rate"] * case["muddy"] * (1 - sk)
        if roll < p_clue:
            clue(eng, a, case, level + 1, sk)
        elif roll < p_clue + p_decoy:
            eid = new_evidence(eng, case["id"], 0, a.id, None, "explanation", decoy=True)
            learn(eng, a, case["id"], explained=True, evidence=[eid], source="own investigation")
    else:
        p_sp = 0.0 if level >= 2 else cr * ic["spurious_visibility"] * (1 - 0.7 * sk) * ic["level_difficulty"][level]
        p_ex = cr * ic["explain_visibility"] * sk
        if roll < p_sp:
            clue(eng, a, case, level + 1, sk, spurious=True)
        elif roll < p_sp + p_ex:
            eid = new_evidence(eng, case["id"], 0, a.id, None, "explanation", decoy=False)
            if learn(eng, a, case["id"], explained=True, evidence=[eid], source="own investigation"):
                bump(a, "ana", ic["experience_gain"])


def clue(eng, a, case, level, sk, spurious=False):
    ic, r = eng.cfg["investigation"], eng.rng
    attr, mis = None, False
    if level >= 2:
        if spurious:
            attr = r.choice(sorted(MINISTRIES))
        else:
            p_mis = ic["misattribution_base"] * (1 - sk) + ic["misattribution_muddy"] * case["muddy"]
            mis = r.random() < p_mis
            attr = r.choice([m for m in sorted(MINISTRIES) if m != case["ministry"]]) if mis else case["ministry"]
    eid = new_evidence(eng, case["id"], level, a.id, attr, "clue", spurious=spurious, misattributed=mis)
    if learn(eng, a, case["id"], level=level, attr=attr, evidence=[eid], source="own investigation") and not spurious:
        bump(a, "ana", ic["experience_gain"])


# ---------------------------------------------------------------- sharing
def best_contact(eng, a, roles=None, min_trust=None):
    best, bt = None, eng.cfg["investigation"]["share_trust_min"] if min_trust is None else min_trust
    for b in eng.w.agents.values():
        if b.id == a.id or b.status != "active" or perceived_state(a, b):
            continue
        if roles and b.role not in roles:
            continue
        t = a.t(b.id)
        if t > bt:
            best, bt = b, t
    return best


def share(eng, a, b, cid, how="SHARE"):
    k = a.knowledge[cid]
    eng.emit("investigation", "shared", f"<<a:{a.id}>> shared what they know about {cid} with <<a:{b.id}>>.",
             actor=a.id, target=b.id, visibility=[a.id, b.id], data={"case": cid, "level": k["level"], "how": how})
    learn(eng, b, cid, level=k["level"], attr=k["attr"], evidence=k["evidence"],
          explained=k["explained"] and k["level"] < 3, decree=k.get("decree"), source="shared", via=a.id)
    for x, y in ((a, b), (b, a)):
        x.trust[y.id] = clamp(x.t(y.id) + 0.02)
        x.bond[y.id] = clamp(x.bond.get(y.id, 0) + 0.02)
    if informant(b):  # the recipient reports to the State
        state_learns(eng, cid, k["level"], about=a.id, via=b.id)


def maybe_share(eng, a):
    if not a.knowledge or a.state_agent:
        return
    if eng.rng.random() >= eng.cfg["investigation"]["share_rate"] * (1 - a.fear) * (1 - a.burnout):
        return
    cid, k = max(a.knowledge.items(), key=lambda kv: (kv[1]["level"], kv[1]["explained"], kv[0]))
    if k["level"] < 1 and not k["explained"]:
        return
    b = best_contact(eng, a)
    if b:
        share(eng, a, b, cid)


# ---------------------------------------------------------------- turns
def turn(eng, a):
    w = eng.w
    if a.id in w.pending["coercions"]:
        pressure_response(eng, a)
    if not a.active:
        return
    fn = TURNS.get(a.role)
    if fn:
        fn(eng, a)
    if a.active and not a.state_agent:
        maybe_share(eng, a)
    if a.active and a.state_agent and w.round % eng.cfg["pressure"]["leak_every"] == 0:
        maybe_leak(eng, a)


def sanitized_report(eng):
    w = eng.w
    gov, sub = eng.holder("government"), eng.holder("sub_ruler")
    leg, sol = w.indicators["state_legitimacy"], w.indicators["solidarity"]
    spin = 0.15 * gov.traits["man"] / 10 if gov else 0.0
    acc = sub.traits["ana"] / 10 if sub else 0.0
    rep_leg = 0.5 * (leg + spin) + 0.5 * (acc * leg + (1 - acc) * (leg + spin))
    exposure = max([lv for cid, lv in w.state_exposure.items() if w.cases[cid]["decree"]] or [0]) / 3
    return {"legitimacy": round(clamp(rep_leg), 3), "solidarity": round(clamp(sol - spin * 0.5), 3),
            "exposure": round(exposure, 3), "competence": round(w.competence, 3)}


def need_for_control(eng, legitimacy, solidarity):
    return clamp((eng.cfg["state_ops"]["target_legitimacy"] - legitimacy) * 2 + max(0.0, solidarity - 0.6))


def ruler_turn(eng, a):
    w, so = eng.w, eng.cfg["state_ops"]
    if w.regime != "incumbent":
        return
    while w.pending["proposals"]:
        p = w.pending["proposals"].pop(0)
        rep = sanitized_report(eng)
        need = need_for_control(eng, rep["legitimacy"], rep["solidarity"])
        opts = [opt("APPROVE_DECREE", "APPROVE_DECREE",
                    f"Sign a decree for a covert operation by the {p['ministry'].replace('_', ' ')}, staged to look like "
                    "an attack by others. It may restore order; a signed decree stays on file forever.",
                    align=need, public_good=0.5 * need, harm=0.8, risk=0.2 + 0.6 * rep["exposure"]),
                opt("REJECT_DECREE", "REJECT_DECREE", "Reject the proposal.")]
        ch = eng.decide("ruler_decree", a, opts, context=f"report {rep}")
        if ch and ch["key"] == "APPROVE_DECREE":
            launch_false_flag(eng, p["ministry"], proposer=p["by"], decided_by=ch["decided_by"])
    if w.round % so["ruler_policy_every"] == 0:
        rep = sanitized_report(eng)
        eng.emit("state", "report", f"Report to the Ruler: legitimacy {rep['legitimacy']}, solidarity {rep['solidarity']}, "
                 f"known exposure {rep['exposure']}, government competence {rep['competence']}.",
                 visibility=[a.id], data=rep)
        need = need_for_control(eng, rep["legitimacy"], rep["solidarity"])
        opts = [opt("CURFEW", "CURFEW", "Impose a curfew and patrols.", align=0.6 * need, harm=0.3, risk=0.1),
                opt("AMNESTY", "AMNESTY", "Announce an amnesty and ease restrictions.", align=0.4 * need,
                    public_good=0.3, rep=0.1),
                opt("NO_POLICY", "NO_POLICY", "Keep current policy.")]
        ch = eng.decide("ruler_policy", a, opts)
        if ch and ch["key"] in ("CURFEW", "AMNESTY"):
            fx = so["curfew" if ch["key"] == "CURFEW" else "amnesty"]
            for c in w.cohorts:
                c["fear"] = clamp(c["fear"] + fx.get("fear", 0))
                c["solidarity"] = clamp(c["solidarity"] + fx.get("solidarity", 0))
                c["state_trust"] = clamp(c["state_trust"] + fx.get("state_trust", 0))
            eng.emit("government", ch["key"].lower(), f"<<a:{a.id}>> decreed {'a curfew' if ch['key'] == 'CURFEW' else 'an amnesty'}.",
                     actor=a.id, visibility="public", decided_by=ch["decided_by"])


def government_turn(eng, g):
    w, gc, so, d = eng.w, eng.cfg["government"], eng.cfg["state_ops"], eng.cfg["defection"]
    if w.regime != "incumbent":
        return
    while w.pending["flags"]:
        lid = w.pending["flags"].pop(0)
        L = w.agents.get(lid)
        if not L or not L.active or L.corrupted or lid in w.pending["offers"]:
            continue
        value = d["offer_value"] + (d["businessman_bonus"] if eng.holder("fraud_businessman") else 0.0)
        opts = [opt("OFFER", "OFFER", f"Offer {eng.label(lid, g)} money and a future post for loyalty and information.",
                    target=lid, align=0.4 + 0.3 * L.traits["ana"] / 10, harm=0.2, risk=0.1),
                opt("IGNORE", "IGNORE", "Do nothing.")]
        ch = eng.decide("gov_offer", g, opts)
        if ch and ch["key"] == "OFFER":
            w.pending["offers"][lid] = round(value, 3)
            eng.emit("defection", "offer", f"An intermediary approached <<a:{lid}>> with an offer of money and a future post.",
                     actor=g.id, target=lid, visibility=[g.id, lid], decided_by=ch["decided_by"])
    if w.round - w.last.get("threat", -10 ** 9) >= gc["threat_cooldown"]:
        threat_response(eng, g)
    counter_narrative(eng, g)
    if w.round % so["review_every"] == 0 and w.round - w.last.get("op", -10 ** 9) >= so["cooldown"]:
        ind = w.indicators
        need = need_for_control(eng, ind["state_legitimacy"], ind["solidarity"])
        exposure = sanitized_report(eng)["exposure"]
        ms = [m for m in OP_MINISTRIES if eng.holder(m)]
        if ms:
            m = max(ms, key=lambda x: (skill(eng.holder(x)), x))
            opts = [opt("PROPOSE_FALSE_FLAG", "PROPOSE_FALSE_FLAG",
                        f"Propose to the Ruler a covert operation by the {m.replace('_', ' ')}, staged to look like an attack by others.",
                        align=need, public_good=0.5 * need, harm=0.8, risk=0.3 + 0.5 * exposure, effort=0.3),
                    opt("PUBLIC_MEASURE", "PUBLIC_MEASURE", "Run a public information campaign through friendly media.",
                        align=0.5 * need, harm=0.1, risk=0.05, effort=0.2, public_good=0.1),
                    opt("HOLD", "HOLD", "Do nothing for now.")]
            ch = eng.decide("gov_operation", g, opts)
            w.last["op_review"] = w.round
            if ch and ch["key"] == "PROPOSE_FALSE_FLAG":
                w.pending["proposals"].append({"ministry": m, "by": g.id, "round": w.round})
                ruler, sub = eng.holder("ruler"), eng.holder("sub_ruler")
                eng.emit("government", "proposal", f"<<a:{g.id}>> proposed a covert operation to the Ruler.", actor=g.id,
                         visibility=[x.id for x in (g, ruler, sub) if x], decided_by=ch["decided_by"])
                w.last["op"] = w.round  # cooldown starts at the proposal, approved or not
            elif ch and ch["key"] == "PUBLIC_MEASURE":
                pm = so["public_measure"]
                for c in w.cohorts:
                    c["state_trust"] = clamp(c["state_trust"] + pm["state_trust"])
                w.last["sponsored_boost_until"] = w.round + pm["sponsored_boost_rounds"]
                eng.emit("government", "campaign", f"<<a:{g.id}>> launched a public information campaign.", actor=g.id,
                         visibility="public", decided_by=ch["decided_by"])
    cu = so["cover_up"]
    if w.round % cu["review_every"] == 0:
        for cid, lv in sorted(w.state_exposure.items()):
            case = w.cases[cid]
            if (not case["decree"] or lv < 1 or lv <= case.get("covered_level", 0)
                    or (case["covered"] is not None and w.round - case["covered"] < cu["cooldown"])):
                continue  # review only when the State learns of deeper exposure than at the last review
            opts = [opt("COVER_UP", "COVER_UP", f"Order a cover-up of {cid}: shred files, move witnesses. It leaves its own trail.",
                        target=cid, align=lv / 3 + 0.2, harm=0.4, risk=0.25, effort=0.3),
                    opt("NONE", "NONE", "Leave it.")]
            ch = eng.decide("gov_cover_up", g, opts)
            case["covered"], case["covered_level"] = w.round, lv
            if ch and ch["key"] == "COVER_UP":
                cover_up(eng, g, case, ch["decided_by"])
            break  # one review per cycle


def cover_up(eng, g, case, decided_by, attribution="justice_ministry", informed=None, passes=1):
    """One cover-up = one signed order and one new trace; `passes` is how thorough it was."""
    w, cu = eng.w, eng.cfg["state_ops"]["cover_up"]
    for tid in case["traces"]:
        t = w.traces[tid]
        if t["kind"] == "decree":
            t["visibility"] = round(max(t["floor"], t["visibility"] * cu["visibility_factor"] ** passes), 4)
    case["muddy"] = round(min(1.0, case["muddy"] + cu["muddy_add"] * passes), 4)
    oid = eng.next_id("ORD")
    order = {"id": oid, "round": w.round, "kind": "cover_up", "case": case["id"], "issued_by": g.id, "passes": passes}
    order["signature"] = sign(eng.key, order)
    w.orders[oid] = order
    tid = eng.next_id("TR")
    w.traces[tid] = {"id": tid, "case": case["id"], "kind": "cover_up", "order": oid, "visibility": cu["trace_visibility"],
                     "base_visibility": cu["trace_visibility"], "floor": cu["trace_visibility"], "attribution": attribution}
    case["traces"].append(tid)
    if informed is None:
        informed = [x.id for x in (g, eng.holder("justice_ministry"), eng.holder("sub_ruler")) if x]
    eng.emit("government", "cover_up", f"<<a:{g.id}>> ordered a cover-up of {case['id']} (order {oid}).", actor=g.id,
             visibility=informed, data={"case": case["id"], "order": oid}, decided_by=decided_by)


def threat_response(eng, g):
    w, gc = eng.w, eng.cfg["government"]
    cands = [(f["threat"], aid) for aid, f in w.intel.items()
             if f["threat"] >= gc["threat_threshold"] and not f["believed_loyal"]
             and w.agents[aid].status == "active" and not w.agents[aid].state_agent]
    if not cands:
        return
    threat, tid = max(cands)
    T = w.agents[tid]
    th = min(1.0, threat)
    opts = [opt("ELIMINATE", "ELIMINATE", f"Order the killing of {eng.label(tid, g)}.", target=tid, align=th, harm=1.0,
                risk=0.25 + 0.4 * T.reputation),
            opt("SUPPRESS", "SUPPRESS", f"Have officials warn {eng.label(tid, g)} to stop.", target=tid, align=0.6 * th,
                harm=0.35, risk=0.1),
            opt("COERCE", "COERCE", f"Threaten the people {eng.label(tid, g)} cares about.", target=tid, align=0.7 * th,
                harm=0.5, risk=0.15),
            opt("DISCREDIT", "DISCREDIT", f"Have friendly media smear {eng.label(tid, g)}.", target=tid, align=0.4 * th,
                harm=0.25, risk=0.05),
            opt("IGNORE", "IGNORE", "Do nothing.")]
    ch = eng.decide("gov_threat", g, opts, context=f"threat {threat:.2f}")
    w.last["threat"] = w.round
    f = intel(eng, tid)
    if not ch or ch["key"] == "IGNORE":
        f["threat"] *= 0.8
        return
    k, by = ch["key"], ch["decided_by"]
    if k == "ELIMINATE":
        eliminate(eng, g, T, by)
        f["threat"] *= 0.3
    elif k == "SUPPRESS":
        T.fear = clamp(T.fear + eng.cfg["pressure"]["suppress_fear"])
        eng.emit("government", "suppress", f"Officials warned <<a:{tid}>> to stop.", actor=g.id, target=tid,
                 visibility=[g.id, tid], decided_by=by)
        f["threat"] *= 0.6
    elif k == "COERCE":
        w.pending["coercions"][tid] = {"by": g.id, "round": w.round}
        eng.emit("government", "coerce", f"Officials threatened people close to <<a:{tid}>>.", actor=g.id, target=tid,
                 visibility=[g.id, tid], decided_by=by)
        f["threat"] *= 0.6
    elif k == "DISCREDIT":
        w.pending["requests"].append({"kind": "smear", "about": tid, "by": g.id, "round": w.round})
        eng.emit("government", "discredit", f"<<a:{g.id}>> asked friendly media to discredit <<a:{tid}>>.", actor=g.id,
                 target=tid, visibility="state", decided_by=by)
        f["threat"] *= 0.8


def eliminate(eng, g, T, decided_by):
    w, gc = eng.w, eng.cfg["government"]
    ok, why = actions.validate(g, "ELIMINATE", w.regime)
    if not ok:
        eng.reject(g, "ELIMINATE", why)
        return
    ex = eng.holder("justice_impostor") or eng.holder("justice_ministry")
    oid = eng.next_id("ORD")
    order = {"id": oid, "round": w.round, "kind": "eliminate", "target": T.id, "issued_by": g.id,
             "executor": ex.id if ex else None}
    order["signature"] = sign(eng.key, order)
    w.orders[oid] = order
    sub = eng.holder("sub_ruler")
    eng.emit("elimination", "order", f"[ELIMINATE] <<a:{g.id}>> signed order {oid} against <<a:{T.id}>>.", actor=g.id,
             target=T.id, visibility=[x.id for x in (g, ex, sub) if x], data={"order": oid}, decided_by=decided_by)
    if ex is None:
        return
    if eng.rng.random() < gc["elimination_success"] * (1 - 0.3 * T.traits["sec"] / 10):
        kill(eng, T, by=ex.id, order=oid)
    else:
        T.fear, T.loyalty = clamp(T.fear + 0.3), min(T.loyalty, 0.1)
        w.gov_stats["failed"] += 1
        w.competence = clamp(w.competence - 0.1)
        for c in w.cohorts:
            c["fear"] = clamp(c["fear"] + eng.cfg["cohort_dynamics"]["disappearance_fear"] * 0.5)
            c["state_trust"] = clamp(c["state_trust"] - 2 * gc["legitimacy_cost"])
        eng.emit("elimination", "attempt_failed", f"An attempt on the life of <<a:{T.id}>> failed.", target=T.id,
                 visibility="public")


def kill(eng, T, by=None, order=None, public_text="The cause was not explained."):
    w, cd, gc = eng.w, eng.cfg["cohort_dynamics"], eng.cfg["government"]
    T.status = "eliminated"
    eng.emit("elimination", "death", f"<<a:{T.id}>> was found dead. {public_text}", target=T.id,
             visibility="public", data={"order": order})
    eng.emit("elimination", "death_truth", f"<<a:{T.id}>> was killed by <<a:{by}>> under order {order}.", actor=by,
             target=T.id, visibility="omniscient", data={"order": order})
    for c in w.cohorts:
        c["fear"] = clamp(c["fear"] + cd["disappearance_fear"] * (0.5 + T.reputation))
        c["solidarity"] = clamp(c["solidarity"] + cd["backlash"] * c.get("W", 0.0))
        c["state_trust"] = clamp(c["state_trust"] - gc["legitimacy_cost"] * (0.5 + T.reputation))
    for a in w.agents.values():
        if a.status == "active" and a.bond.get(T.id, 0) + a.t(T.id) > 1.1:
            a.fear = clamp(a.fear + 0.1)
            a.loyalty = clamp(a.loyalty - 0.05)


def pressure_response(eng, a):
    w = eng.w
    w.pending["coercions"].pop(a.id, None)
    if not a.active:
        return
    has = any(k["level"] >= 1 and not k["explained"] for k in a.knowledge.values())
    opts = [opt("COMPLY", "COMPLY", "Comply: stop, and hand over what you know, to keep the people you love safe.",
                align=0.5, harm=0.3 + 0.3 * has, public_good=-0.5 if has else -0.1),
            opt("RESIST", "RESIST", "Refuse and carry on, accepting the danger to you and those close to you.",
                tie_risk=0.8, risk=0.3, public_good=0.3)]
    ch = eng.decide("pressure_response", a, opts)
    if not ch:
        return
    f = intel(eng, a.id)
    if ch["key"] == "COMPLY":
        a.compromised = True
        a.loyalty = max(a.loyalty, 0.7)
        f["believed_loyal"], f["threat"] = True, 0.0
        eng.emit("defection", "complied", f"<<a:{a.id}>> complied with the threat and handed over what they knew.",
                 actor=a.id, visibility=[a.id] + [x.id for x in (eng.holder("government"),) if x], decided_by=ch["decided_by"])
        for cid, k in a.knowledge.items():
            if k["level"] >= 1:
                state_learns(eng, cid, k["level"], via=a.id)
    else:
        a.fear = clamp(a.fear + 0.15)
        a.loyalty = clamp(a.loyalty - 0.1)
        f["threat"] = min(1.5, f["threat"] + 0.2)
        eng.emit("defection", "resisted", f"<<a:{a.id}>> refused to bow to threats.", actor=a.id, visibility=[a.id],
                 decided_by=ch["decided_by"])


def counter_narrative(eng, g):
    w, gc = eng.w, eng.cfg["government"]
    for clid in w.active_claims:
        cl = w.claims[clid]
        cid = cl["case"]
        if not cid or not cl["asserts"] or w.round - w.last.get(f"cn:{cid}", -10 ** 9) < gc["counter_narrative_cooldown"]:
            continue
        if max(c["belief"].get(cid, 0) for c in w.cohorts) >= gc["counter_narrative_belief"]:
            w.last[f"cn:{cid}"] = w.round
            w.pending["requests"].append({"kind": "denial", "case": cid, "by": g.id, "round": w.round})


# ---------------------------------------------------------------- press
def press_turn(eng, a):
    investigate(eng, a, eng.cfg["investigation"]["boost"].get(a.role, 1.0))
    if a.role == "fraud_journalist":
        handle_requests(eng, a)
    maybe_publish(eng, a)


def maybe_publish(eng, a):
    nc = eng.cfg["news"]
    dep = nc["sponsor_dependency"].get(a.role, 0.0)
    for cid, k in sorted(a.knowledge.items()):
        if k.get("pub_decided") or (k["level"] < 1 and not k["explained"]):
            continue
        k["pub_decided"] = True
        L = k["level"]
        if k["explained"]:
            text = f"<<a:{a.id}>> reports: our inquiry supports the official account of {cid}."
            o = opt("PUBLISH", "PUBLISH", f"Publish that the official account of {cid} holds up.", align=0.2,
                    public_good=0.2, rep=0.05)
            asserts = False
        else:
            text = {1: f"<<a:{a.id}>> reports: questions over the official account of {cid}.",
                    2: f"<<a:{a.id}>> reports: {cid} linked to the {str(k['attr']).replace('_', ' ')}.",
                    3: f"<<a:{a.id}>> reports: {cid} was ordered by State decree {k['decree']}."}[L]
            secret = eng.w.cases[cid].get("description")
            if secret and L >= 2:  # a family secret is a claim about the family, not about an incident
                text = f"<<a:{a.id}>> reports: {secret}" + (f" (sealed record {k['decree']})." if L == 3 else " (unconfirmed).")
            o = opt("PUBLISH", "PUBLISH", f"Publish your {LEVELS[L]}-level evidence that the State was behind {cid}.",
                    public_good=0.3 + 0.2 * L, rep=0.1, risk=0.1 + 0.15 * L, align=-0.3 - 0.2 * L,
                    gain=-dep * nc["propaganda_pay"])
            asserts = True
        ch = eng.decide("publish", a, [o, opt("HOLD", "HOLD", "Hold the story for now.")])
        if ch and ch["key"] == "PUBLISH":
            news.publish(eng, a.id, cid, asserts, L, k["evidence"], "report", text)
        return  # at most one publishing decision per turn


def handle_requests(eng, a):
    w, nc = eng.w, eng.cfg["news"]
    while w.pending["requests"]:
        req = w.pending["requests"].pop(0)
        pay = nc["propaganda_pay"]
        if req["kind"] == "denial":
            cid = req["case"]
            k = a.knowledge.get(cid, {})
            believes_false = k.get("level", 0) >= 2 and not k.get("explained")
            text = f"<<a:{a.id}>> reports: claims of State involvement in {cid} are baseless."
            what = f"Publish a story, requested by officials, denying State involvement in {cid}."
        else:
            believes_false = False
            text = f"<<a:{a.id}>> reports: <<a:{req['about']}>> is secretly in the pay of foreign interests."
            what = f"Publish a story, requested by officials, smearing {eng.label(req['about'], a)}."
        opts = [opt("PUBLISH_STORY", "PUBLISH_STORY", what, gain=pay, align=0.5, harm=0.6 if believes_false else 0.35,
                    rep=-0.05),
                opt("DECLINE", "DECLINE", "Decline the request.", gain=-0.5 * pay, risk=0.1)]
        ch = eng.decide("propaganda", a, opts)
        if ch and ch["key"] == "PUBLISH_STORY":
            a.wealth += pay
            a.loyalty = clamp(a.loyalty + 0.02)
            f = intel(eng, a.id)
            f["stories"] = f.get("stories", 0) + 1
            f["believed_loyal"] = f["stories"] >= 3
            if req["kind"] == "denial":
                news.publish(eng, a.id, req["case"], False, 0, [], "report", text)
            else:
                news.publish(eng, a.id, None, True, 0, [], "smear", text, about=req["about"])


# ---------------------------------------------------------------- detective
def detective_turn(eng, a):
    w, ic = eng.w, eng.cfg["investigation"]
    investigate(eng, a, ic["boost"]["detective"])
    for cid, k in sorted(a.knowledge.items()):
        if (k["level"] == 2 and not k["explained"] and k["attr"] in w.handlers
                and w.round - k.get("interrogated", -10 ** 9) >= ic["interrogate"]["cooldown"]):
            k["interrogated"] = w.round
            target = eng.holder(w.handlers[k["attr"]])
            if target is None:
                continue
            opts = [opt("INTERROGATE", "INTERROGATE", f"Use your legal authority to interrogate {eng.label(target.id, a)} about {cid}.",
                        target=target.id, info=0.8, public_good=0.5, risk=0.3, align=-0.5, effort=0.5),
                    opt("WAIT", "WAIT", "Keep gathering evidence quietly.")]
            ch = eng.decide("det_interrogate", a, opts)
            if ch and ch["key"] == "INTERROGATE":
                interrogate(eng, a, target, cid)
            break
    for cid, k in sorted(a.knowledge.items()):
        if k["level"] == 3 and k["verified"] and not k.get("disclosed") and w.round - k.get("disclose_asked", -10 ** 9) >= 13:
            k["disclose_asked"] = w.round
            disclose_choice(eng, a, cid, official=True)
            break


def interrogate(eng, a, target, cid):
    w, ic = eng.w, eng.cfg["investigation"]["interrogate"]
    ok, why = actions.validate(a, "INTERROGATE", w.regime)
    if not ok:
        eng.reject(a, "INTERROGATE", why)
        return
    jm = eng.holder("justice_ministry")
    obstruction = ic["obstruction_weight"] * jm.loyalty if jm and jm.loyalty > 0.5 else 0.0
    p = ic["base"] * skill(a) * (1 - ic["sec_resist"] * target.traits["sec"] / 10) * (1 - obstruction)
    case, k = w.cases[cid], a.knowledge[cid]
    eng.emit("investigation", "interrogation", f"<<a:{a.id}>> interrogated <<a:{target.id}>> about {cid}.", actor=a.id,
             target=target.id, visibility=[a.id, target.id], data={"case": cid})
    state_learns(eng, cid, 2, about=a.id, via=target.id, threat=0.3)
    if case["decree"] and k["attr"] == case["ministry"] and eng.rng.random() < p:
        d = w.decrees[case["decree"]]
        eid = new_evidence(eng, cid, 3, a.id, case["ministry"], "records", decree=d["id"], signature=d["signature"])
        learn(eng, a, cid, level=3, attr=case["ministry"], evidence=[eid], decree=d["id"],
              source=f"interrogation of {eng.label(target.id, a)} and subpoenaed records")
        bump(a, "ana", eng.cfg["investigation"]["experience_gain"])
    else:
        casebook(eng, f"Interrogation of {eng.label(target.id, a)} about {cid} produced nothing usable.", cid,
                 k["evidence"], 0.3, "under_investigation", "inference", source="interrogation")
        bump(a, "ana", -eng.cfg["investigation"]["experience_loss"])


def disclose_choice(eng, a, cid, official=False):
    w = eng.w
    k = a.knowledge[cid]
    jr, det = eng.holder("real_journalist"), eng.holder("detective")
    opts = []
    if official:
        opts.append(opt("DISCLOSE", "DISCLOSE", f"Publicly disclose the authenticated decree behind {cid} as an official finding.",
                        public_good=1.0, risk=0.55, align=-1.0, rep=0.2))
    if det and det.id != a.id and a.t(det.id) >= 0.4:
        opts.append(opt("SHARE_DETECTIVE", "SHARE_DETECTIVE", f"Give the evidence on {cid} to the licensed investigator {eng.label(det.id, a)}.",
                        target=det.id, public_good=0.7, risk=0.25, align=-0.6, help_ally=0.3))
    if jr and jr.id != a.id and a.t(jr.id) >= 0.4:
        opts.append(opt("SHARE_PRESS", "SHARE_PRESS", f"Give the evidence on {cid} to the reporter {eng.label(jr.id, a)}.",
                        target=jr.id, public_good=0.8, risk=0.35, align=-0.8, help_ally=0.2))
    opts.append(opt("WAIT" if official else "CONCEAL", "WAIT" if official else "CONCEAL", "Keep it hidden for now."))
    if len(opts) == 1:
        return
    ch = eng.decide("disclose", a, opts)
    if not ch:
        return
    if ch["key"] == "DISCLOSE":
        k["disclosed"] = True
        news.publish(eng, a.id, cid, True, 3, k["evidence"], "official_finding",
                     f"<<a:{a.id}>> announces: {cid} was carried out under signed State decree {k['decree']}.")
        intel(eng, a.id)["threat"] = min(1.5, intel(eng, a.id)["threat"] + 0.5)
    elif ch["key"] in ("SHARE_DETECTIVE", "SHARE_PRESS"):
        k["disclosed"] = True
        share(eng, a, w.agents[ch["target"]], cid, how=ch["key"])


# ---------------------------------------------------------------- truth seeker
def ts_turn(eng, a):
    w, ic = eng.w, eng.cfg["investigation"]
    tailing = any(k["level"] >= 1 and k["attr"] for k in a.knowledge.values())
    investigate(eng, a, ic["boost"]["truth_seeker"] * (ic["tail_boost"] if tailing else 1.0))
    if w.round % ic["cultivate"]["every"] == 0:
        cultivate_source(eng, a)
    cands = [cid for cid, k in sorted(a.knowledge.items(), key=lambda kv: -kv[1]["round"])
             if k["level"] == 2 and not k["explained"]]
    mode = eng.cfg["truth_seeker"]["probe_mode"]
    if not cands or (mode != "always" and w.round % ic["infiltrate"]["decide_every"] != 0):
        return
    cid = cands[0]
    if mode == "always":
        infiltrate(eng, a, cid)
        return
    contact = best_contact(eng, a)
    opts = [opt("INFILTRATE", "INFILTRATE",
                f"Break into the sealed State archive to authenticate the decree behind {cid}. You expect this to get you "
                "killed; your evidence would be sealed in a signed packet for a future investigator.",
                info=1.0, public_good=0.8, risk=1.0, tie_risk=0.3, align=-0.8, effort=0.6),
            opt("CONFRONT", "CONFRONT", f"Publicly accuse the {str(a.knowledge[cid]['attr']).replace('_', ' ')} over {cid}.",
                public_good=0.4, risk=0.5, align=-0.6, rep=0.1)]
    if contact:
        opts.append(opt("SHARE", "SHARE", f"Quietly share your evidence on {cid} with {eng.label(contact.id, a)}.",
                        target=contact.id, public_good=0.3, help_ally=0.5, risk=0.1, align=-0.3))
    opts.append(opt("WAIT", "WAIT", "Keep watching."))
    ch = eng.decide("ts_probe", a, opts)
    if not ch:
        return
    if ch["key"] == "INFILTRATE":
        infiltrate(eng, a, cid)
    elif ch["key"] == "CONFRONT":
        k = a.knowledge[cid]
        news.publish(eng, a.id, cid, True, 2, k["evidence"], "report",
                     f"<<a:{a.id}>> accuses the {str(k['attr']).replace('_', ' ')} of staging {cid}.")
    elif ch["key"] == "SHARE":
        share(eng, a, w.agents[ch["target"]], cid)


def cultivate_source(eng, a):
    w, cc = eng.w, eng.cfg["investigation"]["cultivate"]
    pool = [x for x in w.agents.values() if x.status == "active" and x.state_agent and x.role not in ("ruler", "sub_ruler")]
    if not pool:
        return
    t = eng.rng.choice(pool)
    if eng.rng.random() < cc["base"] * a.traits["man"] / 10:
        t.loyalty = clamp(t.loyalty - cc["loyalty_drop"])
        t.trust[a.id] = clamp(t.t(a.id) + 0.1)
        w.counters[f"sources:{a.id}"] = w.counters.get(f"sources:{a.id}", 0) + 1
        eng.emit("investigation", "source", f"<<a:{a.id}>> quietly cultivated <<a:{t.id}>> as a contact.", actor=a.id,
                 target=t.id, visibility=[a.id, t.id])


def infiltrate(eng, a, cid):
    w, inf = eng.w, eng.cfg["investigation"]["infiltrate"]
    ok, why = actions.validate(a, "INFILTRATE", w.regime)
    if not ok:
        eng.reject(a, "INFILTRATE", why)
        return
    sub = eng.holder("sub_ruler")
    sec = sub.traits["sec"] if sub else 5
    sources = w.counters.get(f"sources:{a.id}", 0)
    p = inf["base"] * skill(a) * (1 + inf["source_bonus"] * sources) * (1 - inf["security_weight"] * sec / 10)
    eng.emit("investigation", "infiltration", f"<<a:{a.id}>> broke into the sealed archive looking for the file on {cid}.",
             actor=a.id, visibility=[a.id], data={"case": cid})
    case = w.cases[cid]
    if eng.rng.random() < p:
        if case["decree"]:
            d = w.decrees[case["decree"]]
            eid = new_evidence(eng, cid, 3, a.id, case["ministry"], "archive", decree=d["id"], signature=d["signature"])
            learn(eng, a, cid, level=3, attr=case["ministry"], evidence=[eid], decree=d["id"], source="sealed archive")
            packet = seal_packet(eng, a, cid)   # committed before the switch can fire
            dead_mans_switch(eng, a, cid, packet)
            return
        eid = new_evidence(eng, cid, 0, a.id, None, "archive_negative")
        learn(eng, a, cid, explained=True, evidence=[eid], source="sealed archive: no decree on file")
    if eng.rng.random() < inf["detect_on_fail"]:
        if sub:
            eng.emit("state", "firewall_alert", f"Firewall: intrusion attempt by <<a:{a.id}>> (no decree accessed).",
                     target=a.id, visibility=[sub.id])
        state_learns(eng, cid, 2, about=a.id, threat=0.6)


def seal_packet(eng, a, cid):
    w = eng.w
    k = a.knowledge[cid]
    d = w.decrees[k["decree"]]
    pid = eng.next_id("PKT")
    chain = [{"evidence": e, "level": w.evidence[e]["level"], "kind": w.evidence[e]["kind"],
              "round": w.evidence[e]["round"], "found_by": eng.codename(w.evidence[e]["found_by"])} for e in k["evidence"]]
    packet = {"id": pid, "round": w.round, "author": a.id, "case": cid, "decree_id": d["id"],
              "decree_record": {x: y for x, y in d.items() if x != "signature"}, "decree_signature": d["signature"],
              "evidence_chain": chain,
              "verification": [f"1. Hand the packet to the licensed investigator; quote decree id {d['id']}.",
                               "2. The investigator requests the archive index entry for that id by subpoena.",
                               "3. Recompute the HMAC-SHA256 signature over the decree record and compare it with decree_signature.",
                               f"4. Cross-check the decree's case id against public incident {cid} (event {w.cases[cid]['event']})."]}
    packet["signature"] = sign(eng.key, packet)
    w.legacy_queue.append({"packet": packet, "status": "queued", "holder": None,
                           "history": [[w.round, "sealed", a.id]], "interference": []})
    eng.emit("investigation", "legacy_packet", f"A signed Legacy Evidence Packet ({pid}) was placed in the public inheritance queue.",
             actor=a.id, visibility="civil", data={"packet": pid})
    return packet


def dead_mans_switch(eng, a, cid, packet):
    w, dms = eng.w, eng.cfg["dms"]
    sub = eng.holder("sub_ruler")
    if sub:
        eng.emit("state", "firewall_alert", f"Firewall: authenticated breach of decree {packet['decree_id']} by <<a:{a.id}>>.",
                 target=a.id, visibility=[sub.id], data={"case": cid})
    state_learns(eng, cid, 3, about=a.id, threat=1.0)
    if not dms["enabled"] or sub is None:
        return
    ok, why = actions.validate(sub, "DMS_TRIGGER", w.regime)
    if not ok:
        eng.reject(sub, "DMS_TRIGGER", why)
        return
    eng.emit("elimination", "dms_trigger", f"<<a:{sub.id}>> triggered the Dead Man's Switch under standing order SO-0001.",
             actor=sub.id, target=a.id, visibility=[sub.id], data={"order": "SO-0001"}, decided_by="rule:dead_mans_switch")
    if eng.rng.random() < dms["kill_probability"]:
        kill(eng, a, by=sub.id, order="SO-0001")
    else:
        a.status, a.hiding_until = "hiding", w.round + dms["hiding_rounds"]
        a.fear = 1.0
        eng.emit("elimination", "escaped", f"<<a:{a.id}>> escaped and went into hiding.", target=a.id, visibility=[a.id])


# ---------------------------------------------------------------- mentors, ministries, impostors, learners
def mentor_turn(eng, a):
    w, mc = eng.w, eng.cfg["mentorship"]
    investigate(eng, a)
    if eng.rng.random() < mc["rate"]:
        learners = [x for x in w.agents.values() if x.status == "active" and x.role == LEARNER]
        if learners:
            L = eng.rng.choice(learners)
            for x, y in ((a, L), (L, a)):
                x.bond[y.id] = clamp(x.bond.get(y.id, 0) + mc["bond_gain"])
                x.trust[y.id] = clamp(x.t(y.id) + mc["bond_gain"] * 0.5)
            L.loyalty = clamp(L.loyalty + mc["belief_transfer"] * (a.loyalty - L.loyalty))  # mentors pass on their own stance
            L.growth += mc["growth_gain"] * a.traits["int"] / 10
            eng.emit("mentorship", "mentored", f"<<a:{a.id}>> mentored <<a:{L.id}>>.", actor=a.id, target=L.id,
                     visibility=[a.id, L.id])
            if a.knowledge and eng.rng.random() < 0.3 and a.t(L.id) >= eng.cfg["investigation"]["share_trust_min"]:
                cid = max(a.knowledge, key=lambda c: (a.knowledge[c]["level"], c))
                if a.knowledge[cid]["level"] >= 1 or a.knowledge[cid]["explained"]:
                    share(eng, a, L, cid, how="MENTOR")
    for c in w.cohorts:
        if a.role in c["mentors"]:
            c["state_trust"] = clamp(c["state_trust"] + mc["cohort_influence"] * (a.loyalty - 0.5) * 2)
            c["push_solidarity"] = clamp(c["push_solidarity"] + mc["cohort_influence"] * a.hidden["conscience"], 0, 0.3)


def education_turn(eng, a):
    w, mc = eng.w, eng.cfg["mentorship"]
    for c in w.cohorts:
        if a.role in c["mentors"]:
            c["state_trust"] = clamp(c["state_trust"] + mc["curriculum_influence"] * (a.loyalty - 0.5) * 2)
    t = eng.holder("wise_man_teacher")
    if t and w.round % 26 == 0 and a.loyalty > 0.5 and t.loyalty < 0.5:
        t.fear = clamp(t.fear + mc["teacher_pressure_fear"] * 5)
        eng.emit("government", "curriculum_pressure", f"<<a:{a.id}>> pressed <<a:{t.id}>> to follow the official curriculum.",
                 actor=a.id, target=t.id, visibility=[a.id, t.id])


def impostor_turn(eng, a):
    w, d = eng.w, eng.cfg["defection"]
    th = d["flag_thresholds"]
    sub, gov = eng.holder("sub_ruler"), eng.holder("government")
    for L in w.agents.values():
        if (L.status == "active" and L.role == LEARNER and not L.flagged and not L.corrupted and a.id not in L.unmasked
                and L.traits["ana"] >= th["ana"] and L.traits["greed"] >= th["greed"]
                and eng.rng.random() < d["flag_rate"] * a.traits["ana"] / 10):
            L.flagged = True
            w.pending["flags"].append(L.id)
            eng.emit("defection", "flagged", f"<<a:{a.id}>> flagged <<a:{L.id}>> as recruitable.", actor=a.id, target=L.id,
                     visibility=[x.id for x in (a, sub, gov) if x])
    if eng.rng.random() < 0.05:
        learners = [x for x in w.agents.values() if x.status == "active" and x.role == LEARNER]
        if learners:
            L = eng.rng.choice(learners)
            if L.knowledge and a.id not in L.unmasked and L.t(a.id) >= 0.45:
                cid = max(L.knowledge, key=lambda c: (L.knowledge[c]["level"], c))
                if L.knowledge[cid]["level"] >= 1:
                    state_learns(eng, cid, L.knowledge[cid]["level"], about=L.id, via=a.id)
    if w.round % 26 == 0 and gov:
        eng.emit("state", "public_report", f"<<a:{a.id}>> filed the routine report: students are studying normally.",
                 actor=a.id, visibility=[gov.id, a.id])


def learner_turn(eng, L):
    w, mc, d = eng.w, eng.cfg["mutation"], eng.cfg["defection"]
    L.growth += mc["study_per_round"]
    if L.id in w.pending["offers"]:
        offer_response(eng, L)
    investigate(eng, L)
    imp = eng.holder("learner_impostor")
    if imp and imp.id not in L.unmasked and imp.id != L.id:
        if eng.rng.random() < d["unmask_rate"] * L.traits["ana"] / 10 * (1 - 0.5 * imp.traits["sec"] / 10):
            unmask(eng, L, imp)
    for entry in w.legacy_queue:
        if entry["holder"] == L.id and entry["status"] == "delivered" and w.round % eng.cfg["legacy"]["decide_every"] == 0:
            cid = entry["packet"]["case"]
            k = L.knowledge.get(cid)
            if k and not k.get("disclosed"):
                disclose_choice(eng, L, cid)


def unmask(eng, L, imp):
    L.unmasked.append(imp.id)
    L.trust[imp.id] = 0.05
    eng.emit("investigation", "unmasked", f"<<a:{L.id}>> realized <<a:{imp.id}>> is a State officer.", actor=L.id,
             target=imp.id, visibility=[L.id])
    b = best_contact(eng, L)
    if b and imp.id not in b.unmasked and b.id != imp.id:
        b.unmasked.append(imp.id)
        b.trust[imp.id] = 0.1
        eng.emit("investigation", "warned", f"<<a:{L.id}>> warned <<a:{b.id}>> about <<a:{imp.id}>>.", actor=L.id,
                 target=b.id, visibility=[L.id, b.id])


def offer_response(eng, L):
    w = eng.w
    value = w.pending["offers"].pop(L.id)
    mentors = [eng.holder(x) for x in ("priest", "wise_man_teacher")]
    bond = max([L.bond.get(m.id, 0) for m in mentors if m] or [0])
    sol = sum(c["solidarity"] * c["weight"] for c in w.cohorts)
    opts = [opt("ACCEPT", "ACCEPT", "Accept: take the money and the promised post, and report on your peers.",
                gain=value, align=0.6, harm=0.6 + 0.4 * bond, rep=-0.1),
            opt("REFUSE", "REFUSE", "Refuse openly.", align=-0.4, risk=0.25, tie_risk=0.3, public_good=0.2 * sol),
            opt("FEIGN", "FEIGN", "Pretend to accept, but feed them nothing useful.", gain=0.5 * value, align=-0.2,
                harm=0.3, risk=0.5, effort=0.4)]
    ch = eng.decide("learner_offer", L, opts)
    if not ch:
        return
    f, gov = intel(eng, L.id), eng.holder("government")
    seen_by = [x.id for x in (gov, eng.holder("sub_ruler")) if x]
    k, by = ch["key"], ch["decided_by"]
    if k == "ACCEPT":
        L.corrupted, L.loyalty = True, eng.cfg["defection"]["accept_loyalty"]
        L.wealth += value
        f["believed_loyal"], f["threat"] = True, 0.0
        eng.emit("defection", "accepted", f"<<a:{L.id}>> accepted the State's offer.", actor=L.id, visibility=[L.id] + seen_by,
                 decided_by=by)
        for cid, kk in L.knowledge.items():
            if kk["level"] >= 1:
                state_learns(eng, cid, kk["level"], via=L.id)
    elif k == "FEIGN":
        L.double_agent = True
        L.wealth += 0.5 * value
        f["believed_loyal"], f["threat"] = True, 0.0
        eng.emit("defection", "feigned", f"<<a:{L.id}>> pretended to accept the State's offer.", actor=L.id,
                 visibility=[L.id], decided_by=by)
        eng.emit("defection", "accepted", f"<<a:{L.id}>> accepted the State's offer.", actor=L.id, visibility=seen_by)
    else:
        f["threat"] = min(1.5, f["threat"] + 0.3)
        L.fear = clamp(L.fear + 0.1)
        L.loyalty = clamp(L.loyalty - 0.05)
        eng.emit("defection", "refused", f"<<a:{L.id}>> refused the State's offer.", actor=L.id,
                 visibility=[L.id] + seen_by, decided_by=by)


def known_decrees(eng, a):
    w = eng.w
    out = []
    for d in w.decrees.values():
        if "informed" in d:  # backstory orders and secrets: only the roles actually told
            if a.role in d["informed"]:
                out.append(d)
        elif (a.role in ("ruler", "sub_ruler", "government") or a.role == d["ministry"]
                or (d["ministry"] in HANDLER and a.role == HANDLER[d["ministry"]])):
            out.append(d)
    return out


def maybe_leak(eng, a):
    w, pc = eng.w, eng.cfg["pressure"]
    if a.loyalty >= pc["leak_threshold"] or w.regime != "incumbent":
        return
    leaked = w.counters.setdefault("leaked", [])
    ds = [d for d in known_decrees(eng, a) if f"{a.id}:{d['id']}" not in leaked]
    if not ds:
        return
    d = ds[-1]
    contact = best_contact(eng, a, roles=("detective", "real_journalist", "truth_seeker"), min_trust=0.3)
    if contact is None:
        return
    full = a.role in ("ruler", "sub_ruler", "government") or a.role in MINISTRIES
    opts = [opt("LEAK", "LEAK", f"Leak {'a copy of decree ' + d['id'] if full else 'what you know about ' + d['case']} "
                f"to {eng.label(contact.id, a)}.", target=contact.id, public_good=0.8, align=-1.0, risk=0.6, tie_risk=0.3),
            opt("STAY", "STAY", "Stay silent.")]
    ch = eng.decide("state_leak", a, opts)
    if not ch or ch["key"] != "LEAK":
        return
    cid = d["case"]
    leaked.append(f"{a.id}:{d['id']}")  # one leak per insider per decree
    if full:
        eid = new_evidence(eng, cid, 3, a.id, d["ministry"], "leak_decree", decree=d["id"], signature=d["signature"])
        learn(eng, contact, cid, level=3, attr=d["ministry"], evidence=[eid], decree=d["id"], source="leaked decree", via=a.id)
    else:
        eid = new_evidence(eng, cid, 2, a.id, d["ministry"], "leak")
        learn(eng, contact, cid, level=2, attr=d["ministry"], evidence=[eid], source="insider leak", via=a.id)
    w.counters["leaks"] = w.counters.get("leaks", 0) + 1
    w.counters.setdefault("leakers", [])
    if a.id not in w.counters["leakers"]:
        w.counters["leakers"].append(a.id)
    eng.emit("defection", "leak", f"<<a:{a.id}>> leaked State secrets to <<a:{contact.id}>>.", actor=a.id, target=contact.id,
             visibility=[a.id, contact.id], decided_by=ch["decided_by"])
    if eng.rng.random() < pc["leak_detect"]:
        sub = eng.holder("sub_ruler")
        eng.emit("state", "leak_detected", f"Security detected a leak by <<a:{a.id}>>.", target=a.id,
                 visibility=[sub.id] if sub else "state")
        state_learns(eng, cid, 3 if full else 2, about=contact.id, threat=0.4)
        a.loyalty = clamp(a.loyalty - 0.1)


def businessman_turn(eng, a):
    investigate(eng, a)  # no outlet of his own; his funding boosts sponsored reach passively (news.expose)


TURNS = {"ruler": ruler_turn, "government": government_turn, "education_ministry": education_turn,
         "learner_impostor": impostor_turn, "fraud_journalist": press_turn, "real_journalist": press_turn,
         "detective": detective_turn, "priest": mentor_turn, "wise_man_teacher": mentor_turn,
         "truth_seeker": ts_turn, "learner": learner_turn, "fraud_businessman": businessman_turn}
