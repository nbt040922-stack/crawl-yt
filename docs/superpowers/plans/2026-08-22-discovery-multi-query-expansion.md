# Discovery Multi-Query Candidate Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Discovery candidates through bounded, profile-controlled search queries while preserving verification, relevance, scoring, crawl, and primary-keyword provenance behavior.

**Architecture:** Add explicit `search_concepts` beside legacy matching `concept_phrases`, build a normalized bounded query plan, and execute sequential provider searches with global channel deduplication and per-run verification. Persist query plan/metrics in the Discovery audit snapshot and pass structured data to the existing server-rendered result pages.

**Tech Stack:** Python standard library, dataclasses, SQLite migrations, FastAPI/Jinja templates, unittest.

**Spec:** User attachment `2b342f3e-68c1-4f58-98b5-3b11567287d1/pasted-text.txt`.

## Global Constraints

- Primary query is always first; maximum 8 normalized unique queries per run.
- Search concepts are candidate-finding terms; matching concepts remain verification-only.
- Per-query raw search requests are bounded; global unique candidate budget is `clamp(target * 25, 100, 500)`.
- Verify each channel at most once per run and stop when accepted target or unique ceiling is reached.
- Secondary query failures are recorded and do not abort later queries; primary failure keeps existing failure behavior.
- Existing relevance thresholds, matcher, coverage formula, scoring, crawl, metadata, and provenance semantics remain unchanged.
- Do not add network providers, LLMs, embeddings, concurrency, or automatic synonyms.

---

### Task 1: Profile data and migration

**Files:**
- Modify: `src/crawl_yt/database/models.py`
- Modify: `src/crawl_yt/database/repository.py`
- Test: `tests/test_topic_profiles.py`
- Test: `tests/test_database.py`

- [ ] Add `search_concepts: list[str]` to `TopicProfile` while retaining `concept_phrases` as matching concepts.
- [ ] Add `search_concepts_json` to the SQLite schema and idempotent migration; legacy rows receive `[]`.
- [ ] Extend create/update/get/list profile repository methods with normalized search concepts and preserve existing matching phrases.
- [ ] Add migration and round-trip tests proving old profiles remain readable and new fields persist.
- [ ] Run focused profile/database tests and confirm they fail before implementation then pass.

### Task 2: Profile editor UI

**Files:**
- Modify: `src/crawl_yt/web/app.py`
- Modify: `src/crawl_yt/web/templates/topic_profile_form.html`
- Modify: `src/crawl_yt/web/templates/topic_profile_detail.html`
- Modify: `src/crawl_yt/web/templates/topic_profiles.html`
- Test: `tests/test_web.py`

- [ ] Add separate `search_concepts` form input and pass it through create/update routes.
- [ ] Label search concepts as candidate-finding terms and matching concepts as verification terms.
- [ ] Render both concept sets in profile list/detail views without changing server-rendered structure.
- [ ] Add web tests for create/edit round-trip and legacy profile rendering.

### Task 3: Query-plan and budget primitives

**Files:**
- Modify: `src/crawl_yt/config.py`
- Modify: `src/crawl_yt/discovery/channel_discovery.py`
- Test: `tests/test_discovery.py`

- [ ] Add centralized constants/configuration for max queries (8), per-query batch (100), minimum unique budget (100), and maximum unique budget (500).
- [ ] Add normalization/deduplication helper that emits primary query first, then up to seven profile search concepts.
- [ ] Add adaptive unique-budget helper and query metric/candidate provenance structures.
- [ ] Add red tests for primary ordering, duplicate removal, query cap, and budget clamp; implement minimally.

### Task 4: Multi-query discovery execution

**Files:**
- Modify: `src/crawl_yt/discovery/channel_discovery.py`
- Modify: `src/crawl_yt/discovery/relevance.py` only if a compatibility adapter is required
- Test: `tests/test_discovery.py`

- [ ] Execute bounded search batches sequentially, aggregate raw counts, and deduplicate channels globally by canonical id.
- [ ] Record ordered `discovered_by_queries` for every candidate and count cross-query duplicates.
- [ ] Verify each unique candidate once, preserving current relevance policy and evidence calculation.
- [ ] Stop after target accepted or unique budget; do not request or inspect beyond the ceiling.
- [ ] Keep primary keyword as the only persisted discovery relationship keyword.
- [ ] Add tests for A–H cross-query dedup/provenance, no duplicate verification, early stop, diversity contribution, and unique ceiling.

### Task 5: Failure handling and audit snapshot

**Files:**
- Modify: `src/crawl_yt/database/repository.py`
- Modify: `src/crawl_yt/discovery/channel_discovery.py`
- Modify: `src/crawl_yt/database/models.py` if audit structures are typed
- Test: `tests/test_discovery.py`
- Test: `tests/test_discovery_normalization.py`

- [ ] Add idempotent audit columns for planned queries, executed queries, and per-query metrics.
- [ ] Persist matching concepts/profile identity plus the query plan snapshot at run time.
- [ ] Record secondary failures and continue to later queries; test primary provenance and snapshot immutability.
- [ ] Ensure legacy audit rows remain readable with safe defaults.

### Task 6: Discovery result UI

**Files:**
- Modify: `src/crawl_yt/web/templates/discovery_result.html`
- Modify: `src/crawl_yt/web/app.py` only if route context needs new report fields
- Test: `tests/test_web.py`

- [ ] Add planned/executed query counts, raw results, unique candidates, cross-query duplicates, accepted, and rejected metrics.
- [ ] Add a collapsible query breakdown with raw, unique, new, duplicate, and failure values.
- [ ] Add `Found by` query provenance to accepted/rejected tables while retaining coverage, evidence, identity, and source links.
- [ ] Add tests proving structured rendering and no raw object dump.

### Task 7: Regression and verification

**Files:**
- Modify: only tests needed for intentional expectations

- [ ] Run focused Discovery/profile/web tests after each task.
- [ ] Run `git diff --check`.
- [ ] Run the complete suite with `python -m unittest discover -s tests -q`.
- [ ] Verify relevance thresholds, matcher behavior, candidate multiplier replacement, scoring, crawl, and metadata tests remain green.
- [ ] Report exact `git status --short` and `git diff --stat`; do not commit.
