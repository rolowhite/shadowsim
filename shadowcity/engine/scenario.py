"""Backstory: the mechanical X factor, entered in the Control Room and applied at round 0.

Events (applied in order, all logged with decided_by="scenario_setup"):
  person   {id, name, kin_of}                                    an off-roster person; kin trust each other fully
  secret   {label, kind: staged_attack|family_secret, description, ministry?, known_to?}
  killing  {label, target, ordered_by, executor, handler, official_cause, motive?}
  destroy  {by, cases: [labels], records_of?, passes: 1-5}
  knows    {agent, case: label, level: anomaly|link|authorization, note?}

validate() is the trust boundary: it runs before a run is created and rejects anything it can't apply.
"""
from . import mechanics, talk
from .events import sign
from .state import TRAITS, make_agent

LEVELS = {"anomaly": 1, "link": 2, "authorization": 3}
CAUSES = ("accident", "house_fire", "illness", "unexplained")
SECRET_KINDS = ("staged_attack", "family_secret")
STAGED_MINISTRIES = ("health_ministry", "justice_ministry")
FIELDS = {"person": ("id", "name", "kin_of"),
          "secret": ("label", "kind", "description", "ministry", "known_to"),
          "killing": ("label", "target", "ordered_by", "executor", "handler", "official_cause", "motive"),
          "destroy": ("by", "cases", "records_of", "passes"),
          "knows": ("agent", "case", "level", "note")}
REQUIRED = {"person": ("id",), "secret": ("label", "kind", "description"),
            "killing": ("label", "target", "ordered_by", "executor", "official_cause"),
            "destroy": ("by", "cases"), "knows": ("agent", "case", "level")}


def roster_ids(cfg):
    from .state import expand_roster
    return {aid for aid, _ in expand_roster(cfg)}


def validate(events, cfg):
    if not isinstance(events, list) or len(events) > 50:
        raise ValueError("backstory must be a list of at most 50 events")
    agents, labels, kinds, dead = roster_ids(cfg), {}, {}, set()
    for i, ev in enumerate(events, 1):
        where = f"backstory event {i}"
        if not isinstance(ev, dict) or ev.get("type") not in FIELDS:
            raise ValueError(f"{where}: type must be one of {', '.join(FIELDS)}")
        t = ev["type"]
        extra = set(ev) - set(FIELDS[t]) - {"type"}
        if extra:
            raise ValueError(f"{where} ({t}): unknown field(s) {', '.join(sorted(extra))}")
        for f in REQUIRED[t]:
            if ev.get(f) in (None, "", []):
                raise ValueError(f"{where} ({t}): '{f}' is required")

        def agent(f, required=True):
            v = ev.get(f)
            if v in (None, "") and not required:
                return
            if v not in agents:
                raise ValueError(f"{where} ({t}): unknown person '{v}' in '{f}'")

        def label(v):
            if v not in labels:
                raise ValueError(f"{where} ({t}): '{v}' is not a label defined by an earlier event")

        if t == "person":
            if not str(ev["id"]).replace("_", "").isalnum() or ev["id"] in agents:
                raise ValueError(f"{where}: person id '{ev['id']}' must be new and use letters, digits or _")
            agent("kin_of", required=False)
            agents.add(ev["id"])
        elif t in ("secret", "killing"):
            if ev["label"] in labels:
                raise ValueError(f"{where}: label '{ev['label']}' is already used")
            if t == "secret":
                if ev["kind"] not in SECRET_KINDS:
                    raise ValueError(f"{where}: secret kind must be one of {', '.join(SECRET_KINDS)}")
                if ev["kind"] == "staged_attack" and ev.get("ministry", "health_ministry") not in STAGED_MINISTRIES:
                    raise ValueError(f"{where}: a staged attack is run by {' or '.join(STAGED_MINISTRIES)}")
                kr = ev.get("known_to") or []
                if not isinstance(kr, list):
                    raise ValueError(f"{where}: known_to must be a list of roles")
            else:
                for f in ("target", "ordered_by", "executor"):
                    agent(f)
                agent("handler", required=False)
                if ev["official_cause"] not in CAUSES:
                    raise ValueError(f"{where}: official_cause must be one of {', '.join(CAUSES)}")
                if ev["target"] in dead:
                    raise ValueError(f"{where}: '{ev['target']}' is already dead")
                if ev.get("motive"):
                    label(ev["motive"])
                dead.add(ev["target"])
            labels[ev["label"]] = t
        elif t == "destroy":
            agent("by")
            agent("records_of", required=False)
            if not isinstance(ev["cases"], list):
                raise ValueError(f"{where}: cases must be a list of labels")
            for c in ev["cases"]:
                label(c)
            p = ev.get("passes", 1)
            if not isinstance(p, int) or not 1 <= p <= 5:
                raise ValueError(f"{where}: passes must be a whole number from 1 to 5")
        elif t == "knows":
            agent("agent")
            label(ev["case"])
            if ev["level"] not in LEVELS:
                raise ValueError(f"{where}: level must be one of {', '.join(LEVELS)}")
    return events


