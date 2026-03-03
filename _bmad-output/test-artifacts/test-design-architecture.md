---
stepsCompleted: ['step-01-detect-mode', 'step-02-load-context', 'step-03-risk-and-testability', 'step-04-coverage-plan', 'step-05-generate-output']
lastStep: 'step-05-generate-output'
lastSaved: '2026-03-03'
workflowType: 'testarch-test-design'
inputDocuments:
  - _bmad-output/implementation-artifacts/tech-spec-iphone-anki-sync-api.md
  - docs/api-design.md
  - docs/migration-ankiconnect-to-anki-package.md
  - tests/test_main.py
  - anki_writer.py
  - main.py
---

# Test Design for Architecture: iphone-anki-sync — Post-Migration Dual-Service Architecture

**Purpose:** Architectural concerns, testability gaps, and risk requirements for review by the development team. Serves as a contract on what must be addressed before test development begins.

**Date:** 2026-03-03
**Author:** Will
**Status:** Architecture Review Pending
**Project:** iphone-anki-sync
**Tech Spec Reference:** `_bmad-output/implementation-artifacts/tech-spec-iphone-anki-sync-api.md`
**Migration Reference:** `docs/migration-ankiconnect-to-anki-package.md`

---

## Executive Summary

**Scope:** System-level test design for the post-migration dual-service architecture. The project was recently migrated from AnkiConnect-based architecture to a direct `anki` Python package integration, producing two independent microservices: `ldoce5-api` and `anki-writer`.

**Architecture (from tech-spec + migration doc):**

- **Service 1 — ldoce5-api** (`main.py`, port 5050): Dictionary lookup via LDOCE5 MDict files; AI-assisted sense disambiguation; audio serving.
- **Service 2 — anki-writer** (`anki_writer.py`, port 5051): Accepts word data, writes Anki notes directly via `anki.collection.Collection`, syncs to AnkiMobile.
- **Service 3 — anki-sync-server** (port 8080): External Anki sync server (not under test here).
- **Deployment:** Single homelab server (Raspberry Pi), all services managed via systemd.

**Key Migration Change:** `POST /add-word` was removed from `main.py` (now `ldoce5-api`) and moved to the new `anki-writer` service. `anki-writer` calls `ldoce5-api` for dictionary lookup, then writes to Anki using the Python package directly — no AnkiConnect dependency.

**Risk Summary:**

- **Total risks**: 8
- **Critical (score 9)**: 1 — requires immediate action before any other testing
- **High-priority (score ≥6)**: 1 — requires mitigation before test development
- **Medium (score 3–5)**: 4
- **Low (score 1–2)**: 2
- **Test effort**: ~40–60 hours (~1.5–2 weeks for 1 developer)

---

## Quick Guide

### 🚨 BLOCKERS — Must Resolve Before Testing Can Begin

**These MUST be addressed before any meaningful test development is possible:**

1. **ASR-002: tests/test_main.py is completely stale** — All 25+ existing tests reference pre-migration `app.state.http_client` (AnkiConnect) and the old `/add-word` endpoint in `main.py`. Running them produces false passes or errors. These tests **must be deleted/rewritten before any CI trust is possible.** (Owner: Developer)

2. **ASR-003: anki_writer.py has zero test coverage** — The new service handling Anki writes has no tests at all. All Anki write paths are untested. (Owner: Developer)

3. **ASR-001: `anki.collection.Collection` testability strategy undefined** — `Collection` is imported inside `_add_note_and_sync()` via `from anki.collection import Collection`. This requires a module-level mock strategy (`unittest.mock.patch('anki_writer.Collection')` or `sys.modules` injection). Without this strategy documented and agreed, `anki-writer` tests cannot be written. (Owner: Developer)

**What we need:** Complete these 3 items before test development begins. The existing test suite is actively misleading.

---

### ⚠️ HIGH PRIORITY — Validate Before Proceeding

