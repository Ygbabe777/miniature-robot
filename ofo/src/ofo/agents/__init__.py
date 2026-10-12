"""LLM agents of OFO. Agents PROPOSE and EXPLAIN; deterministic code DECIDES.

  Researcher : text (paper, idea) -> strategy specs            (papers.py)
  Planner    : dev-only study summary -> next step             (roles.py)
  Auditor    : full validation report -> critique in Italian   (roles.py)
  Judge      : NOT an LLM. judge.py decides APPROVED / REJECTED / INSUFFICIENT_EVIDENCE.
"""
