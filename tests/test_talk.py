"""Conversations, detention and the end-of-run verdict."""
import copy
import unittest

from shadowcity import runs
from shadowcity.engine import mechanics, news, outcomes, talk
from shadowcity.engine.engine import Engine
from shadowcity.llm import provider as llm

CFG = runs.load_config()


def cfg(**over):
    return runs.apply_overrides(CFG, over)


def lines(e, act=None):
    return [x for x in e.log.events if x["category"] == "conversation" and x["type"] == "line"
            and (act is None or x["data"]["act"] == act)]


class Advice(unittest.TestCase):
    def test_strong_advice_is_followed_and_override_says_no(self):
        e = Engine(cfg(**{"utility.weights.advice": 50.0}), seed=1)
        g, sub = e.w.agents["government"], e.w.agents["sub_ruler"]
        opts = [mechanics.opt("SUPPRESS", "SUPPRESS", "warn", target="real_journalist", harm=0.1),
                mechanics.opt("IGNORE", "IGNORE", "nothing")]
        ch = e.decide("gov_threat", g, copy.deepcopy(opts), advisor=sub)
        adv = lines(e, "advise")[-1]
        advised = e.log.events[-4]["data"]["chosen"]  # the advise_gov_threat decision
        self.assertEqual(ch["key"], advised)
        self.assertEqual(adv["visibility"], sorted(["government", "sub_ruler"]))  # private between the two
        self.assertEqual(adv["data"]["source"], "scripted")
        # with advice ignored entirely, an override reply starts with "No."
        e2 = Engine(cfg(**{"utility.weights.advice": -50.0}), seed=1)
        e2.decide("gov_threat", e2.w.agents["government"], copy.deepcopy(opts), advisor=e2.w.agents["sub_ruler"])
        self.assertTrue(lines(e2, "reply")[-1]["data"]["say"].startswith("No"))

    def test_llm_say_becomes_an_llm_line(self):
        class Stub:
            name, model = "stub", "3b"

            def chat(self, *a):
                return '{"option": 1, "say": "We should not kill anyone today.", "reason": "x"}'
        d = llm.LLMDecider(Stub(), dict(CFG["llm"], timeout_s=5))
        e = Engine(CFG, seed=3, decider=d)
        e.run(300)
        said = [x for x in lines(e) if x["data"]["source"] == "llm"]
        self.assertTrue(said)
        self.assertEqual(said[0]["data"]["say"], "We should not kill anyone today.")


class Confiding(unittest.TestCase):
    def setUp(self):
        self.e = Engine(cfg(**{"talk.accept_base": 0.0, "talk.accept_trust": 1.0, "talk.accept_level": 0.0}), seed=2)
        self.case = next(c for c in self.e.w.cases.values() if c["decree"])
        self.a, self.b = self.e.w.agents["real_journalist"], self.e.w.agents["slow_learner_01"]
        mechanics.learn(self.e, self.a, self.case["id"], level=2, attr=self.case["ministry"])

    def test_no_trust_no_belief(self):
        self.b.trust[self.a.id] = 0.0
        mechanics.share(self.e, self.a, self.b, self.case["id"])
        self.assertNotIn(self.case["id"], self.b.knowledge)
        self.assertEqual(lines(self.e)[-1]["data"]["act"], "doubt")

    def test_full_trust_belief(self):
        self.b.trust[self.a.id] = 1.0
        mechanics.share(self.e, self.a, self.b, self.case["id"])
        self.assertEqual(self.b.knowledge[self.case["id"]]["level"], 2)


class Interrogation(unittest.TestCase):
    def run_reply(self, key, man=5.0):
        e = Engine(CFG, seed=4)
        det, case = e.w.agents["detective"], next(c for c in e.w.cases.values() if c["decree"])
        target = e.w.agents[e.w.handlers[case["ministry"]]]
        target.traits["man"] = man
        mechanics.learn(e, det, case["id"], level=2, attr=case["ministry"])

        class Force:
            def decide(self, req, eng):
                k = key if req["kind"] == "interrogation_reply" else max(req["options"], key=lambda o: o["score"])["key"]
                return {"key": k, "decided_by": "test"}
        e.decider = Force()
        mechanics.interrogate(e, det, target, case["id"])
        return e, det.knowledge[case["id"]], case

    def test_confession_gives_the_record(self):
        e, k, case = self.run_reply("CONFESS")
        self.assertEqual((k["level"], k["decree"]), (3, case["decree"]))


class DetentionAndVerdict(unittest.TestCase):
    def test_detention_after_change_of_control(self):
        e = Engine(CFG, seed=13)
        case = next(c for c in e.w.cases.values() if c["decree"])
        case["publicly_verified"] = True
        for c in e.w.cohorts:
            c["belief"][case["id"]] = 0.9
            c["state_trust"] = 0.1
        news.refresh_indicators(e, full=True)
        outcomes.check(e)
        self.assertEqual(e.w.regime, "changed")
        d = e.w.decrees[case["decree"]]
        for f in ("issued_by", "executor"):
            self.assertEqual(e.w.agents[d[f]].status, "detained")
        from shadowcity.engine import actions
        self.assertFalse(actions.validate(e.w.agents[d["issued_by"]], "CURFEW")[0])

    def test_verdict_contents(self):
        e = Engine(CFG, seed=4)
        e.run()
        text = "\n".join(e.render(x) for x in e.verdict)
        self.assertIn("Final outcome", text)
        self.assertIn("Government:", text)
        self.assertIn("simulated outcome under this model", text)
        found = any(k["level"] == 3 and k["decree"] for a in e.w.agents.values() if not a.state_agent for k in a.knowledge.values())
        if not found:
            self.assertIn("No one outside the State discovered that the Ruler", text)
        gov_terms = [x for x in e.verdict if x.startswith("•")]
        holders = [a for a in e.w.agents.values() if a.role == "government" or any("->government" in h[1] for h in a.history)]
        self.assertGreaterEqual(len(gov_terms), len(holders))
        self.assertTrue(any(x["type"] == "verdict" for x in e.log.events))

    def test_verdict_names_who_found_the_ruler(self):
        e = Engine(CFG, seed=6)
        det, case = e.w.agents["detective"], next(c for c in e.w.cases.values() if c["decree"])
        mechanics.learn(e, det, case["id"], level=3, attr=case["ministry"], decree=case["decree"])
        e.run(10)
        e.final = outcomes.finalize(e)
        self.assertTrue(any("The Ruler was discovered" in e.render(x) and "detective" in e.render(x) for x in e.verdict))


class Mislead(unittest.TestCase):
    def test_mislead_sometimes_works(self):
        diverted = 0
        for s in range(20):
            e = Engine(CFG, seed=s)
            det, case = e.w.agents["detective"], next(c for c in e.w.cases.values() if c["decree"])
            target = e.w.agents[e.w.handlers[case["ministry"]]]
            mechanics.learn(e, det, case["id"], level=2, attr=case["ministry"])

            class Force:
                def decide(self, req, eng):
                    k = "MISLEAD" if req["kind"] == "interrogation_reply" else req["options"][0]["key"]
                    return {"key": k, "decided_by": "test"}
            e.decider = Force()
            mechanics.interrogate(e, det, target, case["id"])
            diverted += det.knowledge[case["id"]]["attr"] != case["ministry"]
        self.assertTrue(0 < diverted < 20)


if __name__ == "__main__":
    unittest.main()