1. **R-002: anki_writer.py zero test coverage** — All 16 API test scenarios for `anki-writer` are unwritten. The core write-and-sync path, duplicate detection, and error handling have never been exercised in automated tests. (Owner: Developer)

2. **R-003: anki package not importable in dev/CI environment** — The `anki` Python package requires native binary extensions (SQLite, etc.) and is only available on the production server. Dev/CI environments will fail to import it. This blocks writing tests for `anki-writer` unless the mock strategy (ASR-001) is in place. (Owner: Developer)

**What we need:** Mock strategy documented and agreed before writing `anki-writer` tests.

---

### 📋 INFO ONLY — Solutions Provided

1. **Test strategy**: API integration tests (pytest + httpx AsyncClient) + unit tests; no E2E or Playwright required for this backend-only project.
2. **Tooling**: pytest, pytest-asyncio, httpx, unittest.mock — all already in dev environment.
3. **Execution**: All tests via pytest in every PR (no k6, no chaos — homelab personal tool, single user).
4. **Coverage**: ~44 test scenarios identified, prioritized P0–P3 across both services.
5. **Quality gates**: All P0 tests passing; all P1 tests passing or triaged; no open critical bugs.

---

## For Architecture/Dev Team — Open Topics 👷

### Risk Assessment

**Total risks identified**: 8 (1 critical score=9, 1 high score=6, 4 medium, 2 low)

#### Critical Risks (Score = 9) — IMMEDIATE ACTION REQUIRED

| Risk ID    | Category  | Description                                                                | Probability | Impact | Score       | Mitigation                                              | Owner     | Timeline           |
| ---------- | --------- | -------------------------------------------------------------------------- | ----------- | ------ | ----------- | ------------------------------------------------------- | --------- | ------------------ |
| **R-001**  | **TECH**  | `tests/test_main.py` is completely stale — all 25+ tests mock AnkiConnect state and pre-migration `/add-word` endpoint that no longer exists in `main.py` | 3           | 3      | **9**       | Delete stale tests; rewrite for current architecture    | Developer | Before test sprint |

#### High-Priority Risks (Score ≥6)

| Risk ID    | Category  | Description                                                                | Probability | Impact | Score       | Mitigation                                              | Owner     | Timeline           |
| ---------- | --------- | -------------------------------------------------------------------------- | ----------- | ------ | ----------- | ------------------------------------------------------- | --------- | ------------------ |
| **R-002**  | **TECH**  | `anki_writer.py` has zero test coverage — new core service for Anki writes is completely untested | 3           | 2      | **6**       | Write full test suite for `anki-writer` (16 scenarios)  | Developer | Test sprint        |

#### Medium-Priority Risks (Score 3–5)

| Risk ID | Category | Description                                                                                      | Probability | Impact | Score | Mitigation                                                  | Owner     |
| ------- | -------- | ------------------------------------------------------------------------------------------------ | ----------- | ------ | ----- | ----------------------------------------------------------- | --------- |
| R-003   | TECH     | `anki` Python package not importable in dev/CI — binary extensions require production environment | 3           | 2      | 4     | Use `unittest.mock.patch('anki_writer.Collection')` strategy | Developer |
| R-004   | TECH     | `_col_lock` threading lock serializes all writes — concurrent requests queue up; no timeout      | 2           | 2      | 4     | Add lock acquisition timeout; test concurrent request behavior | Developer |
| R-005   | OPS      | `col.sync_collection()` makes network call to `anki-sync-server` — hard failure if server down   | 2           | 2      | 4     | Mock `col.sync_login` + `col.sync_collection` in all tests  | Developer |
| R-006   | OPS      | LLM client not reachable causes fallback behavior — not tested                                    | 2           | 2      | 3     | Test LLM-disabled path for `ldoce5-api`                     | Developer |

#### Low-Priority Risks (Score 1–2)

