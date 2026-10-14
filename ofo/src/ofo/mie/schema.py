"""Vocabulary and provenance of the Market Intelligence Engine.

EVIDENCE STATUS (never "PROVEN" for an empirical result):
  OBSERVED       measured on data, no test of a claim (a description)
  HYPOTHESIZED   a falsifiable claim exists, not tested yet
  TESTED         the claim was tested; the result does not (yet) settle it
  SUPPORTED      the test supports the claim on DEVELOPMENT data, after costs, stable, not concentrated, multiplicity-adjusted
  INCONCLUSIVE   not enough events or an interval that contains both 'nothing' and 'something useful'
  CONTRADICTED   the interval excludes the claim's direction

NOTE CLASS (what kind of knowledge a note holds; they are not equivalent truths):
  A empirical_observation   B untested_hypothesis   C validated_conclusion   D negative_result   E llm_interpretation

ACCESS LABEL (applied in code BEFORE any ranking, never left to a prompt):
  dev             derived from development data only            -> may reach the Researcher
  owner           written by the owner                           -> may reach the Researcher, as opinion, never as a result
  external        paper / web (untrusted text)                   -> may reach the Researcher as untrusted text
  locked_derived  contains anything from the hidden block (verdicts, locked P&L, deployment results) -> NEVER reaches the Researcher
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

EVIDENCE = ("OBSERVED", "HYPOTHESIZED", "TESTED", "SUPPORTED", "INCONCLUSIVE", "CONTRADICTED")
NOTE_CLASSES = {"empirical_observation": "A", "untested_hypothesis": "B", "validated_conclusion": "C", "negative_result": "D", "llm_interpretation": "E"}
ACCESS = ("dev", "owner", "external", "locked_derived")
RESEARCHER_ACCESS = ("dev", "owner", "external")
KINDS = ("observation", "structure_study", "behavior_study", "hypothesis", "mission", "strategy_study", "validation", "negative_finding", "verdict",
         "forward_observation", "monthly_report", "open_question", "conflict", "consolidation")
CONFIRMATION = ("PENDING", "CONFIRMED", "INVALIDATED")


def fingerprint(obj) -> str:
    """Stable identity of a DEFINITION (event definition, parameters, market): the same definition always gives the same id."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Observation:
    """One thing the Market Observer / Structure Engine saw. `available_at` is when it was KNOWABLE; a structure confirmed by later candles has
    detection_time/available_at at the confirming candle, never at the event."""
    market: str
    kind: str
    event_time: str            # when the thing happened (e.g. the swing bar)
    available_at: str          # earliest time at which it could have been known (e.g. k bars later)
    detection_time: str        # when the code detected it (the bar at which the rule fired)
    data_version: str          # fingerprint of the bars it was computed from
    confirmation_status: str = "CONFIRMED"
    session: str | None = None
    values: dict = field(default_factory=dict)
    definition: str | None = None    # fingerprint of the definition that produced it

    def to_dict(self) -> dict:
        return asdict(self)


def causal(obs: Observation) -> bool:
    """available_at must never precede event_time, and detection never precede availability."""
    return obs.event_time <= obs.available_at <= obs.detection_time
