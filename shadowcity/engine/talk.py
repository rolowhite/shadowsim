"""Conversations. Speech is a set of acts with mechanical effects (advice, confiding, replies under
interrogation, threats, offers); the words are flavor. Fallback lines come from these templates and are
labeled `scripted`; an LLM's own line is labeled `llm`. Template variants are picked by hashing, so talk
never consumes the simulation RNG.

{t} = the option's target (an agent or a case), {case}, {m} = ministry, {other} = another office.
"""
import hashlib

ADVICE = {
    ("gov_threat", "ELIMINATE"): ["{t} knows too much. I think we should kill them.", "End {t}. Quietly."],
    ("gov_threat", "SUPPRESS"): ["Have {t} warned. A body draws attention.", "A warning is enough for {t}."],
    ("gov_threat", "COERCE"): ["Lean on the people {t} loves.", "Go through {t}'s family."],
    ("gov_threat", "DISCREDIT"): ["Let the papers destroy {t}'s name.", "Make people stop believing {t}."],
    ("gov_threat", "IGNORE"): ["{t} is not worth the risk. Leave them.", "Let {t} be. It will pass."],
    ("ruler_decree", "APPROVE_DECREE"): ["The city is slipping. Sign it.", "Sign it. Fear will bring them back to us."],
    ("ruler_decree", "REJECT_DECREE"): ["Too risky. Every decree stays on file.", "Don't sign. One leak and it's over."],
    ("ruler_policy", "CURFEW"): ["We need a curfew.", "Put soldiers on the streets at night."],
    ("ruler_policy", "AMNESTY"): ["Offer an amnesty. Let them breathe.", "Show mercy. It costs us little."],
    ("ruler_policy", "NO_POLICY"): ["Change nothing for now.", "Stay the course."],
    ("gov_operation", "PROPOSE_FALSE_FLAG"): ["My people can stage it. Nobody will trace it to us.", "Give me the order and it will look like their work."],
    ("gov_operation", "PUBLIC_MEASURE"): ["A campaign in the papers would be safer.", "Use the newspapers, not blood."],
    ("gov_operation", "HOLD"): ["Not now. Wait.", "Let it settle first."],
    ("gov_offer", "OFFER"): ["{t} could be bought. Make an offer.", "{t} wants money. Use that."],
    ("gov_offer", "IGNORE"): ["{t} isn't worth it.", "Forget {t}."],
    ("gov_cover_up", "COVER_UP"): ["The files on {case} are a danger. Let me clean them up.", "Someone is close on {case}. Burn the files."],
    ("gov_cover_up", "NONE"): ["Leave {case} alone. Touching it leaves marks.", "Don't touch {case}."],
    ("propaganda", "PUBLISH_STORY"): ["Run the story. It pays.", "Print it. Our friends will remember."],
    ("propaganda", "DECLINE"): ["Skip this one.", "Not this one; it could come back on us."],
    ("learner_offer", "ACCEPT"): ["Take the money. Anyone would.", "Take it. You'll never get another chance."],
    ("learner_offer", "REFUSE"): ["Don't. Once they own you, they never let go.", "Say no. We'll manage."],
    ("learner_offer", "FEIGN"): ["Play along, but give them nothing.", "Say yes, then feed them nothing."],
    ("pressure_response", "COMPLY"): ["Do what they say. Your family comes first.", "Stop. It isn't worth your family."],
    ("pressure_response", "RESIST"): ["Don't give in. We'll stand with you.", "If you stop now, they win."],
    ("ts_probe", "INFILTRATE"): ["If the proof is in that archive, go.", "Go in. Someone has to."],
    ("ts_probe", "CONFRONT"): ["Say it in public. They can't silence everyone.", "Go public."],
    ("ts_probe", "SHARE"): ["Tell someone you trust first.", "Don't carry this alone."],
    ("ts_probe", "WAIT"): ["Don't go in there. It's suicide.", "Wait. Please."],
    ("disclose", "DISCLOSE"): ["Announce it. People deserve to know.", "Tell everyone."],
    ("disclose", "SHARE_DETECTIVE"): ["Give it to the investigator.", "The investigator can use this."],
    ("disclose", "SHARE_PRESS"): ["Take it to the press.", "A reporter would print this."],
    ("disclose", "WAIT"): ["Keep it hidden for now. It's too dangerous.", "Not yet."],
    ("disclose", "CONCEAL"): ["Keep it hidden for now. It's too dangerous.", "Hide it."],
}