| Risk ID | Category | Description                                                                   | Probability | Impact | Score | Action  |
| ------- | -------- | ----------------------------------------------------------------------------- | ----------- | ------ | ----- | ------- |
| R-007   | DATA     | Audio fetch failure silently swallowed — note created without audio, no warning logged to user | 1           | 2      | 2     | Monitor; add test to verify warning field populated        |
| R-008   | DATA     | Duplicate detection only checks `word` field — a word with different `sense_index` would be rejected as duplicate | 1           | 2      | 2     | Monitor; acceptable for current personal-tool scope        |

#### Risk Category Legend

- **TECH**: Technical/Architecture (flaws, integration, testability)
- **SEC**: Security (access controls, auth, data exposure)
- **PERF**: Performance (SLA violations, degradation)
- **DATA**: Data Integrity (loss, corruption, inconsistency)
- **BUS**: Business Impact (UX harm, logic errors)
- **OPS**: Operations (deployment, config, monitoring)

---

### Testability Concerns and Architectural Gaps

**🚨 ACTIONABLE CONCERNS — Developer Must Address**

#### 1. Blockers to Automated Testing

| Concern                          | Impact on Testing                                          | What Developer Must Provide                                                          | Owner     | Timeline           |
| -------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------ | --------- | ------------------ |
| **Stale test suite (ASR-002)**   | All existing tests are invalid — CI is providing false confidence or irrelevant failures | Delete `tests/test_main.py` and rewrite for current dual-service architecture         | Developer | Immediately        |
| **No anki-writer tests (ASR-003)** | Core write-and-sync path has never been exercised in automated tests | Create `tests/test_anki_writer.py` with full coverage                                | Developer | Test sprint        |
| **`Collection` mock strategy undefined (ASR-001)** | Cannot write any `anki-writer` unit or integration tests without agreed mock pattern | Document and implement `unittest.mock.patch('anki_writer.Collection')` pattern       | Developer | Before test sprint |

#### 2. Architectural Improvements Needed

1. **`from anki.collection import Collection` inside function body**
   - **Current pattern**: `Collection` is imported inside `_add_note_and_sync()` at call time, not at module top-level.
   - **Testability impact**: Standard `@patch('anki_writer.anki.collection.Collection')` won't work — must use `@patch('anki_writer.Collection')` with a `sys.modules` shim or inject via the function's local `from anki.collection import Collection` path.
   - **Recommended approach**:
     ```python
     # Recommended: move import to module top-level with try/except
     try:
         from anki.collection import Collection as AnkiCollection
     except ImportError:
         AnkiCollection = None  # dev/CI environment
     ```
     Then mock with `@patch('anki_writer.AnkiCollection')`.
   - **Owner**: Developer
   - **Timeline**: Before test sprint

2. **No structured error types from `_add_note_and_sync`**
   - **Current problem**: Only `ValueError` (duplicate) and generic `Exception` are raised; callers cannot distinguish sync failure from write failure.
   - **Impact if not fixed**: Tests must rely on string matching in error messages, which is fragile.
   - **Acceptable for now**: Personal tool with limited error surface; monitor.

---

### Testability Assessment Summary

#### What Works Well

- ✅ `ldoce5-api` follows FastAPI async pattern consistently — easy to test with `AsyncClient(transport=ASGITransport(app=app))`
- ✅ `anki-writer` uses `run_in_executor` for the blocking Anki call — can be tested by mocking the executor function
- ✅ Both services use `app.state` for dependency injection — mockable at fixture level
- ✅ `_col_lock` is module-level — can be bypassed in tests by patching `Collection` before lock acquisition
- ✅ Both services have `/health` endpoints — health check tests are straightforward
- ✅ Configuration via environment variables — fully overrideable in test fixtures

#### Accepted Trade-offs (No Action Required)

For `iphone-anki-sync` v1 (personal tool), the following trade-offs are acceptable:

- **No authentication on either service** — Personal homelab tool, not exposed to internet; auth out of scope.
- **Single-threaded collection access via `_col_lock`** — Personal tool with ≤1 concurrent user; lock contention is non-issue.
- **No structured logging or distributed tracing** — Acceptable for personal use; `logging.INFO` is sufficient.

