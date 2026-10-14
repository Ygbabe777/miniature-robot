"""Win / loss streaks of a series of results (trades, or days). A zero result is neither a win nor a loss: it breaks both streaks."""
from __future__ import annotations


def runs(values, positive: bool) -> list[int]:
    out, cur = [], 0
    for v in values:
        hit = v > 0 if positive else v < 0
        if hit:
            cur += 1
        elif cur:
            out.append(cur); cur = 0
    if cur:
        out.append(cur)
    return out


def streak_stats(values) -> dict:
    """{'n', 'win': {'avg','max','count'}, 'loss': {...}}: average and longest run of consecutive wins / losses, and how many runs there were."""
    x = [float(v) for v in values if v == v]
    def pack(r):
        return {"avg": (sum(r) / len(r)) if r else None, "max": max(r) if r else 0, "count": len(r)}
    return {"n": len(x), "win": pack(runs(x, True)), "loss": pack(runs(x, False))}
