# Phase 2E Video Prioritization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic local video scoring and use it for bounded metadata/transcript planning.

**Architecture:** A standalone scoring service persists versioned scores in SQLite. The planner fetches bounded candidates, refreshes stale scores, and orders work using the persisted metadata/transcript priorities.

**Tech Stack:** Python standard library, dataclasses, SQLite, unittest, argparse.

**Spec:** `docs/superpowers/specs/2026-08-21-video-prioritization-design.md`

## Global Constraints

- No network calls, LLMs, embeddings, audio, or automatic transcript fetching during scoring.
- SQLite remains the development database; migrations are additive.
- Existing enrichment/transcript budgets remain authoritative.
- Scoring version is `v1`; stale threshold is 24 hours; candidate pool defaults to 5x budget.
- Do not commit automatically.

---

### Task 1: Models, schema, and repository persistence

- [ ] Add `VideoScore`, `video_scores` table, indexes, and repository upsert/get/list helpers.
- [ ] Add failing tests for table/FK and round-trip persistence.
- [ ] Run focused tests, implement minimal code, rerun.

### Task 2: Local scoring service

- [ ] Add failing tests for buckets, signals, formulas, confidence, clamp, tiers, reason JSON, and stale refresh.
- [ ] Implement `VideoScoringService` and deterministic constants.
- [ ] Run focused tests, then full suite.

### Task 3: Planner and CLI integration

- [ ] Add failing tests for candidate pool, score ordering, and CLI commands.
- [ ] Integrate bounded scoring into planner and add `score-video`, `score-videos`, `top-videos`.
- [ ] Update README and run full tests plus smoke commands.
