"""Batch runs, parameter sweeps and comparisons for the study.

    python3 -m shadowcity.batch run   --seeds 1-200 [--set key=value ...] [--provider fallback] [--out out/base]
    python3 -m shadowcity.batch sweep --params investigation.clue_rate,news.belief_rate --factor 0.25 --seeds 1-50
    python3 -m shadowcity.batch sweep --params ... --lhs 40 --seeds 1-5
    python3 -m shadowcity.batch compare [--db data/shadowcity.db]

Writes runs.csv (one row per run), metrics.csv (every metrics_every rounds) and, with --events, events.jsonl.
Runs are also saved to the SQLite database so they appear on the Runs page.
"""
import argparse
import collections
import copy
import csv
import json
import pathlib
import random
import secrets
import sys

from . import runs
from .engine import access
from .llm import provider as llm
from .store import Store

FIELDS = ["run_id", "condition", "seed", "final", "round", "false_flags", "incidents", "genuine_share", "claims",
          "eliminations", "packets", "dms_triggers", "leaks", "offers", "accepted", "feigned", "succession_failures", "regime",
          "m_truth_verified_public", "m_change_after_verified_truth", "m_change_without_verified_truth",
          "m_investigation_suppressed", "m_civil_dark_age", "m_state_crisis", "public_trust", "fear", "media_credibility",
          "state_legitimacy", "evidence_exposure", "solidarity", "succession_readiness", "llm_decisions", "fallback_decisions",
          "code_version", "config_hash", "provider", "params"]


def seeds(spec):
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def get(cfg, path):
    for k in path.split("."):
        cfg = cfg[k]
    return cfg


def run_condition(cfg, seed_list, condition, store=None, events_fh=None, premise=None, params=None):
    p, info = runs.provider_info(cfg)
    rows, metric_rows = [], []
    cv, ch = runs.code_version(), runs.config_hash(cfg)
    for s in seed_list:
        dec = llm.LLMDecider(p, cfg["llm"]) if p else None
        secret = secrets.token_hex(16)
        eng = runs.make_engine(cfg, s, premise, secret, decider=dec)
        eng.run()
        sm = runs.summary(eng)
        rid = runs.new_run_id(s)
        db = sm["decided_by"]
        row = {"run_id": rid, "condition": condition, "seed": s, "code_version": cv, "config_hash": ch,
               "provider": info["label"], "params": json.dumps(params or {}),
               "llm_decisions": sum(v for k, v in db.items() if k.startswith("llm:")),
               "fallback_decisions": sum(v for k, v in db.items() if not k.startswith("llm:"))}
        row.update({k: sm[k] for k in ("final", "round", "false_flags", "incidents", "genuine_share", "claims", "eliminations",
                                       "packets", "dms_triggers", "leaks", "offers", "accepted", "feigned",
                                       "succession_failures", "regime")})
        for m in ("truth_verified_public", "change_after_verified_truth", "change_without_verified_truth",
                  "investigation_suppressed", "civil_dark_age", "state_crisis"):
            row["m_" + m] = sm["milestones"].get(m)
        row.update(sm["indicators"])
        rows.append(row)
        for m in eng.w.metrics:
            metric_rows.append(dict(m, run_id=rid, condition=condition, seed=s))
        if store:
            store.create_run(rid, s, cfg, premise or {"text": "", "visibility": "operator"}, info, secret, cv, ch)
            store.update_run(rid, status="finished", round=eng.w.round, final_label=eng.final, milestones=sm["milestones"],
                             summary=sm)
            store.put_metrics(rid, eng.w.metrics)
            store.put_views(rid, eng.w.round, {"control": access.control_view(eng), "audit": access.audit_view(eng),
                                               "desk": access.desk_view(eng), "desk_audit": access.desk_audit(eng)})
            if events_fh is not None:
                store.append_events(rid, eng.log.events)
        if events_fh is not None:
            for e in eng.log.events:
                events_fh.write(json.dumps(dict(e, run_id=rid)) + "\n")
        print(f"  {condition} seed {s}: {eng.final}", file=sys.stderr)
    return rows, metric_rows


def report(rows, title=""):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)
    print(f"\n{title}")
    for cond, rs in by.items():
        n = len(rs)
        print(f"\n== {cond}  (n={n})")
        c = collections.Counter(r["final"] for r in rs)
        for lab in runs.LABELS:
            if c[lab]:
                lo, hi = runs.wilson(c[lab], n)
                print(f"  {lab:34s} {c[lab]:4d}  {c[lab] / n:6.1%}  95% CI [{lo:.1%}, {hi:.1%}]")
        for m in ("truth_verified_public", "change_after_verified_truth", "change_without_verified_truth",
                  "investigation_suppressed", "civil_dark_age", "state_crisis"):
            k = sum(1 for r in rs if r["m_" + m] is not None)
            if k:
                lo, hi = runs.wilson(k, n)
                print(f"  milestone {m:30s} {k / n:6.1%}  [{lo:.1%}, {hi:.1%}]")
        for ind in ("state_legitimacy", "fear", "evidence_exposure", "solidarity", "genuine_share"):
            vals = [r[ind] for r in rs if r[ind] is not None]
            if vals:
                print(f"  mean {ind:24s} {sum(vals) / len(vals):.3f}")


