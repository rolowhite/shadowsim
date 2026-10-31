"""Compact, neutral-label prompts and a strict reply parser.

Prompts use codenames and neutral role cards (never the roster's value-laden names). Motives and
hidden attributes appear as plain words; abilities never appear (they act only through success odds).
Options are shuffled with a per-decision seed to reduce position bias.
"""
import json
import random
import re

from ..engine import access

SYSTEM = ("You are one person in a simulated city. Decide as that person would, given their situation and "
          "tendencies. Reply with JSON only, no other text: {\"option\": <number>, \"reason\": \"<at most 20 words>\"}")


def level_word(x):
    return ["very low", "low", "moderate", "high", "very high"][min(4, int(x / 10 * 5))]


def card_role(eng, a):
    role = a.role if a.role in eng.cfg["prompt_cards"] else "learner"
    for x, y in eng.cfg.get("ablation", {}).get("label_swap", []):  # ablation: swap persona cards, keep mechanics
        if role == x:
            return y
        if role == y:
            return x
    return role


def option_order(eng, req):
    order = list(range(len(req["options"])))
    random.Random(f"opt:{eng.seed}:{req['seq']}").shuffle(order)
    return order


def build(eng, req):
    a = eng.w.agents[req["agent"]]
    obs = access.observation(eng, a.id)
    h = a.hidden
    lines = [eng.cfg["prompt_cards"][card_role(eng, a)],
             f"You are {obs['you']['codename']}. Year {obs['year']} of the record.",
             f"Your tendencies: curiosity {level_word(a.traits['cur'])}; desire for money {level_word(a.traits['greed'])}; "
             f"{'bold' if h['risk_tolerance'] > 0.66 else 'cautious' if h['risk_tolerance'] < 0.33 else 'measured'} about danger; "
             f"conscience {level_word(h['conscience'] * 10)}; ties to family and friends {level_word(h['attachment'] * 10)}; "
             f"loyalty to the State {level_word(a.loyalty * 10)}.",
             f"Right now you feel {obs['you']['fear']} and {obs['you']['exhaustion']}."]
    if obs.get("premise"):
        lines.append(f"Background: {obs['premise']}")
    if obs["your_cases"]:
        lines.append("What you know: " + "; ".join(
            f"{c['case']} (official cause: {c['official_cause'].replace('_', ' ')}; your evidence: {c['your_evidence']}"
            + (f", points to {c['linked_to'].replace('_', ' ')}" if c["linked_to"] else "")
            + (f", decree {c['decree']}" if c["decree"] else "") + ")" for c in obs["your_cases"]))
    if obs.get("report"):
        lines.append(f"Your officials report: {json.dumps(obs['report'])}")
    if obs.get("security_file"):
        lines.append(f"Security file: {json.dumps(obs['security_file'])}")
    if obs["recent"]:
        lines.append("Recently: " + " | ".join(obs["recent"]))
    order = option_order(eng, req)
    lines.append("Options:")
    for i, j in enumerate(order, 1):
        lines.append(f"{i}. {eng.render(req['options'][j]['text'], viewer=a)}")
    lines.append("Which option do you choose?")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}], order


JSON_RE = re.compile(r"\{.*?\}", re.S)


def parse(text, order):
    """Return (option index into req['options'], reason) or raise ValueError."""
    m = JSON_RE.search(text or "")
    if not m:
        raise ValueError("no JSON object in reply")
    obj = json.loads(m.group(0))
    n = int(obj["option"])
    if not 1 <= n <= len(order):
        raise ValueError(f"option {n} out of range")
    return order[n - 1], str(obj.get("reason", ""))[:200]
