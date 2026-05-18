# Benchmark Legacy Cleanup

## What Is Legacy

The following are legacy benchmark elements and should not be treated as the
primary readiness signal:

- fixed retrieval gold-set expectations tied to `evaluation/queries.json`
- report language written before local pgvector restoration
- report language written before the LLM intent-classifier fallback repair
- stale benchmark claims copied forward from earlier `output/benchmark_*` runs

## What Stays

Legacy fixtures still have value as:

- evaluator regression checks
- content-hash matching diagnostics
- retrieval trace debugging aids

They do not stay as the main benchmark score.

## Cleanup Outcome For This Sprint

- local pgvector corpus restored as the benchmark source
- benchmark report addendum corrected the most important stale claims
- harness preflights now detect missing retrieval and routing integrity issues
- execution guidance moved to `docs/benchmark_agent_execution.md`

## Rules For Future Reports

- state corpus source explicitly
- state corpus size explicitly
- separate legacy regression findings from live benchmark findings
- do not claim classifier staleness unless the live path is failing again
- do not claim retrieval unavailability when the actual problem is evaluator or
  corpus-alignment mismatch
