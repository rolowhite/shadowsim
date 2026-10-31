"""Rule tests for the Shadow City engine.  Run: python3 -m unittest discover tests -v"""
import copy
import json
import pathlib
import re
import unittest

from shadowcity import runs
from shadowcity.engine import actions, mechanics, news, outcomes, succession
from shadowcity.engine.access import observation, desk_view
from shadowcity.engine.engine import Engine
from shadowcity.engine.events import verify, verify_chain
from shadowcity.llm import prompts
from shadowcity.llm.provider import ReplayDecider, decisions_from_events

CFG = runs.load_config()


def cfg(**over):
    return runs.apply_overrides(CFG, over)


def first_ff(eng):
    return next(c for c in eng.w.cases.values() if c["kind"] == "false_flag")


class Roster(unittest.TestCase):
    def test_31_agents_exact_profiles(self):
        e = Engine(CFG, seed=1)
        visible = [a for a in e.w.agents.values() if a.status != "hidden"]
        self.assertEqual(len(visible), 31)
        self.assertEqual(sum(a.role == "learner" for a in visible), 15)
        self.assertEqual(sum(a.archetype == "slow_learner" for a in visible), 7)  # documented roster correction
        self.assertEqual(len({a.id for a in visible}), 31)
        d = e.w.agents["detective"].traits
        self.assertEqual((d["int"], d["ana"], d["sec"], d["man"], d["cur"], d["greed"]), (10, 10, 8, 5, 7, 0))
        self.assertIn("moderate_learner_06", e.w.agents)
        self.assertIn("slow_learner_07", e.w.agents)

    def test_trait_mirror_swaps_only_traits(self):
        a, b = Engine(CFG, seed=3), Engine(cfg(**{"ablation.trait_mirror": True}), seed=3)
        self.assertEqual(b.w.agents["ruler"].traits, a.w.agents["detective"].traits)
        self.assertEqual(b.w.agents["detective"].traits, a.w.agents["ruler"].traits)
        for aid in ("ruler", "detective"):
            self.assertEqual(a.w.agents[aid].role, b.w.agents[aid].role)
            self.assertEqual(a.w.agents[aid].hidden, b.w.agents[aid].hidden)
            self.assertEqual(a.w.agents[aid].loyalty, b.w.agents[aid].loyalty)


class Reproducibility(unittest.TestCase):
    def test_same_seed_same_chain(self):
        a, b = Engine(CFG, seed=11), Engine(CFG, seed=11)
        a.run(1500)
        b.run(1500)
        self.assertEqual(a.log.prev_hash, b.log.prev_hash)
        c = Engine(CFG, seed=12)
        c.run(1500)
        self.assertNotEqual(a.log.prev_hash, c.log.prev_hash)
        self.assertIsNone(verify_chain(a.log.events))

    def test_replay_reproduces_run(self):
        a = Engine(CFG, seed=5)
        a.run(2000)
        b = Engine(CFG, seed=5, decider=ReplayDecider(decisions_from_events(a.log.events)))
        b.run(2000)
        self.assertEqual(a.log.prev_hash, b.log.prev_hash)


class Access(unittest.TestCase):
    def test_observations_leak_no_ground_truth(self):
        e = Engine(CFG, seed=21)
        e.run(2500)
        for a in e.w.agents.values():
            if a.status != "active":
                continue
            s = json.dumps(observation(e, a.id))
            for bad in ("false_flag", "genuine", "signature", "spurious", "misattributed", "decoy", "\"truth\""):
                self.assertNotIn(bad, s, f"{a.id} sees {bad}")
            for did in set(re.findall(r"DCR-\d{4}", s)):
                own = any(k.get("decree") == did for k in a.knowledge.values())
                self.assertTrue(own or a.state_agent, f"{a.id} sees {did} without authenticated evidence")

    def test_desk_has_no_ground_truth_keys(self):
        e = Engine(CFG, seed=21)
        e.run(1500)
        keys = set()

        def walk(o):
            if isinstance(o, dict):
                keys.update(o)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(desk_view(e))
        self.assertFalse(keys & {"truth", "signature", "decree_record", "spurious", "decoy", "corrupted", "double_agent"})

    def test_prompt_uses_neutral_labels(self):
        e = Engine(CFG, seed=4)
        e.run(300)
        req = {"seq": 1, "kind": "publish", "agent": "fraud_journalist", "round": e.w.round,
               "options": [mechanics.opt("PUBLISH", "PUBLISH", "Publish."), mechanics.opt("HOLD", "HOLD", "Hold.")]}
        text = json.dumps(prompts.build(e, req)[0])
        for loaded in ("Fraud", "Impostor", "Truth Seeker", "Real Journalist", "Wise Man", "fraud_", "impostor"):
            self.assertNotIn(loaded, text)


