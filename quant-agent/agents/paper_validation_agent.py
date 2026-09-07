"""Paper Validation Agent (spec section 37).

Runs the ten research-quality-control questions against a paper's score
breakdown + abstract text. This is a coarse, abstract-only check —
several questions (independent replication, arbitrage-away risk) genuinely
require the full paper text or domain judgment a human should weigh in on;
those are returned as `"REQUIRES_HUMAN_REVIEW"` rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

from database.models import Paper

from .base import Agent


@dataclass
class QualityControlAnswer:
    question: str
    answer: str  # "YES" | "NO" | "REQUIRES_HUMAN_REVIEW"
    rationale: str


QUESTIONS = [
    "Is the source legitimate?",
    "Is the methodology sound?",
    "Can the result be independently replicated?",
    "Is the effect economically meaningful?",
    "Does the effect survive transaction costs?",
    "Does the effect survive different market regimes?",
    "Does the effect survive out-of-sample testing?",
    "Could the result be caused by data mining?",
    "Is the effect likely arbitraged away?",
    "Is the effect still theoretically plausible today?",
]


class PaperValidationAgent(Agent):
    name = "paper_validation_agent"

    def evaluate(self, paper: Paper) -> list[QualityControlAnswer]:
        breakdown = paper.score_breakdown or {}
        answers = []

        answers.append(QualityControlAnswer(
            QUESTIONS[0], "YES" if breakdown.get("source_quality", 0) >= 0.5 else "NO",
            f"source_quality={breakdown.get('source_quality')}",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[1], "YES" if breakdown.get("methodological_rigor", 0) >= 0.5 else "NO",
            f"methodological_rigor={breakdown.get('methodological_rigor')}",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[2], "YES" if breakdown.get("reproducibility", 0) >= 0.6 else "REQUIRES_HUMAN_REVIEW",
            f"reproducibility={breakdown.get('reproducibility')}",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[3], "REQUIRES_HUMAN_REVIEW",
            "Economic significance can only be judged after backtesting with realistic costs.",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[4], "REQUIRES_HUMAN_REVIEW", "Determined later by transaction_cost_stress_test, not at paper-review time.",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[5], "REQUIRES_HUMAN_REVIEW", "Determined later by walk-forward / regime analysis.",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[6], "REQUIRES_HUMAN_REVIEW", "Determined later by the OOS backtest split.",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[7], "YES" if breakdown.get("statistical_significance", 0) < 0.4 else "NO",
            f"statistical_significance={breakdown.get('statistical_significance')} "
            "(low reported significance raises data-mining concern)",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[8], "REQUIRES_HUMAN_REVIEW",
            "Arbitrage-away risk requires judgment about how well-known/tradeable the effect is.",
        ))
        answers.append(QualityControlAnswer(
            QUESTIONS[9], "YES" if breakdown.get("recency", 0) >= 0.4 else "REQUIRES_HUMAN_REVIEW",
            f"recency={breakdown.get('recency')}",
        ))
        return answers

    def run(self, session, paper: Paper) -> list[QualityControlAnswer]:
        answers = self.evaluate(paper)
        summary = "; ".join(f"{a.question} -> {a.answer}" for a in answers)
        self.log_event(session, "paper_quality_control", {"paper_id": paper.paper_id, "answers": [a.__dict__ for a in answers]},
                        decision_log=f"Paper {paper.paper_id}: {summary}")
        return answers
