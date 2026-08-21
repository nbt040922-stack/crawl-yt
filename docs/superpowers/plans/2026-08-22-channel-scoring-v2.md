# Channel Scoring v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace channel scoring v1 with deterministic local-only v2 scoring that rewards sustainable upload cadence while preserving existing storage and lifecycle behavior.

**Architecture:** Keep `ChannelScore.activity_score` as the persisted cadence component for compatibility, add nullable cadence columns to `channel_scores` for SQL filtering/sorting, and centralize cadence/relevance/maturity helpers in the channel scoring module. Repository queries expose persisted cadence to the channels UI; existing lifecycle calls automatically persist v2 after discovery, crawl, and enrichment.

**Tech Stack:** Python standard library, SQLite, FastAPI/Jinja, unittest.

**Spec:** User-provided Channel Scoring v2 requirements in `pasted-text.txt`.

## Global Constraints

- Scoring version is exactly `v2`; video scoring remains `v1`.
- Formula weights are relevance 25%, cadence 35%, traction 25%, confidence/consistency 15%.
- Scoring performs zero network calls and is deterministic with an injected `now`.
- Existing v1 rows remain readable; migrations are additive and non-destructive.
- No LLMs, embeddings, semantic clustering, uncontrolled crawling, scheduler, Redis/Celery, React, or deployment work.

### Task 1: Define v2 scoring helpers and red tests

**Files:** Modify `tests/test_channel_scoring.py`; modify `src/crawl_yt/discovery/channel_scoring.py`.

- [ ] Add failing tests for cadence anchors, above-target decay, cadence labels, maturity/unknown cadence, relevance progression, 30/90 weighting, consistency, robust median traction, and v2 persistence.
- [ ] Run the focused tests and verify they fail for the expected missing v2 behavior.
- [ ] Implement centralized constants/functions and v2 score calculation using existing repository signals.
- [ ] Run focused tests until green.

### Task 2: Add additive cadence persistence and query support

**Files:** Modify `src/crawl_yt/database/repository.py`; modify `tests/test_channel_scoring.py`.

- [ ] Add nullable `cadence_score`, `videos_per_week_30d`, and `videos_per_week_90d` columns plus one cadence index through additive migration.
- [ ] Read/write those columns while retaining all old `ChannelScore` fields and v1 compatibility.
- [ ] Add SQL-backed cadence filtering and sorting to channel page/count queries.
- [ ] Test migration from an old schema and persisted v2 values.

### Task 3: Update human-readable channel UI

**Files:** Modify `src/crawl_yt/web/app.py`, templates, CSS, and `tests/test_web.py`.

- [ ] Add cadence filter and sort query parameters backed by repository SQL.
- [ ] Display v2 components, 30/90 rates, cadence fit, consistency, traction, maturity, and “Not enough data” for unknown cadence.
- [ ] Add channels table cadence column and test filtering/sorting without network calls.

### Task 4: Lifecycle regression and documentation

**Files:** Modify lifecycle tests and `README.md`.

- [ ] Confirm discovery/crawl/enrichment lifecycle persists v2 and preserves nonfatal scoring failures.
- [ ] Keep manual re-score behavior and video scorer version unchanged.
- [ ] Document formula, cadence business interpretation, maturity/unknown policy, and related-keyword limitation.
- [ ] Run the complete suite and inspect `git diff --check`, status, and diff stat.