def write(out, rows, metric_rows):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if metric_rows:
        keys = ["run_id", "condition", "seed", "round"] + sorted(k for k in metric_rows[0] if k not in ("run_id", "condition", "seed", "round"))
        with open(out / "metrics.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(metric_rows)
    print(f"\nwrote {out / 'runs.csv'} ({len(rows)} runs)" + (f" and {out / 'metrics.csv'}" if metric_rows else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shadow City batch runner")
    ap.add_argument("mode", choices=("run", "sweep", "compare"))
    ap.add_argument("--config")
    ap.add_argument("--seeds", default="1-30")
    ap.add_argument("--set", action="append", default=[], help="override, e.g. --set dms.enabled=false")
    ap.add_argument("--provider", help="fallback | ollama | openai_compat | auto")
    ap.add_argument("--premise", default="")
    ap.add_argument("--premise-visibility", default="operator", choices=("public", "state", "operator"))
    ap.add_argument("--params", default="", help="sweep: comma-separated numeric config paths")
    ap.add_argument("--factor", type=float, default=0.25)
    ap.add_argument("--lhs", type=int, default=0, help="sweep: Latin-hypercube samples instead of one-at-a-time")
    ap.add_argument("--out", default="out/batch")
    ap.add_argument("--db", default=str(runs.ROOT / "data" / "shadowcity.db"))
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--events", action="store_true", help="also write events.jsonl and store full event logs")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--like", help="copy config (incl. backstory) and premise from a saved run id")
    a = ap.parse_args(argv)
    store = None if a.no_db else Store(a.db)
    if a.mode == "compare":
        rows = [dict(r, condition=r["config_hash"], final=r["final_label"],
                     **{"m_" + k: v for k, v in (r["milestones"] or {}).items()},
                     **((r["summary"] or {}).get("indicators", {})), genuine_share=(r["summary"] or {}).get("genuine_share"))
                for r in (store.list_runs() if store else []) if r["final_label"]]
        for r in rows:
            for m in ("truth_verified_public", "change_after_verified_truth", "change_without_verified_truth",
                      "investigation_suppressed", "civil_dark_age", "state_crisis"):
                r.setdefault("m_" + m, None)
            for ind in ("state_legitimacy", "fear", "evidence_exposure", "solidarity"):
                r.setdefault(ind, None)
        report(rows, "Saved runs grouped by config hash")
        return
    base = runs.load_config(a.config)
    if a.like:
        row = Store(a.db).get_run(a.like)
        if row is None:
            ap.error(f"no saved run {a.like} in {a.db}")
        base = row["config"]
        if not a.premise:
            a.premise, a.premise_visibility = row["premise"]["text"], row["premise"]["visibility"]
    over = runs.parse_override_text("\n".join(a.set))
    if a.provider:
        over["llm.provider"] = a.provider
    base = runs.apply_overrides(base, over)
    premise = {"text": a.premise, "visibility": a.premise_visibility}
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fh = open(out / "events.jsonl", "w") if a.events else None
    seed_list = seeds(a.seeds)
    rows, mrows = [], []
    if a.mode == "run":
        r, m = run_condition(base, seed_list, a.label, store, fh, premise, over)
        rows += r
        mrows += m
    else:
        params = [p for p in a.params.split(",") if p]
        if not params:
            ap.error("sweep needs --params")
        if a.lhs:
            rng = random.Random(f"lhs:{a.params}:{a.lhs}")
            strata = {p: rng.sample(range(a.lhs), a.lhs) for p in params}
            for i in range(a.lhs):
                cfg, vals = copy.deepcopy(base), {}
                for p in params:
                    b = get(base, p)
                    u = (strata[p][i] + rng.random()) / a.lhs
                    vals[p] = round(b * (1 - a.factor + 2 * a.factor * u), 6)
                cfg = runs.apply_overrides(cfg, vals)
                r, m = run_condition(cfg, seed_list, f"lhs{i:03d}", store, fh, premise, vals)
                rows += r
                mrows += m
        else:
            r, m = run_condition(base, seed_list, "base", store, fh, premise, {})
            rows += r
            mrows += m
            for p in params:
                b = get(base, p)
                for mult in (1 - a.factor, 1 + a.factor):
                    v = round(b * mult, 6) if not isinstance(b, bool) else b
                    cfg = runs.apply_overrides(base, {p: v})
                    r, m = run_condition(cfg, seed_list, f"{p}={v}", store, fh, premise, {p: v})
                    rows += r
                    mrows += m
    if fh:
        fh.close()
    report(rows, f"Shadow City batch ({a.mode}) - simulated outcomes under this model, not predictions")
    write(out, rows, mrows)


if __name__ == "__main__":
    main()