def apply(eng):
    events = eng.cfg.get("scenario", {}).get("events") or []
    if not events:
        return
    validate(events, eng.cfg)
    cases = {}
    for ev in events:
        globals()["_" + ev["type"]](eng, ev, cases)


def _person(eng, ev, cases):
    w = eng.w
    traits = {t: round(w.pop_avg[t], 1) for t in TRAITS}
    a = make_agent(eng.cfg, eng.rng, ev["id"], {"id": ev["id"], "role": "citizen", "name": ev.get("name") or ev["id"],
                                                 "tier": 3, "state_agent": False, "traits": traits})
    w.agents[a.id] = a
    eng.codename(a.id)
    if ev.get("kin_of"):
        k = w.agents[ev["kin_of"]]
        for x, y in ((a, k), (k, a)):
            x.trust[y.id], x.bond[y.id] = 1.0, 1.0
    eng.emit("system", "backstory_person", f"<<a:{a.id}>> lives in the city" + (f", family of <<a:{ev['kin_of']}>>" if ev.get("kin_of") else "") + ".",
             target=a.id, visibility="public", decided_by="scenario_setup")


def _secret(eng, ev, cases):
    w, sd = eng.w, eng.cfg["scenario_defaults"]
    if ev["kind"] == "staged_attack":
        case = mechanics.launch_false_flag(eng, ev.get("ministry", "health_ministry"), decided_by="scenario_setup")
        if case is None:
            raise ValueError(f"secret {ev['label']}: the staged attack could not be set up")
        case["description"] = ev["description"]
        cases[ev["label"]] = case["id"]
        return
    known = ev.get("known_to") or ["ruler", "sub_ruler"]
    cid, did = eng.next_id("SEC"), eng.next_id("DCR")
    ruler = eng.holder("ruler")
    record = {"id": did, "round": w.round, "kind": "family_secret", "case": cid, "ministry": "ruling_family",
              "issued_by": ruler.id if ruler else None, "description": ev["description"], "informed": known,
              "about": w.heir}
    record["signature"] = sign(eng.key, record)
    w.decrees[did] = record
    # not in case_order: no public incident, so it is reachable only through leads
    w.cases[cid] = {"id": cid, "round": w.round, "kind": "family_secret", "subtype": "family_secret",
                    "ministry": "ruling_family", "decree": did, "scope": 0, "traces": [], "muddy": 0.0,
                    "official_story": "no public record", "publicly_verified": False, "covered": None, "event": None,
                    "description": ev["description"]}
    tid = eng.next_id("TR")
    vis = sd["secret_visibility"]
    w.traces[tid] = {"id": tid, "case": cid, "kind": "decree", "decree": did, "visibility": vis, "base_visibility": vis,
                     "floor": eng.cfg["state_ops"]["visibility"]["floor"], "attribution": "ruling_family"}
    w.cases[cid]["traces"].append(tid)
    for c in w.cohorts:
        c["belief"][cid] = sd["secret_prior"]
    ids = [a.id for a in w.agents.values() if a.role in known and a.status in ("active", "hidden")]
    eng.emit("state", "sealed_record", f"Sealed record {did} ({cid}): {ev['description']}", actor=ruler.id if ruler else None,
             visibility=ids, data={"case": cid, "decree": did}, decided_by="scenario_setup")
    cases[ev["label"]] = cid


