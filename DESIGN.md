# Shadow City: design, rules and assumptions

This document records every decision that shapes outcomes, so results can be read as **"under this model"**. All numbers live in [`config/default.json`](config/default.json). Changing one changes the model; each run stores the config hash and code version.

## 1. Purpose and framing

The simulation asks whether a society can preserve and uncover truth across generations while facing repression, greed, violence, propaganda, rumor and betrayal. No side is hard-coded to win. Outcomes come from agent decisions under configured mechanics. The model is not calibrated to real data, apart from the single target noted in §11, and it does not show how real people behave.

## 2. Decisions taken with the researcher

| Decision | Choice | Why it matters |
|---|---|---|
| Stack | Python stdlib + vanilla JS, one command | Runs offline and reproducibly, with no dependency drift |
| Time unit | **1 round = 1 week**. 500 rounds ≈ 9.6 years (checkpoint), 3000 ≈ 58 years (career), limit 6000 ≈ 115 years | Rates are written per year (`*_per_year`) so they can be defended |
| Roster | Exact 31-agent table. **7th Slow Learner** added as a correction, because the brief's six give 30 | Recorded as an assumption; the roster is configurable |
| `[INTERROGATE]` | **Detective only** (legal authority) | The Truth Seeker is a private investigator: observe, tail, cultivate sources, infiltrate, confront, share |
| The trap | The Truth Seeker's unofficial **`[INFILTRATE]`** of the sealed archive authenticates the decree and trips the Sub-Ruler's Dead Man's Switch. The Truth Seeker knows the risk and **chooses** (`truth_seeker.probe_mode`) | The packet is committed **before** the kill; `dms.kill_probability` is kept for ablation |
| Traits | **Abilities (Int, Ana, Sec, Man) → success odds only. Motives (Cur, Greed) → choices only** | Traits are never counted twice; prompts never show ability numbers |
| Greed 0 | Kept exactly, but **is not immunity**: anyone can be compromised by threats to people they are tied to, by ideology/loyalty, reputation or burnout. Hidden attributes (risk tolerance, conscience, attachment) are drawn from **one distribution for every role** | Otherwise civil leaders could not be corrupted by construction |
| Trait mirror | `ablation.trait_mirror` swaps profiles between paired State and civil roles | Measures how much the roster itself drives results |
| Incidents | **≈50% genuine** (accidents, real dissident attacks, outside actors) | Otherwise "the State did it" is always true, and the independent press is right by construction |
| Dark age | The **civil side's** failed succession | The brief left it ambiguous |
| Change of control | Also possible **without** verified truth (legitimacy collapse with mass solidarity or insider defection), and labeled separately | Lets the study test whether truth mattered |
| Run end | **Run to the limit; log milestones** (`time.stop_on_outcome` switch) | Gives time-to-event data and shows reversals |
| X factor | Free-text premise (with visibility) + parameter overrides, frozen at start | The premise changes **no mechanics**; only parameters do |
| State succession | Ruler is dynastic and never civilian (a hidden heir is **trained**). Sub-Ruler comes from the operatives, or the ruling family if none qualifies. Government comes from the ministers. Ministers come from operatives and co-opted civilians, ranked by the State's (fallible) file. **Staggered** State lifespans | Generational pressure falls on both sides |

## 3. Agents

Each agent has a stable id, an archetype, a current role and six traits. It also has hidden attributes (`risk_tolerance`, `conscience`, `attachment`, each uniform in the configured range, the same for all roles), loyalty to the State (State agents 0.8, everyone else 0.5), fear, burnout, reputation and wealth. It keeps per-person trust and bond values (initial trust 0.5 ± 0.1 noise), a bounded memory of event ids it saw, and its own **knowledge** per case: level, attribution, evidence ids, explained flag, decree id, verified flag. Beliefs are derived from the agent's own evidence (`access.p_state`), never from ground truth.

Impostors carry a **cover alias** (student, justice clerk, health inspector). Non-State agents see only the alias until they unmask them.

## 4. Information access (backend-enforced)

- `access.observation(agent)` is the only input to a prompt. It contains the agent's own evidence, the last 6 events visible to it (rendered with codenames and cover aliases), the premise if visible, and the agent's own channel if it is a State agent. The Ruler gets a **sanitized report**: legitimacy inflated by Government's Man trait and partly corrected by the Sub-Ruler's Ana. Other State agents get the security file. A test asserts that no observation contains case kinds, signatures, ground-truth flags, or a decree id the agent has not authenticated.
- Events carry visibility: `public`, `state`, `civil`, a list of agent ids, or `omniscient`. The Control Room timeline shows everything except `omniscient`, with a visibility badge; omniscient records appear only with the audit toggle.
- The Desk endpoint holds only the Detective's casebook, published claims and public incidents. A test asserts it carries no truth keys. Truth Audit uses a separate endpoint.

