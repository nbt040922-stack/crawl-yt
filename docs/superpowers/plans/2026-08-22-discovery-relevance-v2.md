# Discovery Relevance v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded channel-level topic gate to Discovery without changing scoring or crawl behavior.

**Architecture:** Add a pure relevance evaluator and policy constants, extend the discovery provider with lightweight verification, and make `DiscoveryService` persist only accepted candidates while returning explainable evidence for the UI.

**Tech Stack:** Python standard library, dataclasses, existing yt-dlp provider, SQLite repository, FastAPI/Jinja2, unittest.

**Spec:** `docs/superpowers/specs/2026-08-22-discovery-relevance-v2-design.md`

## Global Constraints

- No LLM, embeddings, semantic model, transcripts, comments, YouTube Data API, or background workers.
- Do not modify Channel Scoring v2, crawl, scheduling, batch, or Excel logic.
- Verification samples at most 20 recent video titles per unique candidate.
- Candidate cap is accepted limit multiplied by 5.
- Balanced is default; thresholds are Strict 60%, Balanced 40%, Broad 25%.
- Dry-run writes zero canonical channels and provenance rows.

### Task 1: Relevance evaluator

**Files:** Create `src/crawl_yt/discovery/relevance.py`; Test `tests/test_discovery_relevance.py`.

- [ ] Write failing tests for exact threshold boundaries, generic token protection, related terms, identity exception, and rejection reasons.
- [ ] Run the focused test file and confirm failures come from missing evaluator behavior.
- [ ] Implement normalized terms, deterministic title matching, identity signal, centralized policies, and `evaluate_channel_topic(...)`.
- [ ] Run focused tests until green.

### Task 2: Provider verification boundary

**Files:** Modify `src/crawl_yt/discovery/channel_discovery.py` and `src/crawl_yt/discovery/ytdlp_provider.py`; Test `tests/test_discovery.py`.

- [ ] Add a verification result structure and optional provider method for channel identity plus recent titles.
- [ ] Add fake-provider tests proving one verification call per deduplicated candidate and failures are isolated.
- [ ] Implement yt-dlp lightweight verification with bounded playlist extraction and no downloads.
- [ ] Run discovery provider tests.

### Task 3: Service gate and persistence

**Files:** Modify `src/crawl_yt/discovery/channel_discovery.py`; Test `tests/test_discovery.py`.

- [ ] Add tests for accepted/rejected persistence, existing-channel rejection, accepted-limit continuation, candidate cap, dry-run, and isolated search hits.
- [ ] Run tests to observe failures.
- [ ] Implement candidate inspection loop, report evidence, fail-closed verification, and accepted-only upsert/provenance.
- [ ] Confirm scoring lifecycle receives only accepted IDs and run regression discovery tests.

### Task 4: Web form and result presentation

**Files:** Modify `src/crawl_yt/web/app.py`, `src/crawl_yt/web/templates/form.html`, `src/crawl_yt/web/templates/discovery_result.html`, `src/crawl_yt/web/static/app.css`; Test `tests/test_web.py`.

- [ ] Add network-free tests for mode/related-term form handling, summary counts, evidence columns, rejected section, dry-run, and source links.
- [ ] Implement structured rendering and validation while preserving existing generic result behavior.
- [ ] Run all web tests.

### Task 5: Full regression verification

- [ ] Run `& .venv\\Scripts\\python.exe -m unittest discover -s tests -q`.
- [ ] Inspect `git status --short` and `git diff --stat`.
- [ ] Report limitations; do not commit automatically.
