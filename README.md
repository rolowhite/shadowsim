# Shadow City

A local, reproducible agent simulation that asks whether a society can preserve and uncover truth across generations under repression, greed, violence, propaganda, rumor and betrayal.

**Results are simulated outcomes under this model.** They are not evidence about, or predictions of, how real people or states behave. Every number that shapes them is an assumption listed in [`config/default.json`](config/default.json) and explained in [DESIGN.md](DESIGN.md).

## Run it

Requirements: Python 3.10+ only. No packages to install, no build step, works offline.

```bash
python3 -m shadowcity.server
```

Then open **http://127.0.0.1:8765/control** (the Simulation Control Room).

1. In **New run (X factor)**, set a seed, an optional premise, and optional parameter overrides. Click **Create run**.
2. Click **Start**. You can also **Pause**, **Resume**, **Advance 1 round**, change **Speed** or **Reset**.
3. Click **Open Investigation Desk**. It opens a second window at `/desk?run=<id>` on the same run.

Both windows read the same SQLite database (`data/shadowcity.db`), poll it every 1–1.5 s and show the same run ID. Refreshing or reopening either window loses nothing. If the server restarts, a run resumes by replaying its recorded decisions, and the server checks that the event hash chain comes out identical before continuing.

### The two windows

| Simulation Control Room (`/control`) | Investigation Desk (`/desk`) |
|---|---|
| Start, pause, resume, step, speed, reset | Detective's Casebook: only what the Detective could know |
| Round, 500-round epoch, run ID, seed, model status | News feed: official statements, press reports, smears, rumors, with source, cited evidence, reach and the Detective's status |
| 31-agent roster by tier: traits, loyalty, fear, status, lifespan, and agent-held beliefs (badged) | Analyst Notes: timestamps, confidence, tags, links to claim, case, evidence, event or agent ids. Edit, delete, export |
| Live timeline with category filters | Search and filters: text, rounds, source, topic (case), status |
| Seven society indicators, each with an explanation | **Truth Audit** toggle (off whenever the window opens) |
| Red **Omniscient Audit** panel: true causes, signed decrees, traces, legacy queue, secret flags, ruling family, the State's surveillance file | Badges: fact · agent claim · rumor · Detective inference · Detective verified · your speculation |
| Runs page (`/runs`): outcome counts per configuration with 95% CIs | Export: run settings, all events, notes, metrics, LLM calls |

**Notes never affect the simulation.** They live in their own table, and the engine package imports nothing that could read them (a test enforces this).

**Truth Audit.** The Desk's normal endpoint never sends ground truth. Switching Truth Audit on calls a separate endpoint and marks each claim TRUE or FALSE, along with the true cause of each incident, in a red stripe. Without it, a claim's status is only the Detective's assessment. It reads **verified** only when the Detective holds a decree whose signature checks out.

### The X factor

- **Premise** (free text) plus visibility (operator only, public, or State agents only). It is frozen at start and only enters the prompts of agents allowed to see it. **It changes no mechanics.** In fallback mode it is recorded but has no effect.
- **Parameter overrides**, one per line, for example `incidents.genuine_per_year=0.05` or `ablation.trait_mirror=true`. Unknown paths are rejected, and the overrides are stored with the run (config hash).

## Local model (optional)