## 5. Mechanics

**Decisions.** Every consequential choice is a list of options, each described by features. One **shared utility** applies to all agents:

```
U = w_gain·gain·greed/10 − w_risk·risk·(1−risk_tolerance)·(0.5+fear) − w_tie·tie_risk·attachment
  + w_align·align·(2·loyalty−1) − w_harm·harm·conscience + w_pg·public_good·conscience
  + w_info·info·cur/10 + w_rep·rep + w_ally·help_ally − w_effort·effort·(0.3+burnout) + N(0, noise_sd)
```

Roles differ only in which options they get. Noise is drawn by the engine before the decider runs, so LLM, fallback and replay runs consume the RNG identically. The fallback picks the highest U (`fallback_rule`). In strict mode an LLM picks from the same options, shuffled per decision, and is re-validated by the authority validator.

**Round order.** Seeded shuffle of agent order (no first mover), then per-agent turns (routine rules, plus decisions when their trigger holds), then news (rumors, republishing, cohort update, indicators), then State succession on deaths, then the checkpoint (every 500 rounds), then the milestone check.

**Incidents and false flags.** Genuine incidents arrive at `incidents.genuine_per_year`. A false flag needs a Government proposal (`gov_operation`, reviewed every 26 rounds after a 156-round cooldown) **and** the Ruler's `[DECREE]` (`ruler_decree`). Need = clamp(2·(target_legitimacy − legitimacy) + max(0, solidarity − 0.6)). Each decree is a signed (HMAC-SHA256, per-run key), immutable record. Its trace visibility is

`clamp(base + per_scope·scope + per_handler·handlers + per_witness·witnesses + error·[execution error] − cover_quality·cover, floor, 0.95)`

with floor 0.03 > 0, so every false flag keeps latent exposure risk. Cover quality comes from the executor's and handler's Sec. Cover-ups multiply decree-trace visibility by 0.6 (never below the floor), add muddiness, and leave a signed order plus their own trace (visibility 0.1). They are reviewed only when the State learns of deeper exposure than at the last review.

**Progressive evidence.** Levels: anomaly (1) → link to a ministry (2) → authorization (3). Clues reach only level 2. Per round, an investigator attempts with p = (0.15 + 0.5·cur/10)·(1−burnout)·(1−fear/2) and succeeds with

`clue_rate·boost·trace_union_visibility·skill·difficulty[level]`, where skill = (Int+Ana)/20.

At level 2, misattribution has probability `0.3·(1−skill) + 0.4·muddy`. Muddied cases can yield a false "explanation" (decoy) with `clue_rate·0.5·muddy·(1−skill)`. Genuine incidents can yield **spurious** anomalies and links (always misattributed) or a correct explanation. Level 3 needs one of four routes:
- Detective `[INTERROGATE]` of the handler of the attributed ministry: `0.35·skill·(1−0.6·Sec/10)·(1−obstruction)`, where obstruction is 0.5·the Justice Ministry's loyalty when that loyalty is above 0.5.
- The Truth Seeker's `[INFILTRATE]`: `0.4·skill·(1+0.1·sources)·(1−0.5·SubRuler.Sec/10)`.
- An insider leak: ministers and above can leak a decree copy, impostors a link.
- The legacy packet.

Discovery, proof and public belief are separate fields. Successful findings add +0.1 Ana; failed interrogations cost −0.05. State executors gain or lose Sec the same way.

**Sharing.** Knowledge flows along trust (minimum 0.5) at `share_rate·(1−fear)·(1−burnout)`. Sharing with an impostor or a co-opted civilian reports the sharer to the State (raising threat).

**The trap.** A successful infiltration of a false flag runs in this order:
1. Level-3 evidence.
2. The signed **Legacy Evidence Packet**: decree id, decree record, signature, evidence chain, four verification steps. It is appended to the public-side queue.
3. The firewall alert to the Sub-Ruler.
4. `DMS_TRIGGER` under the standing Government order SO-0001, logged at round 0. This keeps "only Government authorizes lethal force" true.
5. Death.

If the case is genuine, the archive shows "no decree on file", which counts as an explanation, and the intrusion may be detected.