def _killing(eng, ev, cases):
    w, r = eng.w, eng.rng
    ag = w.agents
    T, boss, ex = ag[ev["target"]], ag[ev["ordered_by"]], ag[ev["executor"]]
    hd = ag.get(ev.get("handler") or "") or ex
    gov_role = "government"
    attribution = "state_security" if ex.role == "sub_ruler" else ex.role
    cover = (ex.traits["sec"] + hd.traits["sec"]) / 20 * r.uniform(0.7, 1.0)
    handlers = 1 if hd is ex else 2
    vis = mechanics.trace_vis(eng, 1, handlers, 0, False, cover)
    did, cid = eng.next_id("DCR"), eng.next_id("INC")
    informed = sorted({boss.role, ex.role, hd.role})
    order = {"id": did, "round": w.round, "kind": "killing_order", "case": cid, "ministry": attribution,
             "issued_by": boss.id, "executor": ex.id, "handler": hd.id, "target": T.id,
             "extra_legal": boss.role != gov_role, "bypassed": None if boss.role == gov_role else gov_role,
             "informed": informed, "motive_case": cases.get(ev.get("motive")), "cover_quality": round(cover, 4)}
    order["signature"] = sign(eng.key, order)
    w.decrees[did] = order
    eng.emit("government", "decree", f"[DECREE] {did}: <<a:{boss.id}>> secretly ordered <<a:{ex.id}>> to kill <<a:{T.id}>>"
             + (" (outside the law: Government was bypassed)." if order["extra_legal"] else "."),
             actor=boss.id, target=T.id, visibility=[boss.id, ex.id, hd.id], data={"decree": did, "case": cid},
             decided_by="scenario_setup")
    if attribution not in w.handlers and hd is not ex:
        w.handlers[attribution] = hd.role  # who a Detective with the right link can question
    mechanics.kill(eng, T, by=hd.id, order=did, public_text=f"Officially: {mechanics.STORY[ev['official_cause']]}.")
    mechanics.new_case(eng, "covert_killing", ev["official_cause"], attribution, did, 1,
                       muddy=eng.cfg["state_ops"]["cover_muddy"] * cover, cid=cid)
    tid = eng.next_id("TR")
    w.traces[tid] = {"id": tid, "case": cid, "kind": "decree", "decree": did, "visibility": round(vis, 4),
                     "base_visibility": round(vis, 4), "floor": eng.cfg["state_ops"]["visibility"]["floor"],
                     "attribution": attribution}
    w.cases[cid]["traces"].append(tid)
    cases[ev["label"]] = cid


def _destroy(eng, ev, cases):
    w = eng.w
    by = w.agents[ev["by"]]
    informed = sorted({by.id} | {a.id for a in w.agents.values() if a.role == "sub_ruler" and a.status == "active"})
    for label in ev["cases"]:
        case = w.cases[cases[label]]
        mechanics.cover_up(eng, by, case, "scenario_setup", attribution=by.role, informed=informed,
                           passes=ev.get("passes", 1))
    if ev.get("records_of"):
        p = w.agents[ev["records_of"]]
        p.knowledge.clear()
        eng.emit("state", "records_destroyed", f"<<a:{by.id}>>'s people burned everything <<a:{p.id}>> had kept.",
                 actor=by.id, target=p.id, visibility=informed, decided_by="scenario_setup")


def _knows(eng, ev, cases):
    w = eng.w
    a, cid = w.agents[ev["agent"]], cases[ev["case"]]
    case, lvl = w.cases[cid], LEVELS[ev["level"]]
    if ev.get("note"):
        talk.line(eng, a.id, [], "thought", ev["note"])
    mechanics.learn(eng, a, cid, level=lvl, attr=case["ministry"] if lvl >= 2 else None,
                    decree=case["decree"] if lvl == 3 else None, source="backstory")
