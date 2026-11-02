"""Per-role action catalogue and the authority validator.

Every action the engine performs for an agent passes through validate(). No role holds any action
that deletes a decree trace or a legacy packet, and no such action exists in the engine.
"""
CATALOGUE = {
    "ruler": {"APPROVE_DECREE", "REJECT_DECREE", "CURFEW", "AMNESTY", "NO_POLICY", "APPOINT", "DISMISS"},
    "sub_ruler": {"MONITOR", "REPORT", "DMS_TRIGGER"},
    "government": {"STANDING_ORDER", "PROPOSE_FALSE_FLAG", "PUBLIC_MEASURE", "HOLD", "ELIMINATE", "SUPPRESS",
                   "COERCE", "DISCREDIT", "IGNORE", "OFFER", "COVER_UP", "NONE", "REQUEST_STORY"},
    "health_ministry": {"EXECUTE_OP", "REPORT"},
    "justice_ministry": {"EXECUTE_OP", "OBSTRUCT", "EXECUTE_ORDER", "REPORT"},
    "education_ministry": {"CURRICULUM", "REPORT"},
    "learner_impostor": {"FLAG", "TELEMETRY", "REPORT"},
    "justice_impostor": {"HANDLE_OP", "EXECUTE_ORDER"},
    "health_impostor": {"HANDLE_OP"},
    "fraud_businessman": {"FUND_PRESS", "INVESTIGATE", "SHARE"},
    "fraud_journalist": {"PUBLISH", "HOLD", "PUBLISH_STORY", "DECLINE", "INVESTIGATE", "SHARE"},
    "real_journalist": {"PUBLISH", "HOLD", "INVESTIGATE", "SHARE"},
    "detective": {"INVESTIGATE", "INTERROGATE", "WAIT", "DISCLOSE", "SHARE_PRESS", "SHARE"},
    "priest": {"PREACH", "MENTOR", "INVESTIGATE", "SHARE"},
    "wise_man_teacher": {"TEACH", "MENTOR", "INVESTIGATE", "SHARE"},
    "truth_seeker": {"OBSERVE", "TAIL", "CULTIVATE_SOURCE", "INFILTRATE", "CONFRONT", "SHARE", "WAIT"},
    "learner": {"STUDY", "INVESTIGATE", "SHARE", "ACCEPT", "REFUSE", "FEIGN", "SHARE_DETECTIVE", "SHARE_PRESS",
                "CONCEAL"},
}
# Responses to pressure and the choice to leak or stay quiet are open to every person, State or not.
COMMON = {"COMPLY", "RESIST", "LEAK", "STAY", "STONEWALL", "MISLEAD", "CONFESS"}
COERCIVE = {"ELIMINATE", "SUPPRESS", "COERCE", "PROPOSE_FALSE_FLAG", "APPROVE_DECREE", "COVER_UP",
            "DMS_TRIGGER", "CURFEW", "EXECUTE_ORDER", "OFFER"}
FORBIDDEN = {"DELETE_PACKET", "DELETE_TRACE", "ERASE_DECREE", "EDIT_DECREE"}


def validate(agent, action, regime="incumbent"):
    if agent is None:
        return False, "no such agent"
    if agent.status != "active":
        return False, f"{agent.id} is {agent.status}"
    if action in FORBIDDEN:
        return False, "immutable record: decree traces and legacy packets cannot be deleted or edited"
    if action not in CATALOGUE.get(agent.role, set()) | COMMON:
        return False, f"role {agent.role} has no authority for {action}"
    if regime != "incumbent" and action in COERCIVE:
        return False, "State coercive authority is suspended after the change of control"
    return True, ""
