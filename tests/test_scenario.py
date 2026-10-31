"""Backstory (mechanical X factor) tests, using the priest's-brother example."""
import copy
import json
import unittest

from shadowcity import runs
from shadowcity.engine import mechanics, news, scenario
from shadowcity.engine.access import desk_view, observation
from shadowcity.engine.engine import Engine
from shadowcity.engine.events import verify
from shadowcity.llm.provider import ReplayDecider, decisions_from_events

CFG = runs.load_config()
EXAMPLE = [
    {"type": "person", "id": "priest_brother", "name": "Priest's brother", "kin_of": "priest"},
    {"type": "secret", "label": "heir_secret", "kind": "family_secret", "description": "The Ruler's heir is not of the bloodline.",
     "known_to": ["ruler", "sub_ruler"]},
    {"type": "knows", "agent": "priest_brother", "case": "heir_secret", "level": "authorization"},
    {"type": "killing", "label": "murder", "target": "priest_brother", "ordered_by": "ruler", "executor": "sub_ruler",
     "handler": "justice_impostor", "official_cause": "accident", "motive": "heir_secret"},
    {"type": "destroy", "by": "sub_ruler", "cases": ["heir_secret", "murder"], "records_of": "priest_brother", "passes": 3},
    {"type": "knows", "agent": "priest", "case": "murder", "level": "anomaly",
     "note": "Found my brother's body. He was onto something, but I don't know what."},
]


def with_backstory(events=EXAMPLE):
    c = copy.deepcopy(CFG)
    c["scenario"]["events"] = copy.deepcopy(events)
    return c


def by_kind(e, kind):
    return next(c for c in e.w.cases.values() if c["kind"] == kind)


class Example(unittest.TestCase):
    def setUp(self):
        self.e = Engine(with_backstory(), seed=1)
        self.murder, self.secret = by_kind(self.e, "covert_killing"), by_kind(self.e, "family_secret")

    def test_ground_truth(self):
        w = self.e.w
        self.assertEqual(w.agents["priest_brother"].status, "eliminated")
        self.assertEqual(w.agents["priest_brother"].knowledge, {})  # his papers were burned
        self.assertEqual(sum(a.status in ("active", "hiding") for a in w.agents.values()), 31)
        order = w.decrees[self.murder["decree"]]
        self.assertTrue(verify(self.e.key, order))
        self.assertEqual((order["kind"], order["issued_by"], order["executor"]), ("killing_order", "ruler", "sub_ruler"))
        self.assertTrue(order["extra_legal"])
        self.assertNotIn("government", order["informed"])
        self.assertEqual(order["motive_case"], self.secret["id"])
        self.assertNotIn(order, mechanics.known_decrees(self.e, w.agents["government"]))  # Government can't leak it
        self.assertIn(order, mechanics.known_decrees(self.e, w.agents["sub_ruler"]))
        sec = w.decrees[self.secret["decree"]]
        self.assertEqual(sec["about"], w.heir)
        self.assertTrue(verify(self.e.key, sec))
        for case in (self.murder, self.secret):
            kinds = [w.traces[t]["kind"] for t in case["traces"]]
            self.assertEqual(kinds.count("cover_up"), 1)  # one burning = one order and one new trace
            for t in case["traces"]:
                self.assertGreaterEqual(w.traces[t]["visibility"], w.traces[t]["floor"])
                self.assertGreater(w.traces[t]["visibility"], 0)

    def test_what_people_know(self):
        w, e = self.e.w, self.e
        pk = w.agents["priest"].knowledge
        self.assertEqual(set(pk), {self.murder["id"]})
        self.assertEqual(pk[self.murder["id"]]["level"], 1)
        obs = json.dumps(observation(e, "priest"))
        self.assertNotIn("SEC-", obs)
        self.assertNotIn("DCR-", obs)
        self.assertIn("onto something", obs)
        for a in w.agents.values():  # only the ruler and sub-ruler have the secret in their memory
            if a.status == "active" and a.role not in ("ruler", "sub_ruler"):
                self.assertNotIn("bloodline", json.dumps(observation(e, a.id)), a.id)
        d = desk_view(e)
        self.assertNotIn(self.secret["id"], [i["case"] for i in d["incidents"]])
        self.assertIn(self.murder["id"], [i["case"] for i in d["incidents"]])
        self.assertNotIn(self.secret["id"], e.w.case_order)

    def test_order_leads_to_secret(self):
        e = self.e
        det = e.w.agents["detective"]
        mechanics.learn(e, det, self.murder["id"], level=3, attr=self.murder["ministry"], decree=self.murder["decree"],
                        source="test")
        k = det.knowledge[self.secret["id"]]
        self.assertEqual((k["level"], k["attr"]), (2, "ruling_family"))  # a lead, not proof

    def test_archive_breakin_on_secret_seals_packet_then_dms(self):
        e = Engine(with_backstory(), seed=2)
        e.cfg["investigation"]["infiltrate"]["base"] = 50.0
        secret = by_kind(e, "family_secret")
        ts = e.w.agents["truth_seeker"]
        mechanics.learn(e, ts, secret["id"], level=2, attr="ruling_family")
        mechanics.infiltrate(e, ts, secret["id"])
        types = [ev["type"] for ev in e.log.events]
        self.assertLess(types.index("legacy_packet"), types.index("dms_trigger"))
        self.assertEqual(ts.status, "eliminated")
        self.assertEqual(e.w.legacy_queue[0]["packet"]["decree_id"], secret["decree"])

    def test_public_verification_of_family_secret_is_a_state_crisis(self):
        e = self.e
        det = e.w.agents["detective"]
        eid = mechanics.new_evidence(e, self.secret["id"], 3, det.id, "ruling_family", "archive", decree=self.secret["decree"])
        mechanics.learn(e, det, self.secret["id"], level=3, attr="ruling_family", evidence=[eid], decree=self.secret["decree"])
        self.assertTrue(det.knowledge[self.secret["id"]]["verified"])
        cl = news.publish(e, det.id, self.secret["id"], True, 3, [eid], "official_finding", "finding")
        self.assertTrue(cl["truth"])
        self.assertEqual(cl["status"], "verified")
        self.assertTrue(self.secret["publicly_verified"])
        self.assertIn("state_crisis", e.w.milestones)

    def test_interrogation_route_exists_for_the_killing(self):
        self.assertEqual(self.e.w.handlers.get("state_security"), "justice_impostor")


