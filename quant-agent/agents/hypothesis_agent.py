"""Hypothesis Generation Agent + Statistical Research Agent (spec section 6/48).

`generate_hypotheses` turns one paper's understanding into several
falsifiable, independently-testable hypotheses (never a strategy directly
— see module docstring in `strategies/schemas/hypothesis.py`). Uses the
LLM when configured; otherwise a template-based generator derives
variations (regime-conditioning, transaction-cost survival, session
dependence) mechanically from the paper's own extracted signals, which is
honest but far less creative than an LLM-driven version.

`test_hypothesis` is the "actively attempt to DISPROVE its own hypotheses"
step: a real OLS regression of forward returns on the hypothesis's signal,
with Newey-West (HAC) standard errors because financial returns are
serially correlated and naive OLS SEs would overstate significance.
"""
from __future__ import annotations

import uuid

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sqlalchemy.orm import Session

from database.models import new_id
from strategies.schemas.hypothesis import Direction, Hypothesis, HypothesisStatus, HypothesisTestResult
from strategies.schemas.research import PaperUnderstanding

from .base import Agent
from research.extraction.llm_client import LLMResponseError, get_llm_client

_SYSTEM_PROMPT = """You are a skeptical quantitative researcher. Given a paper's extracted facts \
and interpretations, generate up to 5 FALSIFIABLE hypotheses derived from (but more specific than) \
the paper's finding — e.g. regime-conditioning, transaction-cost survival, session dependence, \
horizon sensitivity. Return a JSON list of objects with keys: statement, economic_rationale, \
measurable_variables (list of strings), expected_direction (one of "positive","negative", \
"nonlinear","regime_dependent"), expected_horizon, falsification_criteria, required_dataset, \
statistical_test. Return ONLY the JSON list."""


class HypothesisAgent(Agent):
    name = "hypothesis_agent"

    def generate_hypotheses(self, paper_id: str, understanding: PaperUnderstanding, n: int = 5) -> list[Hypothesis]:
        client = get_llm_client()
        if client is not None:
            try:
                return self._generate_with_llm(paper_id, understanding, client, n)
            except LLMResponseError:
                pass
        return self._generate_template(paper_id, understanding, n)

    def _generate_with_llm(self, paper_id, understanding, client, n) -> list[Hypothesis]:
        prompt = (
            f"Facts: {understanding.facts.model_dump_json()}\n"
            f"Interpretations: {understanding.interpretations.model_dump_json()}\n"
            f"Generate up to {n} hypotheses."
        )
        raw = client.complete_json(_SYSTEM_PROMPT, prompt)
        items = raw if isinstance(raw, list) else raw.get("hypotheses", [])
        out = []
        for item in items[:n]:
            out.append(Hypothesis(hypothesis_id=new_id("hyp"), paper_id=paper_id, **item))
        return out

    def _generate_template(self, paper_id: str, understanding: PaperUnderstanding, n: int) -> list[Hypothesis]:
        signals = understanding.facts.signals or understanding.facts.features or ["the researched signal"]
        base_signal = signals[0]
        horizon = understanding.facts.timeframe or "5m"
        templates = [
            (
                f"{base_signal} predicts the next-{horizon} return in the researched market.",
                "positive", "This is the paper's core claim, restated as a directly testable statement.",
            ),
            (
                f"The predictive power of {base_signal} increases during high-volatility regimes.",
                "regime_dependent", "Signals tied to liquidity/order-flow mechanics are commonly regime-dependent.",
            ),
            (
                f"{base_signal} is stronger near session open/close than mid-session.",
                "regime_dependent", "Microstructure effects often concentrate at liquidity extremes.",
            ),
            (
                f"{base_signal}'s edge disappears after realistic transaction costs.",
                "negative", "Statistical significance does not imply economic significance after costs.",
            ),
            (
                f"{base_signal} works only during specific market sessions (e.g. RTH vs. overnight).",
                "regime_dependent", "Liquidity and participant composition vary strongly by session.",
            ),
        ]
        out = []
        for statement, direction, rationale in templates[:n]:
            out.append(
                Hypothesis(
                    hypothesis_id=new_id("hyp"),
                    paper_id=paper_id,
                    statement=statement,
                    economic_rationale=rationale,
                    measurable_variables=[base_signal, "forward_return"],
                    expected_direction=Direction(direction),
                    expected_horizon=horizon,
                    falsification_criteria=(
                        "Reject if the OLS slope of forward_return on the signal is not significant "
                        "at the 5% level (Newey-West HAC SEs), or has the wrong sign."
                    ),
                    required_dataset=f"{understanding.facts.assets or ['the researched market']} OHLCV at {horizon}",
                    statistical_test="OLS with Newey-West (HAC) standard errors",
                )
            )
        return out

    def test_hypothesis(
        self, hypothesis: Hypothesis, bars: pd.DataFrame, signal_col: str = "signal", horizon_bars: int = 1
    ) -> HypothesisTestResult:
        """Regresses the forward return on `signal_col`. This is the
        concrete falsification test: a hypothesis with a nonsignificant or
        wrong-signed slope is marked FALSIFIED, not "inconclusive" — the
        system defaults to skepticism.
        """
        if signal_col not in bars.columns:
            raise ValueError(f"Signal column {signal_col!r} not present in bars — cannot test hypothesis.")

        forward_return = bars["close"].pct_change(horizon_bars).shift(-horizon_bars)
        df = pd.DataFrame({"y": forward_return, "x": bars[signal_col]}).dropna()
        if len(df) < 100:
            return HypothesisTestResult(
                hypothesis_id=hypothesis.hypothesis_id, test_name="OLS-HAC", statistic=0.0, p_value=1.0,
                sample_size=len(df), conclusion=HypothesisStatus.INCONCLUSIVE,
                notes=f"Only {len(df)} paired observations — insufficient sample size.",
            )

        X = sm.add_constant(df["x"])
        model = sm.OLS(df["y"], X).fit(cov_type="HAC", cov_kwds={"maxlags": max(1, horizon_bars)})
        slope = float(model.params["x"])
        p_value = float(model.pvalues["x"])

        expected_sign = 1 if hypothesis.expected_direction == Direction.POSITIVE else (
            -1 if hypothesis.expected_direction == Direction.NEGATIVE else 0
        )
        sign_ok = expected_sign == 0 or np.sign(slope) == expected_sign

        if p_value < 0.05 and sign_ok:
            conclusion = HypothesisStatus.SUPPORTED
        elif p_value < 0.05 and not sign_ok:
            conclusion = HypothesisStatus.FALSIFIED
        else:
            conclusion = HypothesisStatus.FALSIFIED

        return HypothesisTestResult(
            hypothesis_id=hypothesis.hypothesis_id, test_name="OLS-HAC (Newey-West)",
            statistic=slope, p_value=p_value, effect_size=slope, sample_size=len(df),
            conclusion=conclusion,
            notes=f"slope={slope:.6f}, p={p_value:.4f}, expected_sign={hypothesis.expected_direction.value}",
        )