**Legacy queue.** Append-only; no action or function deletes from it. At each checkpoint a queued packet goes to the eligible Learner with the **highest Ana+Cur**, with a seeded lottery for ties. Eligible means: active, not corrupted or compromised, Ana ≥ 4, Cur ≥ 5, loyalty ≤ 0.6. If no one qualifies, the packet stays queued. If the holder dies or is corrupted, it re-queues. The State may notice a delivery (`0.3·impostor Ana/10`) and then pressure, discredit or kill the holder, but the packet survives.

**Greed, defection and pressure.** A Learner with Ana ≥ 4 and Greed ≥ 6 is flaggable. The Learner Impostor flags them with p = 0.01·Ana/10 per round. Government then decides whether to offer. The Learner decides:
- **ACCEPT**: becomes corrupted, loyalty 0.85, reports what it knows.
- **REFUSE**: threat +0.3, fear +0.1.
- **FEIGN**: becomes a double agent; the State's file says loyal; it can be exposed at checkpoints with p = 0.25·Ana_imp/10·(1−Sec/20).

In the baseline about 72% accept, 12% feign and 16% refuse.

Government's threat response to the top threat (≥ 0.6, every 52 rounds) can be ELIMINATE, SUPPRESS (fear +0.25), COERCE (the target chooses COMPLY or RESIST; complying makes them *compromised*, which can happen to anyone, including Greed-0 roles), DISCREDIT (a smear request to sponsored press) or IGNORE. Eliminations are signed orders, executed by justice, and succeed with p = 0.85·(1−0.3·Sec/10). Failures are public.