class Reproducibility(unittest.TestCase):
    def test_same_seed_same_hash_and_replay(self):
        a, b = Engine(with_backstory(), seed=9), Engine(with_backstory(), seed=9)
        a.run(1500)
        b.run(1500)
        self.assertEqual(a.log.prev_hash, b.log.prev_hash)
        c = Engine(with_backstory(), seed=9, decider=ReplayDecider(decisions_from_events(a.log.events)))
        c.run(1500)
        self.assertEqual(a.log.prev_hash, c.log.prev_hash)

    def test_full_run_completes(self):
        e = Engine(with_backstory(), seed=3)
        e.run()
        self.assertIsNotNone(e.final)


class Validation(unittest.TestCase):
    def bad(self, events, msg):
        with self.assertRaises(ValueError) as cm:
            scenario.validate(events, CFG)
        self.assertIn(msg, str(cm.exception))

    def test_rejects(self):
        self.bad([{"type": "knows", "agent": "nobody", "case": "x", "level": "link"}], "unknown person")
        self.bad([{"type": "knows", "agent": "priest", "case": "x", "level": "link"}], "not a label")
        ex = copy.deepcopy(EXAMPLE)
        ex[2]["level"] = "everything"
        self.bad(ex, "level must be")
        ex = copy.deepcopy(EXAMPLE)
        ex[4]["passes"] = 9
        self.bad(ex, "passes")
        self.bad([{"type": "teleport"}], "type must be")
        self.bad([{"type": "person", "id": "priest"}], "must be new")
        ex = copy.deepcopy(EXAMPLE)
        ex[3]["official_cause"] = "magic"
        self.bad(ex, "official_cause")
        self.bad([{"type": "person", "id": "x", "colour": "red"}], "unknown field")
        self.assertEqual(scenario.validate(copy.deepcopy(EXAMPLE), CFG), EXAMPLE)


if __name__ == "__main__":
    unittest.main()
