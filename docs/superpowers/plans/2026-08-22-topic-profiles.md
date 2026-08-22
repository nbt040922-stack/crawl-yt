# Discovery Topic Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable Topic Profiles and explainable deterministic concept matching to Discovery.

**Architecture:** Persist normalized profiles and immutable run snapshots in additive SQLite tables. Build effective concepts locally, evaluate titles/identity with explainable evidence, and expose server-rendered CRUD and result auditing without changing network or scoring behavior.

**Tech Stack:** Python dataclasses, sqlite3, FastAPI/Jinja2, unittest.

**Spec:** `docs/superpowers/specs/2026-08-22-topic-profiles-design.md`

## Global Constraints

- Keep Strict 60%, Balanced 40%, Broad 25% and existing identity floors.
- Keep primary-keyword provenance and current search/candidate network behavior.
- No LLM, embeddings, synonym API, YouTube Data API, transcripts, comments, or workers.
- Do not modify Channel Scoring v2, crawl, cadence, scheduling, or metadata hydration.

### Task 1: TopicProfile persistence

**Files:** `src/crawl_yt/database/models.py`, `src/crawl_yt/database/repository.py`, `tests/test_topic_profiles.py`.

- [ ] Add failing CRUD/normalization/delete-safety tests.
- [ ] Run focused tests and verify missing profile APIs fail.
- [ ] Add `TopicProfile`, additive tables, and repository CRUD methods.
- [ ] Run focused tests until green.

### Task 2: Explainable concept matcher

**Files:** `src/crawl_yt/discovery/relevance.py`, `tests/test_discovery_relevance.py`.

- [ ] Add failing tests for exact phrase, normalized duplicate concepts, conservative token reorder, generic protection, title evidence, positive/negative real regressions, and unchanged gates.
- [ ] Run focused tests and verify evidence APIs are missing.
- [ ] Implement effective-concept normalization and per-title matched-concept evidence.
- [ ] Run focused tests until green.

### Task 3: Discovery profile integration and snapshot

**Files:** `src/crawl_yt/discovery/channel_discovery.py`, `src/crawl_yt/database/repository.py`, `tests/test_discovery.py`, `tests/test_topic_profiles.py`.

- [ ] Add failing profile/no-profile, provenance, snapshot immutability, and zero-network-delta tests.
- [ ] Run focused tests and verify integration is absent.
- [ ] Pass profile snapshot/effective concepts into Discovery and persist bounded run/candidate evidence.
- [ ] Run focused tests until green.

### Task 4: Profile CRUD and Discovery evidence UI

**Files:** `src/crawl_yt/web/app.py`, profile templates, `form.html`, `discovery_result.html`, `base.html`, `app.css`, `tests/test_web.py`.

- [ ] Add failing CRUD, form selection, compact layout, snapshot summary, top-concept, and expandable evidence tests.
- [ ] Run focused tests and verify routes/rendering are absent.
- [ ] Implement server-rendered profile pages and Discovery form/result adapters.
- [ ] Run all web tests until green.

### Task 5: Regression verification

- [ ] Run `& .venv\\Scripts\\python.exe -m unittest discover -s tests -q`.
- [ ] Inspect exact `git status --short` and `git diff --stat`.
- [ ] Report limitations and leave all changes uncommitted.