**Leaks and sources.** Any State agent with loyalty < 0.45 may leak once per decree. State loyalty drifts with legitimacy at each checkpoint. The Truth Seeker can cultivate sources (lowering a State agent's loyalty).

**Mentorship and social influence.** The Priest and the Wise Man Teacher mentor Learners: bond, trust, growth, and the Learner's loyalty moves toward **the mentor's own** loyalty. They also push their cohort's State trust toward their own stance. A Priest can preach obedience. The Education Ministry pushes curriculum and pressures a dissenting Teacher. No role improves people by definition.

## 6. News and belief

A claim stores hidden `truth`, publisher, source type, cited evidence, per-cohort reach, repeats, and the Detective's status, all separately.

There are five cohorts, each with a weight and media diet. Every source starts at **0.5 credibility in every cohort** (symmetric). On each exposure:

```
step = belief_rate · inc · cred[source] · strength / (1 + repeats) · (1 − 0.5·fear if the claim accuses the State)
```

Strength is 0.2/0.35/0.55/0.9 by evidence level, or 1.0 for a publicly proven claim. **Without public proof, belief cannot pass the 0.65 cap** (or drop below 0.35) in the claim's direction. Repetition therefore raises reach but can never make a fact. Public proof needs the Detective to hold a signature-verified decree and the claim to cite authorization-level evidence. Credibility moves only when such proof appears: sources that agreed with it gain 0.08 and sources that contradicted it lose 0.08.

Each Detective status:
- **verified**: authenticated decree.
- **contradicted**: authenticated decree against the claim, or the Detective's explanation (an inference).
- **under investigation**: the Detective holds evidence at level 1–2.
- **unverified**: the Detective holds nothing on the case.

Rumors arrive at 4 per year.

**Indicators** (cohort-weighted means):
- Public trust: generalized trust.
- Fear.
- Media credibility: mean of the independent and sponsored press.
- State legitimacy: State trust. Each round it relaxes toward 0.5 − 0.6·max(0, W − 0.3), where W is the cohort's strongest recency-weighted belief in State wrongdoing; rally effects, disappearances, curfews and amnesties shock it.
- Evidence exposure: deepest civilian evidence per false flag, ground truth, /3.
- Solidarity.
- Succession readiness: share of the five civil roles with at least one eligible Learner.

## 7. Generations

**Civil checkpoint** (every 500 rounds, including 3000), in fixed order:
1. **Mutation**: Learner Int and Ana each gain 0.5·g + noise, where g = (0.3 + 0.8·mentor bond + 0.3·has evidence + study)·learn_rate[archetype]·(1 − 0.5·fear). Learn rate is 0.6, 1.0 or 1.5. Cur, Sec, Man and Greed get symmetric noise; corruption adds Greed, Sec and Man. An archetype is relabeled upward once Int+Ana crosses 6 or 8.
2. **Legacy delivery.**
3. **Civil succession**, in role order Detective, Truth Seeker, Wise Man Teacher, Priest, Real Journalist. A role is filled if its holder's term ends now or it is vacant. The successor is the eligible Learner with the highest sum of the role's traits plus 2·the outgoing holder's trust in them. This uses the **selector's trust, not ground truth**, so a corrupted Learner can be chosen. The outgoing holder passes on its evidence.
4. **Retirement**, after successors are chosen.
5. **Learner replenishment**, back to 31 visible agents.
6. **State reviews**: heir tutoring (+15% of the gap to the best tutor each epoch), State loyalty drift, the Government competence review (the Ruler dismisses Government below 0.35), and double-agent exposure.

**State succession** (any round, on death):
- **Ruler**: the hidden heir (traits halfway between the Ruler's and the population average, plus N(0, 0.8), plus tutoring) is crowned. A competence below 7.0 is a **State crisis**. A new heir is born.
- **Sub-Ruler**: the best impostor with Int ≥ 7 and Sec ≥ 7, ranked by competence × the State's file loyalty; otherwise a ruling-family cadet.
- **Government**: the best minister.
- **Ministers**: impostors or civilians the State believes loyal (co-opted, feigning or coerced Learners, propagandists), ranked by the State's file. **A feigner can rise into a ministry.**
- **Impostors**: recruited from co-opted Learners.
- Starting State leaders have staggered lifespans of 1000–3000 rounds; successors get 3000.

## 8. Outcomes

**Milestones** are logged the first time they hold:
- `truth_verified_public`: a false flag publicly proven, and belief ≥ 0.5 in ≥ 3 of 5 cohorts.
- `change_after_verified_truth`: legitimacy < 0.3 within 520 rounds of that milestone, or the other collapse path after it.
- `change_without_verified_truth`: legitimacy < 0.2 and (solidarity ≥ 0.65 or ≥ 3 insider defectors) with no verified truth.
- `investigation_suppressed`: no living, uncorrupted Detective, Truth Seeker, Real Journalist or packet holder for 260 rounds.
- `civil_dark_age`: a civil succession has failed, all five civil roles are vacant or compromised, no Learner is eligible, and fear ≥ 0.45, sustained for 260 rounds.
- `state_crisis`: a weak heir is crowned.

**Final label**, in fixed precedence:
1. verified disclosure changed control
2. change without verified truth
3. truth public (still believed at the end)
4. dark age (holding at the end)
5. State retains (no living uncorrupted holder of link-level evidence or of a packet, and legitimacy ≥ 0.4)
6. resistance survives, unresolved
7. unresolved

After a change of control, State coercive actions are refused by the validator.

## 9. LLM integration

The provider interface supports Ollama and OpenAI-compatible endpoints. One instance and one worker queue serve everything. Only kinds listed in `llm.route` go to the model. Prompts contain:
- a neutral role card (e.g. "a press reporter whose outlet depends on State-aligned sponsors", never "Fraud Journalist") — `ablation.label_swap` swaps cards
- neutral codenames, with cover aliases where relevant
- motives and hidden attributes as words, never ability numbers
- fear and exhaustion
- the agent's evidence and recent visible events
- options shuffled with a seed per decision

The reply must be JSON `{option, reason}`. Every call is logged. Strict and lenient failure handling is described in the README. Replay reproduces any run from its recorded decisions.

## 10. Bias controls

- Seeded shuffle of agent order every round.
- One shared utility for every agent; role effects come only from options and their own state.
- Symmetric experience learning: the State learns from its operations too.
- Symmetric detection: Learners can unmask impostors; the State can expose feigners.
- Mentors transmit their own stance.
- The packet goes to the highest eligibility score, with seeded ties (not "first by id").
- Symmetric cohort credibility baseline (0.5 for every source).
- Neutral prompts, codenames, shuffled options.
- Genuine incidents (≈50%), so accusing the State is not a free win.
- Both press types can be right or wrong. Sponsored press publishes evidence it holds when its own utility favors it. Independent press can publish spurious links.
- Detective inference is fallible (decoys, misattribution). Only cryptographic proof is "verified".
- Greed 0 is not immunity. Hidden attributes come from one distribution for every role.
- "Resistance survives" requires a living, uncorrupted holder or investigator.

**Remaining asymmetries, deliberate and from the brief:** only the State can use lethal force, false flags and cover-ups; only the State has a spy network; the State's top two posts are dynastic and closed to civilians; the packet's durability is guaranteed by rule.

## 11. Calibration and the baseline

**Only one target was calibrated:** `incidents.genuine_per_year = 0.025`, set so that about half of incidents are genuine in fallback baseline runs (realized share over 100 seeds: 0.58). Everything else comes from the stated assumptions above, not from tuning toward any outcome mix.

**Baseline, fallback, seeds 1–100** (code at the commit that added this file):

| Final label | Share | 95% CI |
|---|---:|---|
| verified disclosure changed control | 22% | 15–31% |
| truth public | 31% | 23–41% |
| State retains | 36% | 27–46% |
| resistance survives, unresolved | 11% | 6–19% |
| change without verified truth, dark age, unresolved | 0% | — |

Milestones: truth verified public 59%, investigation suppressed 4%, State crisis 0%, civil dark age 0%.
Per run, on average: 2.2 false flags, 5.2 incidents, 3.7 eliminations, 0.65 Dead Man's Switch triggers, 5.7 failed civil successions.

**Read this carefully:** under the defaults, dark age, change without verified truth and State crisis never occurred. They are reachable (the tests construct each one) and may appear under other settings: higher fear shocks, a weaker heir bar, more aggressive repression, fewer Learners. Run the sweeps before treating any share as a property of the scenario rather than of these defaults.

## 11b. Backstory (the researcher's X factor)

Backstory events (`config.scenario.events`, entered in the Control Room) run at round 0, after the standing order, the initial false flag and the heir. They are logged with `decided_by: scenario_setup`. `engine/scenario.py` validates them before a run is created and rejects anything it cannot apply.

- **State act = a case with a signed record.** False flags, killing orders and family secrets all keep an HMAC-signed record in the decree registry. Investigation, claims' ground truth, exposure, outcomes and the archive trap treat them alike. Baseline runs are unchanged (same hashes before and after this feature).
- **Killing orders** are marked `extra_legal` when not issued by Government, and list exactly who was informed. Insiders can leak only orders they were told about. The trace uses the false-flag visibility formula with scope 1, one or two handlers, no witnesses and no execution error. The official cause is announced as an incident. If the operative differs from the responsible office, a Detective holding a correct link can interrogate that operative.
- **Motive chain:** authenticating a killing order (level 3) gives a link-level lead on its motive case. This is the only built-in route from a death to a sealed secret.
- **Family secrets** have no public incident, so nobody starts investigating them. They are reachable only through leads (the motive chain, a leak, sharing, inheritance). Proof needs the sealed record, obtained by the archive break-in (which fires the Dead Man's Switch), an insider leak or the legacy packet. Interrogation has no handler for them. Trace visibility is `scenario_defaults.secret_visibility` (0.06) and the cohort prior is 0.1. A family secret that is **publicly verified** triggers the `state_crisis` milestone ("dynastic claim disputed").
- **Destroy** applies the cover-up rule `passes` times inside **one** signed order with **one** new trace, so burning more thoroughly never creates more evidence. Traces never go below their floor.
- **Assumption:** an added person gets population-average traits. Family members get trust and bond 1.0. The 31 visible agents stay 31.

The first backstory was the priest's brother: the Ruler secretly ordered the Sub-Ruler to kill him because he knew the heir is not of the bloodline; his papers were burned; the death was announced as an accident; the Priest found the body and knows only that something is wrong.

## 12. Assumptions still open (all configurable)

- Every utility weight and the noise level.
- Every probability base (clue, interrogation, infiltration, flagging, elimination success).
- Visibility coefficients.
- Cohort count, weights and media diets.
- The rumor rate and cap.
- Eligibility bars for succession and the packet.
- Heir noise, tutoring rate and competence bar.
- Competence dynamics.
- Outcome thresholds and windows.
- Offer value.
- Leak threshold.
- Mutation rates and learn rates.
- Learner replenishment (on by default; profile drawn 2:6:7 like the original roster).
- The 7th Slow Learner.
- Lifespans: civil roles exactly 3000 per the brief; State roles staggered by our choice; other roles have none.

## 13. Known limitations

- Argument quality in LLM replies does not change outcomes; only the chosen option does.
- One small model plays everyone, and safety tuning may bias it against violent options. Compare with fallback runs and report the share of LLM decisions.
- Public opinion is five aggregate cohorts.
- Only the four roles named in the brief and State leadership age; journalists, the businessman and impostors do not.
- Post-change-of-control dynamics (restoration, new institutions) are not modeled.
- The model is not empirically validated. Treat it as a structured thought experiment.
