"""Learning / Knowledge Agent (spec section 22-24).

Closes the loop: records the outcome of a rejected/degraded strategy into
the failure memory (so the Hypothesis Agent won't re-propose an
identical hypothesis under identical conditions), and — when a post-mortem
diagnosis suggests a specific, actionable follow-up (e.g. "alpha_decay" ->
worth re-testing the hypothesis on more recent data; "regime_change" ->
worth adding a regime filter) — emits a follow-up hypothesis seed for the
next research cycle rather than a fresh, unguided one.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import new_id
from memory.failure_memory import find_similar_past_failure, record_research_failure
from strategies.schemas.hypothesis import Direction, Hypothesis, HypothesisStatus

from .base import Agent
from .postmortem_agent import Diagnosis

_FOLLOWUP_TEMPLATES = {
    "regime_change": "Re-test {statement} with an explicit regime filter added to the entry condition.",
    "alpha_decay": "Re-test {statement} on the most recent 6-12 months only, to check whether the "
                   "effect has weakened over time (consistent with alpha decay).",
    "execution_degradation": "Re-validate {statement} with a more conservative (2-3x) slippage assumption "
                              "before re-deploying; the edge may be real but execution was worse than assumed.",
}


class LearningAgent(Agent):
    name = "learning_agent"

    def record_hypothesis_rejection(self, session: Session, hypothesis: Hypothesis, reason: str) -> None:
        record_research_failure(
            session, paper_id=hypothesis.paper_id, hypothesis_id=hypothesis.hypothesis_id,
            reason=f"{hypothesis.statement} — {reason}",
            conditions={"expected_direction": hypothesis.expected_direction.value,
                        "expected_horizon": hypothesis.expected_horizon},
        )
        self.log_event(
            session, "hypothesis_rejected_recorded",
            {"hypothesis_id": hypothesis.hypothesis_id, "reason": reason},
            decision_log=f"Recorded rejection of hypothesis {hypothesis.hypothesis_id}: {reason}",
        )

    def check_prior_failure(self, session: Session, statement: str, conditions: dict):
        return find_similar_past_failure(session, statement, conditions)

    def propose_followup_hypothesis(
        self, session: Session, original: Hypothesis, diagnosis: Diagnosis
    ) -> Hypothesis | None:
        template = _FOLLOWUP_TEMPLATES.get(diagnosis.explanation)
        if template is None:
            return None
        followup = Hypothesis(
            hypothesis_id=new_id("hyp"),
            paper_id=original.paper_id,
            parent_hypothesis_ids=[original.hypothesis_id],
            statement=template.format(statement=original.statement),
            economic_rationale=f"Follow-up from post-mortem diagnosis '{diagnosis.explanation}': {diagnosis.evidence}",
            measurable_variables=original.measurable_variables,
            expected_direction=Direction.REGIME_DEPENDENT,
            expected_horizon=original.expected_horizon,
            falsification_criteria=original.falsification_criteria,
            required_dataset=original.required_dataset,
            statistical_test=original.statistical_test,
            status=HypothesisStatus.PROPOSED,
        )
        self.log_event(
            session, "followup_hypothesis_proposed",
            {"original_hypothesis_id": original.hypothesis_id, "new_hypothesis_id": followup.hypothesis_id,
             "diagnosis": diagnosis.explanation},
            decision_log=f"Proposed follow-up hypothesis {followup.hypothesis_id} from diagnosis {diagnosis.explanation!r}.",
        )
        return followup