# (audience, texts). audience: "reply" = advisor and the people the decision is addressed to; "public" = the city
REPLY = {
    ("gov_threat", "ELIMINATE"): ("reply", ["Do it. Make it look natural.", "Do it."]),
    ("gov_threat", "SUPPRESS"): ("reply", ["No killing. Warn them to stop.", "Warn them. Nothing more."]),
    ("gov_threat", "COERCE"): ("reply", ["Find out who they love, and let them know.", "Use the family."]),
    ("gov_threat", "DISCREDIT"): ("reply", ["Give it to the papers.", "Ruin their name."]),
    ("gov_threat", "IGNORE"): ("reply", ["Leave them be.", "Not now."]),
    ("ruler_decree", "APPROVE_DECREE"): ("reply", ["Approved. Make it look like their work.", "I'll sign."]),
    ("ruler_decree", "REJECT_DECREE"): ("reply", ["Rejected. Not this way.", "I won't sign this."]),
    ("ruler_policy", "CURFEW"): ("public", ["From tonight, a curfew. Stay in your homes."]),
    ("ruler_policy", "AMNESTY"): ("public", ["I grant an amnesty. Let the city breathe."]),
    ("ruler_policy", "NO_POLICY"): ("reply", ["Nothing changes."]),
    ("gov_operation", "PROPOSE_FALSE_FLAG"): ("reply", ["Prepare it. I'll take it to the Ruler."]),
    ("gov_operation", "PUBLIC_MEASURE"): ("reply", ["We'll run a campaign instead."]),
    ("gov_operation", "HOLD"): ("reply", ["Not now."]),
    ("gov_offer", "OFFER"): ("reply", ["Make the offer."]),
    ("gov_offer", "IGNORE"): ("reply", ["Leave {t} alone."]),
    ("gov_cover_up", "COVER_UP"): ("reply", ["Clean it up."]),
    ("gov_cover_up", "NONE"): ("reply", ["Leave it."]),
    ("propaganda", "PUBLISH_STORY"): ("reply", ["I'll run it."]),
    ("propaganda", "DECLINE"): ("reply", ["I won't print that."]),
    ("learner_offer", "ACCEPT"): ("reply", ["I accept."]),
    ("learner_offer", "REFUSE"): ("reply", ["I won't sell out my friends."]),
    ("learner_offer", "FEIGN"): ("reply", ["Alright. I'm in."]),
    ("pressure_response", "COMPLY"): ("reply", ["I'll stop. Leave my family alone."]),
    ("pressure_response", "RESIST"): ("reply", ["Do what you want. I'm not stopping."]),
    ("ts_probe", "INFILTRATE"): ("reply", ["I'm going in tonight."]),
    ("ts_probe", "CONFRONT"): ("reply", ["I'll say it in public."]),
    ("ts_probe", "SHARE"): ("reply", ["I'll tell someone first."]),
    ("ts_probe", "WAIT"): ("reply", ["I'll wait."]),
    ("disclose", "DISCLOSE"): ("reply", ["I'll announce it."]),
    ("disclose", "SHARE_DETECTIVE"): ("reply", ["I'll take it to the investigator."]),
    ("disclose", "SHARE_PRESS"): ("reply", ["I'll take it to the press."]),
    ("disclose", "WAIT"): ("reply", ["Not yet."]),
    ("disclose", "CONCEAL"): ("reply", ["Not yet."]),
    ("interrogation_reply", "STONEWALL"): ("reply", ["I have nothing to say.", "I want to go home."]),
    ("interrogation_reply", "MISLEAD"): ("reply", ["You're looking at the wrong office. Ask the {other}.", "That was the {other}, not us."]),
    ("interrogation_reply", "CONFESS"): ("reply", ["Fine. It was an order. From the top.", "I only followed the order."]),
}