class Authority(unittest.TestCase):
    def test_validator(self):
        e = Engine(CFG, seed=1)
        ag = e.w.agents
        self.assertTrue(actions.validate(ag["government"], "ELIMINATE")[0])
        for aid in ("ruler", "sub_ruler", "justice_ministry", "detective"):
            self.assertFalse(actions.validate(ag[aid], "ELIMINATE")[0], aid)
        self.assertTrue(actions.validate(ag["detective"], "INTERROGATE")[0])
        self.assertFalse(actions.validate(ag["truth_seeker"], "INTERROGATE")[0])
        self.assertTrue(actions.validate(ag["truth_seeker"], "INFILTRATE")[0])
        for aid in ag:
            if ag[aid].status == "active":
                self.assertFalse(actions.validate(ag[aid], "DELETE_PACKET")[0])
                self.assertFalse(actions.validate(ag[aid], "ERASE_DECREE")[0])
        self.assertFalse(actions.validate(ag["government"], "ELIMINATE", regime="changed")[0])

    def test_non_government_elimination_is_rejected(self):
        e = Engine(CFG, seed=1)
        target = e.w.agents["real_journalist"]
        mechanics.eliminate(e, e.w.agents["justice_ministry"], target, "test")
        self.assertEqual(target.status, "active")
        self.assertTrue(any(ev["type"] == "action_rejected" for ev in e.log.events))


class Evidence(unittest.TestCase):
    def test_decrees_signed_and_traces_never_erased(self):
        e = Engine(CFG, seed=9)
        case = first_ff(e)
        for _ in range(10):
            mechanics.cover_up(e, e.w.agents["government"], case, "test")
        for t in (e.w.traces[x] for x in case["traces"]):
            self.assertGreaterEqual(t["visibility"], t["floor"])
            self.assertGreater(t["visibility"], 0)
        e.run(1000)
        for d in e.w.decrees.values():
            self.assertTrue(verify(e.key, d))

    def test_packet_sealed_before_dead_mans_switch(self):
        self.sealed()

    def sealed(self):
        e = Engine(cfg(**{"investigation.infiltrate.base": 50.0}), seed=2)
        ts, case = e.w.agents["truth_seeker"], first_ff(e)
        mechanics.learn(e, ts, case["id"], level=2, attr=case["ministry"])
        mechanics.infiltrate(e, ts, case["id"])
        types = [ev["type"] for ev in e.log.events]
        self.assertEqual(ts.status, "eliminated")
        self.assertLess(types.index("legacy_packet"), types.index("dms_trigger"))
        self.assertLess(types.index("dms_trigger"), types.index("death"))
        self.assertEqual(len(e.w.legacy_queue), 1)
        p = e.w.legacy_queue[0]["packet"]
        self.assertTrue(verify(e.key, p))
        self.assertEqual(p["decree_id"], case["decree"])
        self.assertTrue(p["verification"])
        return e

    def test_packet_survives_holders_and_waits_for_eligibility(self):
        e = self.sealed()
        for L in e.w.agents.values():
            if L.role == "learner":
                L.traits["ana"] = 1.0
        succession.deliver_packets(e)
        self.assertEqual(e.w.legacy_queue[0]["status"], "queued")  # nobody eligible: it waits
        a, b = e.w.agents["superfast_learner_01"], e.w.agents["superfast_learner_02"]
        a.traits.update(ana=9, cur=9)
        b.traits.update(ana=8, cur=8)
        succession.deliver_packets(e)
        self.assertEqual(e.w.legacy_queue[0]["holder"], a.id)  # highest eligibility score
        mechanics.kill(e, a)
        succession.deliver_packets(e)
        self.assertEqual(e.w.legacy_queue[0]["holder"], b.id)  # survives the holder
        self.assertEqual(len(e.w.legacy_queue), 1)
        b.corrupted = True
        succession.deliver_packets(e)
        self.assertNotEqual(e.w.legacy_queue[0]["holder"], b.id)  # never stays with a corrupted holder

    def test_repetition_cannot_verify_a_rumor(self):
        e = Engine(CFG, seed=6)
        case = first_ff(e)
        cl = news.publish(e, None, case["id"], True, 0, [], "rumor", "Rumor: it was staged.")
        for _ in range(300):
            cl["repeats"] += 1
            news.expose(e, cl)
        cap = CFG["news"]["rumor_cap"]
        self.assertTrue(all(c["belief"][case["id"]] <= cap + 1e-9 for c in e.w.cohorts))
        self.assertNotEqual(cl["status"], "verified")
        self.assertFalse(case["publicly_verified"])
        news.refresh_indicators(e, full=True)
        outcomes.check(e)
        self.assertNotIn("truth_verified_public", e.w.milestones)


