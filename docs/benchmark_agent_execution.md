# Benchmark Agent Execution

## Purpose

This document replaces the old "gold dataset as primary benchmark" model with an
artifact-first execution contract for the next benchmark agent.

The benchmark is now expected to answer:

1. How does the live system behave against the current corpus?
2. How well do PromptSpec-driven response paths perform across supported modes?
3. How reliable are routing, fallback, caching, streaming, and circuit-breaking?
4. Are the artifacts strong enough to support an engineering readiness decision?

## Current Status

- Local Supabase corpus is the authoritative benchmark corpus for this environment.
- Verified local `knowledge_chunks` count: `237,954`.
- Benchmark preflight can now validate corpus access, retrieval availability, and
  deterministic routing before a long run begins.
- LLM intent-classifier fallback path is repaired and validated live.
- The current `scripts/benchmark_e2e.py` still contains a legacy retrieval
  regression slice based on `evaluation/queries.json` and `ground_truth.json`.
- That legacy slice is now diagnostic only. It is not the primary benchmark for
  an evolving corpus.

## Legacy Cleanup Rules

- Do not treat `evaluation/queries.json` plus `ground_truth.json` as the main
  measure of retrieval quality.
- Do not make readiness claims from legacy report text that predates the local
  pgvector repair or the classifier fix.
- Keep old retrieval fixtures only as a narrow regression check for matching and
  evaluator behavior.
- Treat `output/benchmark_*` directories as run artifacts, not source of truth.

## New Benchmark Shape

The next agent should evolve the benchmark into a scenario-driven, PromptSpec-
aware system with live corpus evaluation.

### Primary benchmark axes

- `task`: `explain`, `compare`, `brainstorm`, `analyze`, `summarize`
- `depth`: `simple`, `accessible`, `technical`, `expert`
- `reasoning`: `direct`, `guided`, `socratic`, `debate`
- provider path: routed, forced direct provider, forced fallback path
- retrieval path: retrieval mode, search depth, rerank on/off, context assembly

### Scenario families

- factual
- implementation
- multi-hop
- synthesis
- ambiguous
- adversarial
- noisy
- retrieval-heavy
- long-context
- failure-injection

### Evaluation dimensions

- retrieval relevance and coverage
- response correctness and grounding
- PromptSpec adherence
- routing and classification correctness
- fallback and retry behavior
- cache behavior
- circuit breaker behavior
- streaming integrity
- latency, throughput, and failure rate

## Required Artifacts

Each benchmark run should persist the following:

- `benchmark_metadata.json`
- `scenario_catalog.json`
- `retrieval_results.json`
- `generation_results.json`
- `classification_results.json`
- `routing_results.json`
- `fallback_results.json`
- `cache_and_stream_results.json`
- `scalability_results.json`
- `judge_evaluations.json`
- `errors.json`
- `failures.json`
- `benchmark_report.md`

## Metadata Contract

Every run must record:

- corpus source
- corpus size
- provider map
- active RAG backend
- benchmark mode flag
- routing determinism status
- retrieval availability preflight
- judge pipeline preflight
- scenario coverage counts

## Integrity Gates

Abort the benchmark if any of the following fail:

- corpus preflight
- retrieval availability preflight
- judge pipeline preflight
- deterministic routing preflight

Do not silently downgrade these to warnings.

## Recommended Refactor Shape

Keep `scripts/benchmark_e2e.py` as the top-level orchestrator and extract the
moving parts into modules:

- scenario catalog builder
- retrieval evaluator
- judge adapter
- provider/routing telemetry helpers
- report generation

This avoids turning the script into a larger monolith while preserving the
current invocation surface.

## Sprint-Carried Technical Facts

- Local Supabase REST endpoint: `http://127.0.0.1:54321`
- Local pgvector-backed corpus is reachable and benchmarkable
- SQL ambiguity fixes were added for:
  - `hybrid_search_v5` / `hybrid_search_trusted_v5`
  - `get_neighbor_chunks`
- Explicit model selection is authoritative in technical mode
- Streaming now fails empty streams even if `[DONE]` is emitted
- `llm_client` emits benchmark-grade provider telemetry

## Immediate Next Tasks

1. Demote the legacy retrieval gold set to a regression suite in code and report
   text.
2. Introduce a scenario catalog driven by PromptSpec coverage.
3. Add scenario-level judge rubrics for retrieval, answer quality, and
   PromptSpec adherence.
4. Make direct-provider benchmark runs strict and alias-normalized.
5. Update the final report layout to separate:
   - legacy regression diagnostics
   - live corpus benchmark results
   - operational reliability results

## Exit Criteria For The Next Agent

The next benchmark iteration is complete only when:

- the benchmark no longer relies on the legacy gold set as its primary score,
- scenario coverage is explicit and persisted,
- retrieval, generation, routing, and reliability are scored separately,
- the final report can defend its claims against the active live corpus.
