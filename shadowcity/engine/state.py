"""Agents, cohorts and the world container. Plain data: the rules live in mechanics/news/succession."""
from dataclasses import dataclass, field

TRAITS = ("int", "ana", "sec", "man", "cur", "greed")
ABILITIES = ("int", "ana", "sec", "man")   # these set success odds
MOTIVES = ("cur", "greed")                  # these only shape choices
LEARNER = "learner"
IMPOSTORS = ("learner_impostor", "justice_impostor", "health_impostor")
MINISTRIES = ("health_ministry", "justice_ministry", "education_ministry")
STATE_LEADERSHIP = ("ruler", "sub_ruler", "government") + MINISTRIES
CIVIL_ROLES = ("detective", "truth_seeker", "wise_man_teacher", "priest", "real_journalist")
VISIBLE = ("active", "hiding")              # statuses that count as a living, visible member of the city


@dataclass
class Agent:
    id: str
    archetype: str
    role: str
    name: str
    tier: int
    traits: dict
    hidden: dict                 # risk_tolerance, conscience, attachment: same distribution for every role
    state_agent: bool
    alias: str = None            # cover identity seen by non-State agents
    loyalty: float = 0.5         # loyalty to the State, 0..1
    fear: float = 0.15
    burnout: float = 0.0
    status: str = "active"       # active | hiding | eliminated | retired | deceased | dismissed | hidden (off-roster heir)
    born: int = 0
    lifespan: int = None
    corrupted: bool = False      # accepted a State offer
    compromised: bool = False    # complied with coercion
    double_agent: bool = False   # feigned acceptance
    flagged: bool = False
    reputation: float = 0.5
    wealth: float = 0.0
    growth: float = 0.0          # accumulated learning, applied at mutation checkpoints
    hiding_until: int = 0
    knowledge: dict = field(default_factory=dict)   # case_id -> {level, attr, evidence, explained, decree, verified}
    memory: list = field(default_factory=list)      # visible event ids, bounded
    trust: dict = field(default_factory=dict)       # other id -> 0..1
    bond: dict = field(default_factory=dict)        # other id -> 0..1 (mentorship, cooperation)
    unmasked: list = field(default_factory=list)    # impostor ids this agent has seen through
    focus: str = None
    history: list = field(default_factory=list)     # [round, event] role changes

    @property
    def active(self):
        return self.status == "active"

    @property
    def ends(self):
        return None if self.lifespan is None else self.born + self.lifespan

    def t(self, other_id):
        return self.trust.get(other_id, 0.5)


def expand_roster(cfg):
    out = []
    for e in cfg["roster"]:
        n = e.get("count", 1)
        for i in range(n):
            out.append((e["id"] if n == 1 else f"{e['id']}_{i + 1:02d}", e))
    return out


def make_agent(cfg, rng, aid, entry, born=0):
    ha = cfg["hidden_attributes"]
    hidden = {k: round(rng.uniform(*ha[k]), 4) for k in ("risk_tolerance", "conscience", "attachment")}
    state_agent = bool(entry.get("state_agent"))
    a = Agent(id=aid, archetype=entry["id"], role=entry["role"], name=entry["name"], tier=entry["tier"],
              traits={k: float(entry["traits"][k]) for k in TRAITS}, hidden=hidden, state_agent=state_agent,
              alias=entry.get("alias"),
              loyalty=cfg["initial_loyalty"]["state_agent" if state_agent else "other"],
              fear=cfg["base_fear"], born=born)
    lf = cfg["lifespans"]
    if a.role in lf["civil_roles"]:
        a.lifespan = lf["civil_rounds"]
    elif a.role in lf["state_roles"]:
        a.lifespan = rng.randint(lf["state_initial_min"], lf["state_initial_max"])  # staggered terms
    return a


def make_cohort(c, base):
    return {"id": c["id"], "name": c["name"], "weight": c["weight"], "media": dict(c["media"]),
            "mentors": list(c.get("mentors", [])),
            "state_trust": base["state_trust"], "gtrust": base["generalized_trust"], "fear": base["fear"],
            "solidarity": base["solidarity"], "cred": {s: base["credibility"] for s in c["media"]},
            "belief": {}, "push_solidarity": 0.0}


class World:
    def __init__(self, cfg, rng):
        self.round = 0
        self.agents = {}
        for aid, entry in expand_roster(cfg):
            self.agents[aid] = make_agent(cfg, rng, aid, entry)
        self.apply_mirror(cfg)
        it = cfg["initial_trust"]
        ids = list(self.agents)
        for a in ids:
            for b in ids:
                if a != b:
                    self.agents[a].trust[b] = round(it["base"] + rng.uniform(-it["noise"], it["noise"]), 4)
        self.pop_avg = {t: sum(a.traits[t] for a in self.agents.values()) / len(self.agents) for t in TRAITS}
        self.cohorts = [make_cohort(c, cfg["cohort_baseline"]) for c in cfg["cohorts"]]
        self.cases, self.traces, self.decrees, self.orders = {}, {}, {}, {}
        self.handlers = {"health_ministry": "health_impostor", "justice_ministry": "justice_impostor"}  # who to interrogate per attribution
        self.evidence, self.claims = {}, {}
        self.case_order, self.active_claims = [], []
        self.legacy_queue = []     # append-only; no code path removes entries
        self.casebook = []
        self.intel = {}            # the State's surveillance file: agent id -> {threat, believed_loyal, records}
        self.state_exposure = {}   # case id -> deepest civilian evidence level the State knows about
        self.pending = {"proposals": [], "offers": {}, "coercions": {}, "requests": [], "flags": []}
        self.milestones = {}
        self.counters = {}
        self.last = {}             # cooldown bookkeeping
        self.regime = "incumbent"
        self.gov_stats = {"ops": 0, "exposed": [], "failed": 0}
        self.competence = cfg["government"]["competence_start"]
        self.indicators = {}
        self.metrics = []
        self.heir = None
        self.succession_failures = 0
        self.ids = {}

    def apply_mirror(self, cfg):
        ab = cfg.get("ablation", {})
        if not ab.get("trait_mirror"):
            return
        for x, y in ab["mirror_pairs"]:
            a, b = self.agents.get(x), self.agents.get(y)
            if a and b:
                a.traits, b.traits = b.traits, a.traits