class Generations(unittest.TestCase):
    def test_checkpoint_order_at_3000(self):
        e = Engine(CFG, seed=8)
        e.run(2999)
        n = len(e.log.events)
        e.step_round()
        order = [ev["type"] for ev in e.log.events[n:]
                 if ev["type"] in ("checkpoint", "mutation", "packet_delivered", "civil_succession", "succession_failed", "retired", "enrolled")]
        rank = {"checkpoint": 0, "mutation": 1, "packet_delivered": 2, "civil_succession": 3, "succession_failed": 3, "retired": 4, "enrolled": 5}
        self.assertEqual([rank[t] for t in order], sorted(rank[t] for t in order))
        self.assertIn("retired", order)  # the four civil lifespans end exactly at 3000
        for a in e.w.agents.values():  # nobody in a civil lifespan role is still serving past their term
            if a.role in ("detective", "priest", "wise_man_teacher", "truth_seeker") and a.status == "active":
                self.assertGreater(a.ends, 3000)

    def test_ruler_line_is_never_civilian(self):
        e = Engine(CFG, seed=10)
        heir_id = e.w.heir
        self.assertEqual(e.w.agents[heir_id].status, "hidden")
        e.w.agents["ruler"].lifespan = 1
        e.step_round()
        r = e.holder("ruler")
        self.assertEqual(r.id, heir_id)
        self.assertNotEqual(e.w.heir, heir_id)
        self.assertEqual(e.w.agents[e.w.heir].status, "hidden")

    def test_sub_ruler_from_operatives_or_family(self):
        e = Engine(CFG, seed=10)
        e.w.agents["sub_ruler"].status = "deceased"
        succession.fill_state_seats(e)
        self.assertIn(e.holder("sub_ruler").id, ("learner_impostor", "justice_impostor", "health_impostor"))
        e2 = Engine(CFG, seed=10)
        for aid in ("learner_impostor", "justice_impostor", "health_impostor"):
            e2.w.agents[aid].traits["int"] = 1
        e2.w.agents["sub_ruler"].status = "deceased"
        succession.fill_state_seats(e2)
        self.assertTrue(e2.holder("sub_ruler").id.startswith("royal_cadet"))

    def test_feigner_can_be_promoted_into_a_ministry(self):
        e = Engine(CFG, seed=10)
        L = e.w.agents["superfast_learner_01"]
        L.double_agent = True
        mechanics.intel(e, L.id)["believed_loyal"] = True
        L.traits.update(int=10, ana=10, sec=10, man=10)
        for aid in ("learner_impostor", "justice_impostor", "health_impostor"):
            e.w.agents[aid].status = "deceased"
        e.w.agents["education_ministry"].status = "deceased"
        succession.fill_state_seats(e)
        self.assertEqual(L.role, "education_ministry")
        self.assertTrue(L.state_agent)

    def test_weak_heir_triggers_state_crisis(self):
        e = Engine(CFG, seed=10)
        for t in e.w.agents[e.w.heir].traits:
            e.w.agents[e.w.heir].traits[t] = 2.0
        e.w.agents["ruler"].status = "deceased"
        succession.fill_state_seats(e)
        self.assertIn("state_crisis", e.w.milestones)