The target is a 3B model on an 8 GB GPU through [Ollama](https://ollama.com):

```bash
ollama pull llama3.2:3b
```

In the Control Room choose **Decision maker → Ollama** (or **Auto**). Any OpenAI-compatible server (llama.cpp `llama-server`, LM Studio) also works: set `llm.openai_url` in the config. One model instance serves everything through a single queue.

The LLM is called only for consequential decisions listed in `llm.route`, such as decrees, eliminations, offers, publishing, the archive break-in and interrogation. Routine behavior is rule-based. Every LLM call is logged with prompt hash, reply and latency.

In the default **strict** mode, a timeout or invalid reply is retried once. If it fails again, the round is rolled back and the run pauses with a flag; it never falls back silently. In **lenient** mode a failure falls back to the rule, labeled `fallback_after_llm_error`.

**Without a model** the app runs on the deterministic fallback, which is labeled `fallback_rule` everywhere. Fallback decisions are never shown as LLM output.

## Batches, sweeps and comparison (for the study)

```bash
python3 -m shadowcity.batch run --seeds 1-200 --label baseline --out out/baseline
python3 -m shadowcity.batch run --seeds 1-200 --set ablation.trait_mirror=true --label mirror --out out/mirror
python3 -m shadowcity.batch run --seeds 1-200 --set dms.enabled=false --label no_trap --out out/no_trap
python3 -m shadowcity.batch sweep --params investigation.clue_rate,news.belief_rate,state_ops.target_legitimacy --factor 0.25 --seeds 1-50 --out out/oat
python3 -m shadowcity.batch sweep --params investigation.clue_rate,news.belief_rate,state_ops.target_legitimacy --lhs 40 --seeds 1-5 --out out/lhs
python3 -m shadowcity.batch compare
```

Each batch prints outcome and milestone rates with Wilson 95% intervals. It writes `runs.csv` (one row per run, including the round each milestone was first reached, provenance hashes and decision-maker counts) and `metrics.csv` (indicators every 10 rounds). Add `--events` for full `events.jsonl`. Runs are also saved to the database and appear on `/runs`. Use `--provider ollama` for LLM batches.

A fallback run of 6000 rounds takes a few seconds. An LLM run takes far longer: at roughly 1 to 2 s per call and several hundred to a couple of thousand calls, expect 15 to 60 minutes per run on a 3B model.

## Tests

```bash
python3 -m unittest discover tests -v
```

There are 31 tests. They cover:
- the roster
- determinism and replay
- information access (no ground truth in prompts or on the Desk)
- authority (`[ELIMINATE]` Government-only, `[INTERROGATE]` Detective-only, no delete actions)
- immutable decrees and never-erased traces
- the packet being sealed before the Dead Man's Switch fires
- packet survival and eligibility
- checkpoint order at round 3000
- State succession rules
- repetition never verifying a rumor
- every outcome being reachable
- strict and lenient LLM failure handling
- the Ollama HTTP path against a stub server
- notes CRUD
- restart resume

## Architecture

```
config/default.json        every rule parameter, roster, cohorts, prompts, outcome thresholds
shadowcity/engine/         pure simulation: no I/O, no wall clock in mechanics, no notes
  state.py                 agents (traits, hidden attributes, loyalty, fear, trust, memory, knowledge), cohorts, world
  events.py                append-only, hash-chained event log; HMAC signing of decrees, orders, packets
  actions.py               per-role action catalogue + authority validator
  mechanics.py             incidents, false flags, investigation, sharing, pressure, offers, leaks, archive trap
  news.py                  claims, reach, cohort belief, credibility, Detective assessment, indicators
  succession.py            State succession on death; 500-round civil checkpoint
  outcomes.py              milestones and final-label precedence
  access.py                per-agent observation, Desk view, Truth Audit, Control view, Omniscient audit
  engine.py                round loop, shared utility, decision plumbing, snapshot/restore
shadowcity/llm/            provider interface (Ollama, OpenAI-compatible), queue, prompts, replay
shadowcity/store.py        SQLite: runs, events, views, metrics, notes, llm_calls
shadowcity/server.py       stdlib HTTP server, JSON API, background runner per run
shadowcity/batch.py        batches, one-at-a-time and Latin-hypercube sweeps, comparison
web/                       control.html/js, desk.html/js, runs.html, app.css (vanilla, no build)
```

The engine decides everything. Deciders (LLM, fallback, replay) only choose one option from a list the engine builds, and the engine re-validates the choice against the agent's authority. Agents see the world only through `access.observation()`. That covers their own evidence, events visible to them (with cover identities and neutral codenames), the premise if it is visible to them, and for State agents their own channel.

## Limitations

- One 3B model plays every agent, so "Int 10" changes success odds in formulas, not reasoning quality.
- A 3B model has its own priors, including safety tuning that may make it reluctant to choose violent options. Compare LLM runs with fallback runs, and report the share of LLM decisions (`runs.csv`).
- GPU inference is not bit-reproducible; LLM runs are reproduced exactly only by replaying their recorded decisions.
- Public opinion is five aggregate cohorts, not individuals.
- After a change of control, restoration dynamics are not modeled.
- Only the share of genuine incidents was calibrated (see DESIGN.md). Every other number comes from stated assumptions, so run the sweeps before drawing comparisons.