---

### Risk Mitigation Plans (Critical and High-Priority Risks)

#### R-001: Stale Test Suite (Score: 9) — CRITICAL BLOCKER

**Description:** All 25+ tests in `tests/test_main.py` reference the pre-migration architecture:
- Mock `app.state.http_client` (AnkiConnect HTTP client — removed from `main.py`)
- Mock `app.state.llm_client` (present in new `main.py` but unused in most tests correctly)
- Test `POST /add-word` endpoint on `main.py` — this endpoint no longer exists
- Assert `anki_connected: true` in health response — field removed in new architecture

Running these tests either: (a) fails with `AttributeError` on missing state, or (b) passes erroneously if the mock setup succeeds but tests the wrong thing.

**Mitigation Strategy:** QA-owned work — see `test-design-qa.md` for full plan (FIX-001 through FIX-003, P0 test scenarios).

**Developer prerequisite**: Implement `Collection` import refactor and resolve ASR-001 before QA can execute the mitigation.

**Owner:** Developer (code prerequisite) + Developer (test implementation)
**Timeline:** Before test development sprint
**Status:** Planned
**Verification:** `tests/test_main.py` removed; no `http_client` or `anki_connected` references remain in test code.

---

#### R-002: anki_writer.py Zero Test Coverage (Score: 6) — HIGH PRIORITY

**Description:** `anki_writer.py` has been in production since the migration with zero automated test coverage. The critical path — `POST /add-word` → ldoce5-api lookup → Collection write → sync — has never been exercised in automated tests.

**Mitigation Strategy:** QA-owned work — see `test-design-qa.md` for full test plan (AW-P0-*, AW-P1-* scenarios covering 16 test cases).

**Developer prerequisite**: Resolve ASR-001 (Collection mock strategy) before test implementation can begin.

**Owner:** Developer
**Timeline:** Test development sprint
**Status:** Planned
**Verification:** `anki_writer.py` coverage ≥80%; all P0 + P1 scenarios passing.

---

### Assumptions and Dependencies

#### Assumptions

1. `ldoce5-api` (`main.py`) is the correct current source of truth — the service at port 5050 with `GET /lookup`, `GET /audio/{filename}`, `GET /health`.
2. `anki_writer.py` (port 5051) calls `ldoce5-api` via `app.state.ldoce5_client` — confirmed from source.
3. `anki-sync-server` at port 8080 is external and not under test scope.
4. The `anki` Python package is available on the production server but NOT in dev/CI environments.
5. Single-developer project — no concurrent test runs, no multi-environment CI matrix needed.

#### Dependencies

1. **`Collection` import refactor or `sys.modules` mock strategy** — Architectural decision; determines how `anki_writer.py` tests are structured. Required before test development begins.
2. **`anki-sync-server` availability** — Any call to `col.sync_collection()` requires the sync server to be running; must be mocked in all automated tests.

#### Risks to the Test Plan

- **Risk**: `anki` package has additional native binary dependencies (libz, etc.) that may not be mockable.
  - **Impact**: If patching `Collection` doesn't work cleanly, `anki_writer.py` integration tests may require a Docker environment.
  - **Contingency**: Use `sys.modules['anki'] = MagicMock()` at test module level before any imports.

---

**End of Architecture Document**

**Next Steps for Developer:**

1. Review Quick Guide (🚨 Blockers) — address ASR-001, ASR-002, ASR-003 before test sprint.
2. Implement `Collection` import refactor or `sys.modules` mock strategy.
3. Delete/archive stale `tests/test_main.py`.
4. Refer to companion QA doc (`test-design-qa.md`) for the full test scenario list.

**Next Steps for QA/Developer (Test Development):**

1. Create `tests/test_ldoce5_api.py` (21 scenarios).
2. Create `tests/test_anki_writer.py` (16 scenarios).
3. Confirm P0 tests pass before merging any new features.
