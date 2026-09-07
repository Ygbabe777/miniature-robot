# Quant Agent — Autonomous AI Quant Research Lab

An autonomous system that discovers academic research, extracts falsifiable
hypotheses, generates and backtests trading strategy candidates, subjects
them to statistical/robustness validation, and — only after paper trading
and (by default) explicit human approval — promotes them toward live
execution. Full lineage from `paper -> hypothesis -> strategy -> experiment
-> backtest -> validation -> paper trade -> live trade` is recorded for
every decision.

## Design philosophy

This is a **research lab**, not a signal generator. The system optimizes for

> the simplest economically plausible hypothesis that demonstrates
> statistically significant, robust, repeatable out-of-sample evidence
> after realistic costs

rather than "highest historical return". See `docs` inline in each module's
docstring for the specific anti-overfitting and lineage guarantees enforced
by that module.

## Status of this build (read before trusting anything)

This repository ships a working **Phase 1 + Phase 2** implementation (see
`46. FIRST MVP...` / `44. MVP` phases in the original spec) plus scaffolding
for Phase 3/4. Concretely:

| Component | Status |
|---|---|
| Research discovery (arXiv) | **Implemented** — real arXiv API client, no key required |
| Research discovery (SSRN/NBER/Crossref/Semantic Scholar/journals) | `TODO: REQUIRES API` — most of these have no free/legal scraping API; wire up official APIs/keys before enabling |
| Paper scoring | Implemented (heuristic, configurable weights) |
| Paper understanding / knowledge extraction | Implemented with a pluggable `LLMClient`. Falls back to a deterministic rule-based extractor when no LLM API key is configured (`TODO: REQUIRES API` for high-quality extraction) |
| Knowledge graph | Implemented as relational tables (Postgres). `TODO: REQUIRES API` for pgvector/Qdrant embeddings — falls back to bag-of-words similarity when no embedding backend is configured |
| Hypothesis generation | Implemented — template + LLM-assisted, always falsifiable-schema-validated |
| Strategy schema + generator | Implemented |
| Backtesting engine | Implemented — event-driven, bar-level, cost/slippage-aware |
| Walk-forward / Monte Carlo / overfitting (deflated Sharpe, PBO) | Implemented |
| Robustness scoring & promotion thresholds | Implemented, fully configurable |
| Paper trading | Implemented (`PaperBroker`, deterministic simulated fills) |
| Live brokers (IBKR/Tradovate/NinjaTrader) | `TODO: REQUIRES API` — interfaces defined, not implemented. **Do not enable `FULL_AUTO_LIVE` — it is refused by config validation.** |
| Risk manager & kill switch | Implemented |
| Experiment tracking / lineage | Implemented (Postgres tables + optional MLflow) |
| Dashboard | Minimal read-only HTML/JS skeleton against the API — `TODO` for full React dashboard |
| Human approval gate | Implemented as a hard state-machine gate; default mode is `HUMAN_APPROVAL` |

Nothing here fakes a result it cannot produce: anywhere the system would
need data/APIs it doesn't have, it raises `NotImplementedError` with a
`TODO: REQUIRES ...` message rather than returning fabricated numbers.

## Architecture

```
RESEARCH PAPERS
 -> EXTRACT KNOWLEDGE -> GENERATE HYPOTHESES -> DESIGN STRATEGIES
 -> GENERATE CODE -> BACKTEST -> STATISTICAL VALIDATION -> ROBUSTNESS
 -> WALK-FORWARD -> OUT-OF-SAMPLE -> PAPER TRADING -> LIVE DEPLOYMENT
 -> MONITORING -> PERFORMANCE ANALYSIS -> LEARNING -> new hypotheses
```

See `docs/architecture.md`-equivalent inline docs in `agents/orchestrator.py`
for the full event-driven wiring, and `database/models.py` for the schema
that gives every live strategy a complete, queryable lineage back to the
paper that inspired it.

## Directory structure

```
quant-agent/
  agents/            agent interfaces + one module per agent in the pipeline
  data/               DataProvider abstraction (CSV/Parquet/DB/broker)
  research/           discovery, ingestion, extraction, embeddings
  strategies/         Pydantic schemas, generator, registry, versions
  backtesting/        event-driven engine, execution sim, costs, metrics
  validation/         walk-forward, Monte Carlo, robustness, overfitting
  portfolio/          correlation, optimizer, portfolio-level risk
  execution/          broker abstraction + PaperBroker, orders, positions
  risk/               RiskManager + kill switch
  monitoring/         performance tracking, alerts, health checks
  memory/             research/strategy/failure memory (institutional memory)
  database/           SQLAlchemy models + session management
  api/                FastAPI app exposing the dashboard + control endpoints
  dashboard/          minimal read-only dashboard (static HTML/JS)
  tests/              unit / integration / safety tests
  config/             Settings (pydantic-settings) + promotion thresholds
  scripts/            example end-to-end workflow
```

## Quickstart

```bash
cd quant-agent
cp .env.example .env          # never commit real keys
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d postgres redis   # optional but recommended
python -m scripts.example_workflow    # runs the full pipeline on synthetic data (~4-5 min)
uvicorn api.main:app --reload         # dashboard + API on :8000
```

`scripts/example_workflow.py` runs the entire
`discover -> extract -> hypothesize -> strategize -> backtest -> validate ->
paper-trade` loop end-to-end using synthetic OHLCV data so it works with
**zero external API keys**. It takes several minutes because the
backtesting engine is a genuine bar-by-bar event-driven simulator (not a
vectorized shortcut) and the anti-overfitting battery runs dozens of
re-backtests (parameter perturbations + randomized-entry Monte Carlo) per
strategy variant — that cost is the point, not a bug. In the shipped run,
every generated strategy variant is correctly **REJECTED**: the injected
synthetic signal is statistically real (hypotheses test as SUPPORTED, p ≈
0) but too small to survive realistic transaction costs, which is exactly
the "statistical vs. economic significance" distinction spec section 14
asks for — a system that always finds a way to approve a strategy would be
the failure mode, not a passing demo. Point `DataProvider` at real
historical data and set `ANTHROPIC_API_KEY` (or another LLM backend) to get
real paper understanding and hypothesis generation instead of the
rule-based fallback.

## Operating modes (config.mode, see `config/settings.py`)

* `FULL_AUTO_RESEARCH` — autonomous research + backtesting only.
* `AUTO_PAPER` — additionally auto-deploys robust candidates to paper trading.
* `HUMAN_APPROVAL` — **default**. Live deployment always requires an explicit
  human approval action via the API (`POST /deployments/{id}/approve`).
* `FULL_AUTO_LIVE` — refused unless `ALLOW_FULL_AUTO_LIVE=true` is set
  **and** a real, tested broker implementation is registered. As shipped,
  no live broker is implemented, so this mode cannot actually place live
  orders regardless of the flag.

## Known limitations / explicit TODOs

Grep the tree for `TODO: REQUIRES` to get the authoritative, current list —
it is kept in the code next to the exact function that can't be completed
without external access, e.g.:

* `research/discovery/ssrn_source.py` — SSRN has no public API; requires a
  licensed data partnership or manual export.
* `execution/brokers/interactive_brokers.py`,
  `tradovate.py`, `ninjatrader.py` — require broker API credentials and a
  live-market connectivity test before they can be trusted with real orders.
* `research/embeddings/` — requires an embedding provider (OpenAI/Anthropic/
  local sentence-transformers) or a running pgvector/Qdrant instance.
