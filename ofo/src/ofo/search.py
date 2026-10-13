"""Search-space design: how many configurations are tested, which ones, and how a promising region is refined.

A full Cartesian grid wastes budget on cosmetic variants of one idea. When a spec's grid is larger than the per-template maximum
(rules/research.json -> search_space[template].max_configs) the search is staged:
  1. COARSE GRID    every `coarse_step`-th value of each parameter (the shape of the surface);
  2. STRATIFIED RANDOM   a few extra points spread over the quantiles of every parameter (no cell of the space is left unseen);
  3. LOCAL REFINEMENT    the true one-step neighbours of the most promising points. "Promising" is judged by the NEIGHBOURHOOD
     (a point and its evaluated neighbours), never by the isolated peak: a spike whose neighbours lose money scores low.
Selection among candidates uses the same plateau-aware score, so a narrow peak cannot win just by having the highest Sharpe.
A grid that fits the maximum is simply tested whole. Nothing here looks at out-of-sample data."""
from __future__ import annotations

import itertools

import numpy as np


def axes_of(spec) -> dict:
    return {n: list(v) for n, v in spec.ranges.items()}


def key_of(combo: dict, axes: dict) -> tuple:
    return tuple(axes[n].index(combo[n]) for n in axes)


def combo_of(key: tuple, axes: dict) -> dict:
    return {n: axes[n][i] for n, i in zip(axes, key)}


def neighbours(key: tuple, axes: dict) -> list[tuple]:
    out = []
    for ax, n in enumerate(axes):
        for d in (-1, 1):
            j = key[ax] + d
            if 0 <= j < len(axes[n]):
                k = list(key); k[ax] = j
                out.append(tuple(k))
    return out


def plateau_scores(sharpes: dict, axes: dict) -> dict:
    """sharpes: {(tf, key): sharpe}. score = the LOWER of the point's Sharpe and the mean Sharpe of its evaluated one-step neighbours at the
    same timeframe. A spike scores at its neighbours' level, and so does the point next to a spike: only a region where the point AND its
    surroundings do well scores high. A point with no evaluated neighbour is unconfirmed and counts at half its Sharpe."""
    out = {}
    for (tf, k), s in sharpes.items():
        nb = [sharpes[(tf, n)] for n in neighbours(k, axes) if (tf, n) in sharpes]
        out[(tf, k)] = min(s, float(np.mean(nb))) if nb else 0.5 * s
    return out


def plan_search(spec, max_configs: int, cfg: dict | None = None, seed: int = 7) -> dict:
    """The first batch of combinations to test. Returns {"mode": "grid"|"staged", "initial": [combos], "axes", "refine_budget"}."""
    cfg = cfg or {}
    axes = axes_of(spec)
    allc = spec.combos()
    rng = np.random.default_rng(seed)
    if len(allc) <= max_configs:
        order = list(allc)
        rng.shuffle(order)                                  # random order: an early look at the first few is an unbiased sample
        return {"mode": "grid", "initial": order, "axes": axes, "refine_budget": 0}
    step = max(2, int(cfg.get("coarse_step", 2)))
    refine_share = float(cfg.get("refine_share", 0.25))
    initial_cap = max(4, int(round(max_configs * (1 - refine_share))))
    valid = {key_of(c, axes) for c in allc}
    coarse_idx = [sorted(set(list(range(0, len(axes[n]), step)) + [len(axes[n]) - 1])) for n in axes]
    coarse = [k for k in itertools.product(*coarse_idx) if k in valid]
    if len(coarse) > int(initial_cap * (1 - float(cfg.get("random_extra_fraction", 0.25)))):
        sel = rng.choice(len(coarse), int(initial_cap * (1 - float(cfg.get("random_extra_fraction", 0.25)))), replace=False)
        coarse = [coarse[i] for i in sorted(sel)]
    chosen = list(coarse)
    seen = set(chosen)
    extra = initial_cap - len(chosen)
    attempts = 0
    while extra > 0 and attempts < 50 * max(1, initial_cap):          # stratified random: one quantile stratum per sample, per axis
        attempts += 1
        u = (rng.permutation(initial_cap)[:1] + rng.random()) / initial_cap
        k = tuple(min(len(axes[n]) - 1, int(rng.random() * len(axes[n]))) if i else min(len(axes[n]) - 1, int(u[0] * len(axes[n])))
                  for i, n in enumerate(axes))
        if k in valid and k not in seen:
            seen.add(k); chosen.append(k); extra -= 1
    order = [combo_of(k, axes) for k in chosen]
    rng.shuffle(order)
    return {"mode": "staged", "initial": order, "axes": axes, "refine_budget": max_configs - len(order)}


def refine_points(sharpes: dict, axes: dict, valid: set, top: int, budget: int) -> list[tuple]:
    """Unevaluated one-step neighbours of the `top` best points by NEIGHBOURHOOD score, as [(tf, key)], at most `budget`."""
    sc = plateau_scores(sharpes, axes)
    out, seen = [], set(sharpes)
    for (tf, k), _ in sorted(sc.items(), key=lambda kv: -kv[1])[:top]:
        for n in neighbours(k, axes):
            if n in valid and (tf, n) not in seen and len(out) < budget:
                seen.add((tf, n)); out.append((tf, n))
    return out


def sharpe_upper_bound(s: float, n_days: int, z: float = 1.645) -> float:
    """One-sided upper confidence bound of a per-period Sharpe ratio (Lo 2002 normal approximation)."""
    return s + z * float(np.sqrt((1 + 0.5 * s * s) / max(n_days, 2)))


def spec_overlap(a: dict | None, b: dict | None) -> float:
    """Mean Jaccard overlap of the value sets of two parameter-range dicts (0 if they do not share the same parameters)."""
    if not a or not b or set(a) != set(b):
        return 0.0
    return float(np.mean([len(set(a[k]) & set(b[k])) / max(1, len(set(a[k]) | set(b[k]))) for k in a]))
