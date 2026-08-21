# Phase 3A.1 Dashboard Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden the local FastAPI dashboard with transcript details, public count APIs, safe action validation, and network-free provider tests.

**Architecture:** Keep FastAPI + Jinja2 and the existing repository/service boundaries. Add public SQL COUNT/query methods to the repository; the web layer consumes only those APIs and injected providers/services.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, SQLite, unittest, mocked in-memory providers.

**Spec:** User-provided Phase 3A.1 Dashboard Hardening request.

## Global Constraints

- Do not start Phase 3B.
- Do not add React, Vue, Node.js, Redis, Celery, WebSockets, authentication, PostgreSQL, scheduler, LLM, or embeddings.
- Tests must not perform real YouTube calls.
- Keep local bind at `127.0.0.1`.

### Task 1: Repository public count and transcript APIs

**Files:** `src/crawl_yt/database/repository.py`, `tests/test_web.py`

- Add public COUNT methods for due channels, failing channels, transcript totals, transcript video totals, and video score tiers.
- Add transcript lookup by stable row identifier or equivalent route-safe identifier.
- Add failing tests proving count methods are used and transcript ownership is enforced.

### Task 2: Transcript detail and action safety

**Files:** `src/crawl_yt/web/app.py`, `src/crawl_yt/web/templates/detail.html`, `tests/test_web.py`

- Add transcript detail route with 404 checks and human-readable timestamps.
- Validate channel, video, and work-plan targets before service invocation.
- Preserve HTTP 400/404 and render unexpected provider errors as friendly 502 HTML.
- Remove all direct `_connect()` use from web code.

### Task 3: Provider-backed web tests and validation

**Files:** `tests/test_web.py`

- Add network-free mocked provider tests for discovery, crawl, score, metadata, transcript, full-crawl confirmation, and plan execution.
- Verify transcript action stays caption-only and GET routes do not mutate storage.

### Task 4: Verification

- Run `python -m unittest discover -s tests -q`.
- Start `python main.py web` bound to `127.0.0.1` and check dashboard routes plus one transcript detail page.
- Report exact status and diff; do not commit automatically.
