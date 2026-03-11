#!/usr/bin/env python3
"""
analyze_io.py – Test whether Ceph fault injection caused a statistically
significant change in RBD read throughput.

Phases
------
  baseline  : analysis_start + WARMUP_S  →  t_fault_injected
  grace     : t_fault_injected           →  t_marked_out   (~600 s grace period)
  recovery  : t_marked_out               →  t_analysis_end (Ceph is actively
                                            redistributing data; analysis ends
                                            before Ceph finishes recovery)

Statistics
----------
  Per-phase: n, mean, median, std, 5th/95th pctile
  Mann-Whitney U (baseline vs grace, baseline vs recovery):
    U, p-value (two-sided), rank-biserial effect size r
  Interpretation of effect size: |r| < 0.1 negligible, < 0.3 small,
    < 0.5 medium, ≥ 0.5 large (Cohen 1988 / Rosenthal 1991)

Usage
-----
  # Analyse both runs (default)
  python3 scripts/analyze_io.py

  # Analyse a single experiment directory
  python3 scripts/analyze_io.py results/recovery-experiment-20260309_212043
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ── configuration ────────────────────────────────────────────────────────────
RESULTS_ROOT = Path(__file__).parent.parent / "results"
DEFAULT_DIRS = [
    "recovery-experiment-20260309_162628",
    "recovery-experiment-20260309_212043",
]
WARMUP_S = 15        # skip first N seconds of analysis (I/O ramp-up)
COLUMN   = "io_read_mb"   # metric to test


# ── helpers ──────────────────────────────────────────────────────────────────
def parse_ts(s: str) -> float:
    """Parse an ISO-8601 timestamp (with Z or ±HH:MM/±HHMM offset) to UTC epoch."""
    import re
    s = s.strip()
    # Python ≤3.10 doesn't parse 'Z' as UTC
    s = s.replace("Z", "+00:00")
    # Normalise ±HHMM → ±HH:MM  (e.g. -0600 → -06:00)
    s = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
    return datetime.fromisoformat(s).astimezone(timezone.utc).timestamp()


def load_experiment(exp_dir: Path) -> dict:
    with open(exp_dir / "results.json") as f:
        meta = json.load(f)

    no_fault = meta.get("no_fault", False)

    ts = {"t_analysis_start": parse_ts(meta["t_analysis_start"]),
          "t_analysis_end":   parse_ts(meta["t_analysis_end"]),
          "t_fault_injected": parse_ts(meta["t_fault_injected"]) if meta.get("t_fault_injected") else None,
          "t_marked_out":     parse_ts(meta["t_marked_out"])     if meta.get("t_marked_out")     else None}

    df = pd.read_csv(exp_dir / "perf.csv")
    df.columns = df.columns.str.strip()

    # drop ramp-up
    t0 = ts["t_analysis_start"]
    df = df[df["timestamp"] >= t0 + WARMUP_S].copy()

    # assign phase
    tf  = ts["t_fault_injected"]
    tmo = ts["t_marked_out"]
    te  = ts["t_analysis_end"]

    if no_fault:
        # entire run is one clean "baseline" phase
        df["phase"] = "baseline"
    else:
        def phase(t):
            if t < tf:
                return "baseline"
            elif tmo is None or t < tmo:
                return "grace"
            elif t <= te:
                return "recovery"
            else:
                return "post"
        df["phase"] = df["timestamp"].apply(phase)

    df["elapsed_s"] = df["timestamp"] - t0
    return {"meta": meta, "df": df, "ts": ts, "dir": exp_dir, "no_fault": no_fault}


def phase_stats(series: pd.Series, label: str) -> dict:
    a = series.dropna().values
    return {
        "label": label,
        "n": len(a),
        "mean":   float(np.mean(a)),
        "median": float(np.median(a)),
        "std":    float(np.std(a, ddof=1)) if len(a) > 1 else 0.0,
        "p05":    float(np.percentile(a,  5)),
        "p95":    float(np.percentile(a, 95)),
    }


def mwu(a: np.ndarray, b: np.ndarray) -> dict:
    """Mann-Whitney U + rank-biserial effect size."""
    if len(a) < 2 or len(b) < 2:
        return {"U": float("nan"), "p": float("nan"), "r": float("nan")}
    U, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    # rank-biserial r (Wendt 1972)
    r = 1 - 2 * U / (len(a) * len(b))
    return {"U": float(U), "p": float(p), "r": float(r)}


def effect_label(r: float) -> str:
    ar = abs(r)
    if ar < 0.10:  return "negligible"
    elif ar < 0.30: return "small"
    elif ar < 0.50: return "medium"
    else:           return "large"


def stars(p: float) -> str:
    if np.isnan(p):   return "n/a"
    if p < 0.001:     return "p<0.001 ***"
    if p < 0.010:     return f"p={p:.3f} **"
    if p < 0.050:     return f"p={p:.3f} *"
    return f"p={p:.3f} (n.s.)"


# ── reporting ─────────────────────────────────────────────────────────────────
def print_experiment(exp: dict):
    name = exp["dir"].name
    meta = exp["meta"]
    df   = exp["df"]
    no_fault = exp.get("no_fault", False)

    print(f"\n{'═'*66}")
    print(f"  Experiment : {name}")
    print(f"  Analysis   : {meta['t_analysis_start']}  →  {meta['t_analysis_end']}"
          f"  ({meta['duration_analysis_s']:.0f} s)")
    if no_fault:
        print(f"  Mode       : NO-FAULT (baseline/control run)")
    else:
        rec_s = meta.get("duration_recovery_s")
        rec_str = f"{rec_s:.0f}s" if rec_s is not None else "ongoing/unknown"
        grace_s = meta.get("duration_grace_period_s")
        grace_str = f"{grace_s:.0f}s" if grace_s is not None else "n/a"
        print(f"  Fault      : t+{meta['duration_fault_delay_s']:.0f}s"
              f"  |  Grace period: {grace_str}"
              f"  |  Recovery: {rec_str}")
    print(f"{'═'*66}")

    phases_order = ["baseline"] if no_fault else ["baseline", "grace", "recovery"]
    pstats = {}
    for ph in phases_order:
        sub = df.loc[df["phase"] == ph, COLUMN]
        pstats[ph] = phase_stats(sub, ph)

    # ── per-phase table ──────────────────────────────────────────────────────
    hdr = f"  {'Phase':<12}  {'n':>4}  {'mean':>7}  {'median':>7}  {'std':>7}  {'p5':>7}  {'p95':>7}"
    print(hdr)
    print(f"  {'-'*62}")
    for ph in phases_order:
        s = pstats[ph]
        print(f"  {s['label']:<12}  {s['n']:>4}  "
              f"{s['mean']:>7.2f}  {s['median']:>7.2f}  {s['std']:>7.2f}  "
              f"{s['p05']:>7.2f}  {s['p95']:>7.2f}  MB/s")

    # ── statistical tests ────────────────────────────────────────────────────
    print()
    base = df.loc[df["phase"] == "baseline",  COLUMN].dropna().values

    if no_fault:
        print(f"  No-fault run: n={len(base)} samples, "
              f"mean={np.mean(base):.2f}  median={np.median(base):.2f}  "
              f"std={np.std(base, ddof=1):.2f}  "
              f"p5={np.percentile(base,5):.2f}  p95={np.percentile(base,95):.2f}  MB/s")
        return

    grac = df.loc[df["phase"] == "grace",     COLUMN].dropna().values
    recy = df.loc[df["phase"] == "recovery",  COLUMN].dropna().values

    for label, arr in [("grace", grac), ("recovery", recy)]:
        mw = mwu(base, arr)
        delta = np.median(arr) - np.median(base) if len(arr) > 0 else float("nan")
        pct   = 100 * delta / np.median(base) if np.median(base) > 0 else float("nan")
        print(f"  baseline vs {label:<10}: "
              f"median Δ = {delta:+.2f} MB/s ({pct:+.1f}%)  "
              f"{stars(mw['p'])}  "
              f"effect r = {mw['r']:.3f} ({effect_label(mw['r'])})")

    print()
    # plain-language verdict
    for label, arr in [("grace", grac), ("recovery", recy)]:
        mw = mwu(base, arr)
        if len(arr) < 2:
            print(f"  [!] Too few samples in '{label}' phase to test.")
            continue
        delta = np.median(arr) - np.median(base)
        sig = not np.isnan(mw["p"]) and mw["p"] < 0.05
        slow = delta < 0
        if sig and slow:
            verdict = (f"YES – fault injection SLOWED I/O during {label} "
                       f"(median −{abs(delta):.1f} MB/s, {effect_label(mw['r'])} effect)")
        elif sig and not slow:
            verdict = (f"Significant but FASTER during {label} (unusual, "
                       f"median +{delta:.1f} MB/s)")
        else:
            verdict = f"NO significant slowdown during {label}  (not significant)"
        print(f"  ► {verdict}")


def print_pooled(experiments: list):
    print(f"\n{'═'*66}")
    print("  POOLED ANALYSIS (both runs combined)")
    print(f"{'═'*66}")

    frames = []
    for exp in experiments:
        df = exp["df"][exp["df"]["phase"].isin(["baseline", "grace", "recovery"])].copy()
        df["run"] = exp["dir"].name
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)

    phases_order = ["baseline", "grace", "recovery"]
    pstats = {}
    for ph in phases_order:
        sub = combined.loc[combined["phase"] == ph, COLUMN]
        pstats[ph] = phase_stats(sub, ph)

    hdr = f"  {'Phase':<12}  {'n':>4}  {'mean':>7}  {'median':>7}  {'std':>7}  {'p5':>7}  {'p95':>7}"
    print(hdr)
    print(f"  {'-'*62}")
    for ph in phases_order:
        s = pstats[ph]
        print(f"  {s['label']:<12}  {s['n']:>4}  "
              f"{s['mean']:>7.2f}  {s['median']:>7.2f}  {s['std']:>7.2f}  "
              f"{s['p05']:>7.2f}  {s['p95']:>7.2f}  MB/s")

    print()
    base = combined.loc[combined["phase"] == "baseline",  COLUMN].dropna().values
    grac = combined.loc[combined["phase"] == "grace",     COLUMN].dropna().values
    recy = combined.loc[combined["phase"] == "recovery",  COLUMN].dropna().values

    for label, arr in [("grace", grac), ("recovery", recy)]:
        mw = mwu(base, arr)
        delta = np.median(arr) - np.median(base) if len(arr) > 0 else float("nan")
        pct   = 100 * delta / np.median(base) if np.median(base) > 0 else float("nan")
        print(f"  baseline vs {label:<10}: "
              f"median Δ = {delta:+.2f} MB/s ({pct:+.1f}%)  "
              f"{stars(mw['p'])}  "
              f"effect r = {mw['r']:.3f} ({effect_label(mw['r'])})")

    print()
    for label, arr in [("grace", grac), ("recovery", recy)]:
        mw = mwu(base, arr)
        if len(arr) < 2:
            print(f"  [!] Too few samples in '{label}' phase to test.")
            continue
        delta = np.median(arr) - np.median(base)
        sig = not np.isnan(mw["p"]) and mw["p"] < 0.05
        slow = delta < 0
        if sig and slow:
            verdict = (f"YES – fault injection SLOWED I/O during {label} "
                       f"(median −{abs(delta):.1f} MB/s, {effect_label(mw['r'])} effect)")
        elif sig and not slow:
            verdict = f"Significant but FASTER during {label} (+{delta:.1f} MB/s)"
        else:
            verdict = f"NO significant slowdown during {label}  (not significant)"
        print(f"  ► {verdict}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        dirs = [Path(sys.argv[1]).resolve()]
    else:
        dirs = [RESULTS_ROOT / d for d in DEFAULT_DIRS]

    experiments = []
    for d in dirs:
        if not d.exists():
            print(f"[warning] directory not found: {d}", file=sys.stderr)
            continue
        print(f"Loading {d.name} ...", end=" ", flush=True)
        exp = load_experiment(d)
        experiments.append(exp)
        counts = exp["df"]["phase"].value_counts().to_dict()
        print(f"baseline={counts.get('baseline',0)}, "
              f"grace={counts.get('grace',0)}, "
              f"recovery={counts.get('recovery',0)} samples")

    if not experiments:
        sys.exit("No valid experiment directories found.")

    print(f"\nMetric : {COLUMN}  (3-second RBD read throughput samples)")
    print(f"Warmup : first {WARMUP_S}s of analysis excluded from baseline")

    for exp in experiments:
        print_experiment(exp)

    if len(experiments) > 1:
        print_pooled(experiments)

    print()


if __name__ == "__main__":
    main()
