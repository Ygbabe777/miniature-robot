"""Quant Coding Agent (spec section 8/42).

Rather than generating free-form Python (which per spec section 42 must
be sandboxed, tested, lint/type-checked, resource-limited, and unable to
reach unauthorized systems — a much larger undertaking than an MVP should
attempt to fake), this agent's job is to *compile* a `StrategySpec` via
`strategies.generator.compile_strategy` (which only ever evaluates a
whitelisted-AST expression language) and additionally render a
human-readable Python source string purely for audit/version-control
purposes. That rendered string is NEVER executed — it exists so a human
reviewer or `git diff` can see what a strategy version "does" in familiar
syntax.
"""
from __future__ import annotations

import pandas as pd

from strategies.generator import CompiledStrategy, compile_strategy
from strategies.schemas.strategy import StrategySpec

from .base import Agent


class CodingAgent(Agent):
    name = "coding_agent"

    def compile(self, spec: StrategySpec, bars: pd.DataFrame) -> CompiledStrategy:
        return compile_strategy(spec, bars)

    def render_readable_source(self, spec: StrategySpec) -> str:
        """Human-readable-only rendering — NOT executed by anything in this
        system. The engine always runs the sandboxed `CompiledStrategy`."""
        lines = [
            f"# Strategy: {spec.name} (id={spec.strategy_id}, v{spec.version})",
            f"# Market={spec.market} Timeframe={spec.timeframe}",
            f"# Hypothesis: {spec.hypothesis_id}",
            "",
            "def signal(row, params):",
            f"    return ({' and '.join(spec.signal_logic.all_of) or 'True'})"
            + (f" or ({' or '.join(spec.signal_logic.any_of)})" if spec.signal_logic.any_of else ""),
            "",
            "def entry(row, params):",
            f"    return ({' and '.join(spec.entry_logic.all_of) or 'True'})"
            + (f" or ({' or '.join(spec.entry_logic.any_of)})" if spec.entry_logic.any_of else ""),
            "",
            "def exit(row, params):",
            f"    return ({' and '.join(spec.exit_logic.all_of) or 'False'})"
            + (f" or ({' or '.join(spec.exit_logic.any_of)})" if spec.exit_logic.any_of else ""),
            "",
            f"# risk_model: {spec.risk_model.model_dump()}",
            f"# parameters: {spec.parameters}",
        ]
        return "\n".join(lines)