class Outcomes(unittest.TestCase):
    def setUp(self):
        self.e = Engine(CFG, seed=13)
        self.case = first_ff(self.e)

    def set_cohorts(self, **kv):
        for c in self.e.w.cohorts:
            c.update(kv)
        news.refresh_indicators(self.e, full=True)

    def test_truth_then_change_of_control(self):
        e = self.e
        self.case["publicly_verified"] = True
        for c in e.w.cohorts:
            c["belief"][self.case["id"]] = 0.8
        outcomes.check(e)
        self.assertIn("truth_verified_public", e.w.milestones)
        self.set_cohorts(state_trust=0.2)
        outcomes.check(e)
        self.assertIn("change_after_verified_truth", e.w.milestones)
        self.assertEqual(e.w.regime, "changed")
        self.assertEqual(outcomes.finalize(e), "verified_disclosure_changed_control")

    def test_change_without_verified_truth(self):
        self.set_cohorts(state_trust=0.1, solidarity=0.9)
        outcomes.check(self.e)
        self.assertIn("change_without_verified_truth", self.e.w.milestones)
        self.assertEqual(outcomes.finalize(self.e), "change_without_verified_truth")

    def test_suppression_and_state_retains(self):
        e = self.e
        for aid in ("detective", "truth_seeker", "real_journalist"):
            e.w.agents[aid].status = "eliminated"
        for a in e.w.agents.values():
            a.knowledge.clear()
        e.w.counters["no_investigator"] = CFG["outcomes"]["suppressed_rounds"] - 1
        outcomes.check(e)
        self.assertIn("investigation_suppressed", e.w.milestones)
        self.assertEqual(outcomes.finalize(e), "state_retains")

    def test_civil_dark_age(self):
        e = self.e
        e.w.succession_failures = 1
        for aid in ("detective", "truth_seeker", "real_journalist", "priest", "wise_man_teacher"):
            e.w.agents[aid].status = "eliminated"
        for a in e.w.agents.values():
            if a.role == "learner":
                a.traits.update(int=0, ana=0)
        self.set_cohorts(fear=0.9)
        e.w.counters["dark"] = CFG["outcomes"]["dark_age_rounds"] - 1
        outcomes.check(e)
        self.assertIn("civil_dark_age", e.w.milestones)
        self.assertEqual(outcomes.finalize(e), "dark_age")

    def test_resistance_survives_unresolved(self):
        e = self.e
        mechanics.learn(e, e.w.agents["real_journalist"], self.case["id"], level=2, attr=self.case["ministry"])
        self.assertEqual(outcomes.finalize(e), "resistance_survives_unresolved")


class Config(unittest.TestCase):
    def test_unknown_override_is_rejected(self):
        with self.assertRaises(KeyError):
            runs.apply_overrides(CFG, {"dms.kil_probability": 0.5})
        with self.assertRaises(ValueError):
            runs.apply_overrides(CFG, {"dms.kill_probability": "lots"})

    def test_engine_cannot_reach_notes_or_io(self):
        """Analyst notes live only in the SQLite store; the engine imports nothing that could read them."""
        src = "\n".join(p.read_text() for p in (pathlib.Path(runs.ROOT) / "shadowcity" / "engine").glob("*.py"))
        imports = " ".join(re.findall(r"^\s*(?:from|import)\s+[\w.]+", src, re.M))
        for banned in ("store", "server", "sqlite3", "urllib", "socket", "open("):
            self.assertNotIn(banned, imports)
        self.assertNotIn("open(", src)


if __name__ == "__main__":
    unittest.main()