LINES = {  # interactions that are not decisions
    "offer": ["There's money in it for you, and a future, if you help us.", "Help us and you'll never be poor again."],
    "coerce": ["Think about your family.", "It would be a shame if something happened to your family."],
    "suppress": ["Stop asking questions. This is your only warning.", "Leave it alone."],
    "smear": ["Write about {t}. Make people doubt them.", "I need a story on {t}."],
    "proposal": ["I propose a covert operation by the {m}, staged to look like an attack by others."],
    "interrogate": ["Where were you when {case} happened?", "Tell me what you know about {case}."],
    "leak": ["You didn't get this from me.", "Take it. Burn it after reading."],
    "warn": ["Be careful around {t}. They work for the State.", "Don't trust {t}."],
    "flag": ["{t} could be bought.", "{t} wants money. Worth an offer."],
    "dms": ["The archive was breached. Trigger the switch.", "Someone touched the sealed files. Finish it."],
    "infiltrate": ["If I don't come back, the packet will speak for me."],
    "mentor": ["Sit with me. Tell me what you've learned.", "Come, let's talk."],
    "believe": ["I believe you.", "I knew something was wrong."],
    "doubt": ["That's hard to believe.", "Are you sure? It sounds like a rumor."],
}
CONFIDE = {0: "I looked into {case}. I think the official account holds.",
           1: "Something about {case} doesn't add up.",
           2: "I think the {m} was behind {case}.",
           3: "I have the signed decree behind {case}."}


def pick(eng, texts):
    h = int(hashlib.sha256(f"{eng.seed}:{len(eng.log.events)}".encode()).hexdigest()[:8], 16)
    return texts[h % len(texts)]


def fill(eng, text, opt=None, ctx=None):
    ctx = dict(ctx or {})
    t = (opt or {}).get("target") or ctx.get("t")
    if t is not None:
        ctx["t"] = f"<<a:{t}>>" if t in eng.w.agents else t
    for k in ("m", "other"):
        if k in ctx:
            ctx[k] = str(ctx[k]).replace("_", " ")
    try:
        return text.format(**ctx)
    except (KeyError, IndexError):
        return text.replace("{", "").replace("}", "")


def line(eng, speaker, to, act, text, source="scripted", public=False, reply_to=None):
    """Emit one spoken line. Private lines are visible only to the speaker and listeners."""
    to = [x for x in (to or []) if x and x != speaker]
    vis = "public" if public else sorted({speaker, *to})
    return eng.emit("conversation", "line", f"<<a:{speaker}>>: “{text}”", actor=speaker, target=to[0] if to else None,
                    visibility=vis, data={"to": to, "act": act, "source": source, "reply_to": reply_to,
                                          "say": eng.render(text), "public": public})


def say(eng, speaker, to, act, ctx=None, public=False):
    return line(eng, speaker, to, act, fill(eng, pick(eng, LINES[act]), ctx=ctx), public=public)


def advice(eng, advisor, decider, kind, opt, llm_say, ctx):
    texts = ADVICE.get((kind, opt["key"]))
    if llm_say:
        return line(eng, advisor.id, [decider.id], "advise", llm_say, source="llm")
    if texts:
        return line(eng, advisor.id, [decider.id], "advise", fill(eng, pick(eng, texts), opt, ctx))


def reply(eng, decider, to, kind, opt, llm_say, ctx, advised_key=None, reply_to=None):
    aud, texts = REPLY.get((kind, opt["key"]), ("reply", None))
    if llm_say:
        text, src = llm_say, "llm"
    elif texts:
        text, src = fill(eng, pick(eng, texts), opt, ctx), "scripted"
        if advised_key and advised_key != opt["key"] and text.split()[0].rstrip(".,") != "No":
            text = "No. " + text
        elif advised_key == opt["key"]:
            text = "Agreed. " + text
    elif advised_key:
        text, src = ("Agreed." if advised_key == opt["key"] else "No."), "scripted"
    else:
        return None
    if aud == "public":
        return line(eng, decider.id, to, "reply", text, source=src, public=True, reply_to=reply_to)
    if not to:
        return None
    return line(eng, decider.id, to, "reply", text, source=src, reply_to=reply_to)


def confide(eng, a, b, cid, k):
    m = k.get("attr") or "State"
    return line(eng, a.id, [b.id], "confide", fill(eng, CONFIDE[0 if k["explained"] else k["level"]], ctx={"case": cid, "m": m}))
