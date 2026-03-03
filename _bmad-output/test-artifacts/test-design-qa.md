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

# Test Design for QA: iphone-anki-sync — Post-Migration Dual-Service Architecture

**Purpose:** Test execution recipe for the developer. Defines what to test, how to test it, and what infrastructure is needed.

**Date:** 2026-03-03
**Author:** Will
**Status:** Draft
**Project:** iphone-anki-sync

**Related:** See Architecture doc (`test-design-architecture.md`) for testability concerns and architectural blockers.

---

## Executive Summary

**Scope:** Full test coverage for two services post-migration:
- `ldoce5-api` (`main.py`, port 5050) — dictionary lookup, audio serving, health
- `anki-writer` (`anki_writer.py`, port 5051) — Anki card creation and sync

Also includes **FIX work**: replace the completely stale `tests/test_main.py` before writing any new tests.

**Risk Summary:**

- Total Risks: 8 (1 critical score=9, 1 high score=6, 4 medium, 2 low)
- Critical Categories: TECH (stale tests, untested service, import mock strategy)

**Coverage Summary:**

- P0 tests: ~7 (critical paths, blockers resolved)
- P1 tests: ~17 (important features, all error paths)
- P2 tests: ~17 (edge cases, unit tests, secondary paths)
- P3 tests: ~3 (exploratory, concurrency)
- **Total**: ~44 tests (~1.5–2 weeks development effort for 1 developer)

---

## Not in Scope

| Item                              | Reasoning                                                                   | Mitigation                                                              |
| --------------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **anki-sync-server** (port 8080)  | External service not owned by this project                                  | Mocked via `col.sync_login` + `col.sync_collection` stubs in all tests  |
| **AnkiMobile iOS app**            | Mobile app not testable in CI; sync protocol tested at service boundary     | Verified manually after deployment                                      |
| **Performance / load testing**    | Personal single-user tool; ≤1 concurrent request by design                  | Acceptable trade-off; monitored via logs in production                  |
| **Authentication / authorization**| Homelab tool; not internet-exposed; auth explicitly out of scope in tech-spec | No mitigation needed; re-evaluate if exposed publicly                  |
| **LDOCE5 MDict file content**     | Dictionary content is external data; not owned by this project              | Tests use fixture data for known words; real file tested manually       |

---

## Dependencies & Test Blockers

**CRITICAL:** These must be resolved before test development begins.

### Architectural Blockers (Pre-Test-Sprint)

**Source:** See Architecture doc "Quick Guide" for detailed mitigation plans.

1. **ASR-002: Delete stale `tests/test_main.py`** — Developer — Before test sprint
   - File references `app.state.http_client` (AnkiConnect, removed) and `POST /add-word` on `main.py` (moved to `anki-writer`).
   - Blocks: Cannot run `pytest tests/` with confidence while stale tests exist.

2. **ASR-001: `Collection` import mock strategy** — Developer — Before test sprint
   - `from anki.collection import Collection` is inside `_add_note_and_sync()` function body.
   - Must confirm mock target: either refactor to module-level import, or use `sys.modules['anki'] = MagicMock()` before any test imports.
   - Blocks: All `anki-writer` tests require this strategy.

3. **ASR-003: `anki` package not available in dev/CI** — Developer — Before test sprint
   - Binary extension not installable outside production server.
   - Resolved by the mock strategy above — tests must never actually import `anki`.

### QA Infrastructure Setup

1. **Test fixtures for both services**
   - `ldoce5-api` fixture: `AsyncClient(transport=ASGITransport(app=main.app))` with mocked `app.state.mdx_builder`, `app.state.mdd_wrapper`, `app.state.llm_client`.
   - `anki-writer` fixture: `AsyncClient(transport=ASGITransport(app=anki_writer.app))` with mocked `app.state.ldoce5_client` and patched `Collection`.

2. **Environment**
   - Local: `pytest tests/` with `.env` file pointing to test paths.
   - CI (if added): Same `pytest tests/`; no server needed (all in-process mocking).
   - Production: Deploy and verify manually via `/health` endpoints.

**Example pytest fixture pattern for `ldoce5-api`:**

```python
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
import main

@pytest_asyncio.fixture
async def ldoce5_client():
    mock_mdx = MagicMock()
    mock_mdd = MagicMock()
    mock_llm = AsyncMock()
    main.app.state.mdx_builder = mock_mdx
    main.app.state.mdd_wrapper = mock_mdd
    main.app.state.llm_client = mock_llm
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        yield ac, mock_mdx, mock_mdd, mock_llm
```

**Example pytest fixture pattern for `anki-writer`:**

```python
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport
import anki_writer

@pytest_asyncio.fixture
async def anki_writer_client():
    mock_ldoce5 = AsyncMock()
    anki_writer.app.state.ldoce5_client = mock_ldoce5

    # Block anki package import at module level
    with patch('anki_writer.Collection') as mock_col_cls:
        mock_col = MagicMock()
        mock_col_cls.return_value = mock_col
        mock_col.find_notes.return_value = []     # no duplicates by default
        mock_col.decks.id.return_value = 1
        mock_col.models.by_name.return_value = {"id": 1, "flds": []}
        mock_col.new_note.return_value = MagicMock(id=12345)
        mock_col.media.dir.return_value = "/tmp/media"
        async with AsyncClient(transport=ASGITransport(app=anki_writer.app), base_url="http://test") as ac:
            yield ac, mock_ldoce5, mock_col_cls, mock_col
```

---

## Risk Assessment

### High-Priority Risks (Score ≥6)

| Risk ID   | Category | Description                                              | Score   | QA Test Coverage                                     |
| --------- | -------- | -------------------------------------------------------- | ------- | ---------------------------------------------------- |
| **R-001** | TECH     | `tests/test_main.py` completely stale                    | **9**   | FIX-001 through FIX-003: delete and rewrite          |
| **R-002** | TECH     | `anki_writer.py` has zero test coverage                  | **6**   | AW-API-001 through AW-API-016: full test suite       |

### Medium/Low-Priority Risks

| Risk ID | Category | Description                                              | Score | QA Test Coverage                                       |
| ------- | -------- | -------------------------------------------------------- | ----- | ------------------------------------------------------ |
| R-003   | TECH     | `anki` package not importable in dev/CI                  | 4     | Mock via fixture; tests never import real anki package |
| R-004   | TECH     | `_col_lock` threading lock with no timeout               | 4     | AW-CONC-001: concurrent request test (P3)              |
| R-005   | OPS      | Network call to anki-sync-server in all writes           | 4     | Mocked in all fixtures; AW-API-009, AW-API-010         |
| R-006   | OPS      | LLM client not reachable causes fallback                 | 3     | LDO-API-004: LLM disabled path                         |
| R-007   | DATA     | Audio fetch failure silently swallowed                   | 2     | AW-API-013: verify warning field populated             |
| R-008   | DATA     | Duplicate detection by word field only                   | 2     | AW-API-008: duplicate word rejected                    |

---

## Entry Criteria

**Test development cannot begin until ALL of the following are met:**

- [ ] `tests/test_main.py` deleted or archived (ASR-002)
- [ ] `Collection` mock strategy confirmed and documented (ASR-001)
- [ ] Both `main.py` and `anki_writer.py` source code reviewed (done in this design session)
- [ ] pytest + pytest-asyncio + httpx confirmed installed in dev environment
- [ ] `.env` file with test-safe config values present

## Exit Criteria

**Test development is complete when ALL of the following are met:**

- [ ] All P0 tests passing (7 tests)
- [ ] All P1 tests passing or failures triaged (17 tests)
- [ ] P2 tests passing (17 tests, best effort)
- [ ] No `http_client` or `anki_connected` references remain in test code
- [ ] `pytest tests/` exits with code 0
- [ ] `pytest --cov=main --cov=anki_writer tests/` shows ≥80% coverage for both modules

---

## Test Coverage Plan

**Note:** P0/P1/P2/P3 = **priority and risk level** (what to implement first if time-constrained), NOT execution order. All tests run on every `pytest` invocation.

---

### 🔧 FIX — Stale Test Remediation (Do First)

**Must be completed before writing any new tests.**

| Test ID    | Action Required                                                                               | Risk Link | Notes                                        |
| ---------- | --------------------------------------------------------------------------------------------- | --------- | -------------------------------------------- |
| **FIX-001** | Delete or archive `tests/test_main.py` — remove all AnkiConnect mock references              | R-001     | Archive as `tests/archive/` for reference    |
| **FIX-002** | Create `tests/test_ldoce5_api.py` with correct `app.state` fixture for new `main.py`         | R-001     | Mock: `mdx_builder`, `mdd_wrapper`, `llm_client` |
| **FIX-003** | Create `tests/test_anki_writer.py` with `Collection` mock + `ldoce5_client` mock fixture      | R-001     | See fixture pattern in Dependencies section  |

---

### P0 (Critical)

**Criteria:** Blocks core functionality + Risk ≥6 + No workaround

| Test ID      | Service        | Test Description                                              | Test Level | Risk Link | Notes                                           |
| ------------ | -------------- | ------------------------------------------------------------- | ---------- | --------- | ----------------------------------------------- |
| **LDO-P0-01** | ldoce5-api    | `GET /lookup?word=happy` — success, returns senses + audio + pronunciation | API | — | Verify response shape: `word`, `senses`, `audio`, `pronunciation` |
| **LDO-P0-02** | ldoce5-api    | `GET /lookup?word=unknown` — word not found returns 404       | API        | —         | `detail` contains "not found"                   |
| **LDO-P0-03** | ldoce5-api    | `GET /health` — all components loaded returns `{"status": "ok", "mdx_loaded": true, "mdd_loaded": true}` | API | — | Verify all fields present |
| **AW-P0-01**  | anki-writer   | `POST /add-word {"word": "happy", "sentence": "..."}` — success, returns `note_id` | API | R-002 | Mock ldoce5-api response; mock Collection write |
| **AW-P0-02**  | anki-writer   | `POST /add-word {"word": "happy"}` — duplicate word returns 422 with `"duplicate:"` | API | R-002 | Mock `col.find_notes` returns `[12345]`         |
| **AW-P0-03**  | anki-writer   | `POST /add-word {}` — empty word returns 422                  | API        | R-002     | `detail` contains "invalid: word cannot be empty" |
| **AW-P0-04**  | anki-writer   | `GET /health` — collection accessible returns `{"status": "ok", "collection_accessible": true}` | API | — | Verify `collection_accessible` field            |

**Total P0:** 7 tests

---

### P1 (High)

**Criteria:** Important features + Medium risk + Common workflows

| Test ID      | Service        | Test Description                                              | Test Level | Risk Link | Notes                                           |
| ------------ | -------------- | ------------------------------------------------------------- | ---------- | --------- | ----------------------------------------------- |
| **LDO-P1-01** | ldoce5-api    | `GET /lookup?word=run` with sentence — selected_sense_index used correctly | API | — | Verify `selected_sense_index` in response       |
| **LDO-P1-02** | ldoce5-api    | `GET /lookup?word=happy` — audio UK preferred over US         | API        | —         | Verify `audio.uk.url` present; `audio.us` as fallback |
| **LDO-P1-03** | ldoce5-api    | `GET /lookup?word=` — empty word returns 422                  | API        | —         | `detail` contains "invalid"                     |
| **LDO-P1-04** | ldoce5-api    | `GET /lookup?word=<101chars>` — word too long returns 422     | API        | —         | Word > 100 chars                                |
| **LDO-P1-05** | ldoce5-api    | `GET /audio/GB_happy.spx` — file served with correct content-type | API    | —         | `Content-Type: audio/x-speex` or `application/octet-stream` |
| **LDO-P1-06** | ldoce5-api    | `GET /audio/nonexistent.spx` — returns 404                    | API        | —         | File not in MDD                                 |
| **LDO-P1-07** | ldoce5-api    | `GET /lookup?word=happy` — LLM disabled (`llm_client=None`) returns response without AI disambiguation | API | R-006 | Verify graceful fallback |
| **AW-P1-01**  | anki-writer   | `POST /add-word {"word": "happy"}` — success without sentence | API        | R-002     | Verify `sentence` field uses LDOCE example      |
| **AW-P1-02**  | anki-writer   | `POST /add-word {"word": "x" * 101}` — word too long returns 422 | API     | R-002     | Max 100 chars                                   |
| **AW-P1-03**  | anki-writer   | `POST /add-word {"word": "happy"}` — ldoce5-api unreachable returns 502 | API | R-002, R-005 | Mock `ldoce5_client` raises `httpx.ConnectError` |
| **AW-P1-04**  | anki-writer   | `POST /add-word {"word": "unknown"}` — ldoce5-api returns 404 → 422 | API | R-002 | Mock response status 404                        |
| **AW-P1-05**  | anki-writer   | `POST /add-word {"word": "happy"}` — ldoce5-api returns 5xx → 502 | API | R-002 | Mock response status 500                        |
| **AW-P1-06**  | anki-writer   | `POST /add-word {"word": "happy"}` — Collection write fails (RuntimeError) → 502 | API | R-002 | Mock `col.add_note` raises RuntimeError         |
| **AW-P1-07**  | anki-writer   | `POST /add-word {"word": "happy"}` — anki-sync-server down (sync fails) → 502 | API | R-002, R-005 | Mock `col.sync_collection` raises Exception     |
| **AW-P1-08**  | anki-writer   | `POST /add-word {"word": "happy", "sense_index": 2}` — uses specified sense index | API | R-002 | Verify `sense_used: 2` in response              |
| **AW-P1-09**  | anki-writer   | `POST /add-word {"word": "happy"}` — note type not found → 502 | API | R-002 | Mock `col.models.by_name` returns `None`        |
| **AW-P1-10**  | anki-writer   | `GET /health` — collection file not found returns `collection_accessible: false` | API | — | Set `COLLECTION_PATH` to nonexistent path      |

**Total P1:** 17 tests

---

### P2 (Medium)

**Criteria:** Secondary features + Low risk + Edge cases + Unit tests

| Test ID       | Service        | Test Description                                              | Test Level | Risk Link | Notes                                            |
| ------------- | -------------- | ------------------------------------------------------------- | ---------- | --------- | ------------------------------------------------ |
| **LDO-P2-01** | ldoce5-api    | `GET /lookup?word=run` — multiple senses returned, all present in response | API | — | Verify `senses` is a list with ≥2 items         |
| **LDO-P2-02** | ldoce5-api    | `GET /lookup?word=happy` — no audio available → `audio: {}` in response | API | R-007 | MDD has no audio for word                       |
| **LDO-P2-03** | ldoce5-api    | `GET /health` — MDX not loaded returns `mdx_loaded: false`   | API        | —         | `mdx_builder` is None                           |
| **LDO-P2-04** | ldoce5-api    | `GET /lookup?word=happy` — LLM disambiguation picks correct sense | API   | —         | Verify LLM response used for `selected_sense_index` |
| **LDO-P2-05** | ldoce5-api    | `GET /lookup?word=happy` — response includes `pronunciation` field | API  | —         | Verify format (e.g., `/ˈhæp.i/`)               |
| **UNIT-LDO-01** | ldoce5-api  | `_extract_audio_filenames()` — both UK and US present → returns `{"uk": {...}, "us": {...}}` | Unit | — | Pure function test |
| **UNIT-LDO-02** | ldoce5-api  | `_extract_audio_filenames()` — UK only → `{"uk": {...}, "us": None}` | Unit | — | |
| **UNIT-LDO-03** | ldoce5-api  | `_extract_audio_filenames()` — neither → `{}` or `{"uk": None, "us": None}` | Unit | — | |
| **UNIT-LDO-04** | ldoce5-api  | `_extract_senses()` — multiple senses extracted correctly     | Unit       | —         | Verify sense structure: `definition_html`, `example`, etc. |
| **UNIT-LDO-05** | ldoce5-api  | Sense index clamped: `sense_index > len(senses)-1` → uses last sense | Unit | — | From `anki_writer.py` line ~187: `max(0, min(sense_idx, len(senses) - 1))` |
| **AW-P2-01**  | anki-writer   | `POST /add-word {"word": "happy", "sentence": "..."}` — audio UK fetched successfully | API | R-007 | Verify `audio_filename` ends with `.spx`        |
| **AW-P2-02**  | anki-writer   | `POST /add-word {"word": "happy"}` — audio fetch fails → `warning` field populated | API | R-007 | Mock `ldoce5_client.get(audio_url)` raises Exception |
| **AW-P2-03**  | anki-writer   | `POST /add-word {"word": "happy", "sentence": "He is happy."}` — sentence + LDOCE example combined | API | — | Verify `sentence_field` = `"He is happy.\n\n— LDOCE: ..."` |
| **AW-P2-04**  | anki-writer   | `POST /add-word {"word": "HAPPY"}` — word normalized to lowercase | API | — | Verify `word: "happy"` in response             |
| **AW-P2-05**  | anki-writer   | `POST /add-word {"word": "happy", "sense_index": 0}` — sense_index 0 explicit | API | — | Verify `sense_used: 0`                          |
| **AW-P2-06**  | anki-writer   | `POST /add-word` with `sense_index` beyond list length → clamped to last sense | API | R-008 | Mock senses list has 2 items; pass `sense_index=10` |
| **AW-P2-07**  | anki-writer   | `GET /health` — ldoce5-api reachability check works            | API        | —         | Mock `ldoce5_client.get('/health')` returns 200 |

**Total P2:** 17 tests

---

### P3 (Low)

**Criteria:** Nice-to-have + Exploratory + Concurrency

| Test ID       | Service       | Test Description                                                   | Test Level | Notes                                              |
| ------------- | ------------- | ------------------------------------------------------------------ | ---------- | -------------------------------------------------- |
| **AW-P3-01**  | anki-writer  | Concurrent `POST /add-word` requests — `_col_lock` serializes correctly | Integration | Two async tasks both calling add-word; verify second queues behind first |
| **AW-P3-02**  | anki-writer  | `POST /add-word` with `sentence` > 2000 chars — Pydantic validation rejects | API | Field has `max_length=2000`                        |
| **EXPL-01**   | Both         | Exploratory: send malformed JSON body — verify 422 not 500        | API        | Basic robustness sanity check                       |

**Total P3:** 3 tests

---

**Grand Total: ~44 tests** (7 P0 + 17 P1 + 17 P2 + 3 P3)

---

## Execution Strategy

**Philosophy:** All tests run via `pytest` on every change. Backend Python project — no Playwright/k6 required. Fast in-process mocking; entire suite should complete in <60 seconds.

### Every Commit / PR: `pytest tests/`

**All functional tests** across all priority levels:

- Run: `pytest tests/ -v`
- With coverage: `pytest tests/ --cov=main --cov=anki_writer --cov-report=term-missing`
- Parallelized (if desired): `pytest tests/ -n auto` (requires `pytest-xdist`)
- Total: ~44 tests
- Expected runtime: <60 seconds (all in-process, no network, no file I/O)

**Run specific priorities:**

```bash
# Run only P0 tests
pytest tests/ -k "P0"

# Run only anki-writer tests
pytest tests/test_anki_writer.py

# Run with coverage
pytest tests/ --cov=main --cov=anki_writer --cov-report=html
```

### Manual (Post-Deploy) Verification

**Smoke checks on production server after any deployment:**

1. `curl http://localhost:5050/health` — verify `{"status": "ok", "mdx_loaded": true}`
2. `curl http://localhost:5051/health` — verify `{"status": "ok", "collection_accessible": true}`
3. Single word lookup via Shortcuts app — verify full end-to-end flow

**No k6, no chaos, no multi-region tests** — personal homelab tool with single user.

---

## QA Effort Estimate

| Priority  | Count | Effort Range       | Notes                                                        |
| --------- | ----- | ------------------ | ------------------------------------------------------------ |
| FIX       | 3     | ~4–6 hours         | Delete stale tests, create fixture boilerplate for both services |
| P0        | 7     | ~8–10 hours        | Core paths, mock setup complexity                            |
| P1        | 17    | ~14–18 hours       | Error paths, mock variants                                   |
| P2        | 17    | ~10–14 hours       | Edge cases, unit tests (simpler)                             |
| P3        | 3     | ~4–6 hours         | Concurrency, exploratory                                     |
| **Total** | ~44   | **~40–54 hours**   | **~1.5–2 weeks, 1 developer, part-time test work**           |

**Assumptions:**

- Includes test design, implementation, debugging, and CI integration.
- Excludes ongoing maintenance (~10% effort).
- Assumes `Collection` mock strategy resolved before P0 implementation begins.
- `pytest` + `pytest-asyncio` + `httpx` already installed.

---

## Implementation Planning Handoff

| Work Item                                     | Owner     | Target              | Dependencies / Notes                                   |
| --------------------------------------------- | --------- | ------------------- | ------------------------------------------------------ |
| Delete/archive `tests/test_main.py`           | Developer | Before test sprint  | ASR-002 blocker                                        |
| Document `Collection` mock strategy           | Developer | Before test sprint  | ASR-001 blocker; see Architecture doc                  |
| Create `tests/test_ldoce5_api.py` (FIX + P0–P2) | Developer | Test sprint week 1 | 21 tests for `ldoce5-api`                              |
| Create `tests/test_anki_writer.py` (FIX + P0–P2) | Developer | Test sprint week 1–2 | 23 tests for `anki-writer`; blocked by mock strategy  |
| Add P3 concurrency test                       | Developer | Test sprint week 2  | Low priority; do last                                  |
| Add `pytest --cov` to CI (if CI exists)       | Developer | Post-sprint         | Optional; homelab may not have CI                      |

---

## Tooling & Access

| Tool or Service          | Purpose                                    | Access Required         | Status |
| ------------------------ | ------------------------------------------ | ----------------------- | ------ |
| pytest                   | Test runner                                | Already installed       | Ready  |
| pytest-asyncio           | Async test support for FastAPI             | Already installed       | Ready  |
| httpx                    | `AsyncClient` + `ASGITransport` for tests  | Already installed       | Ready  |
| pytest-cov               | Coverage reporting                         | `pip install pytest-cov` | Pending |
| unittest.mock            | `patch`, `MagicMock`, `AsyncMock`          | Python stdlib           | Ready  |

---

## Interworking & Regression

| Service/Component    | Impact                                              | Regression Scope                      | Validation Steps                                  |
| -------------------- | --------------------------------------------------- | ------------------------------------- | ------------------------------------------------- |
| **ldoce5-api**       | API contract change → anki-writer lookup breaks    | All AW-* tests that mock ldoce5_client | Verify mock response shape matches real API       |
| **anki-writer**      | Field rename → Anki card format changes            | All AW-API-* tests                    | Check note field names match Collection schema    |
| **anki-sync-server** | External; not under test                           | Manual smoke test post-deploy         | `GET /health` verifies connectivity               |

**Regression test strategy:**

- Run full `pytest tests/` before any merge to main.
- If `ldoce5-api` endpoint response shape changes, update both `tests/test_ldoce5_api.py` and the mock fixtures in `tests/test_anki_writer.py`.

---

## Appendix A: Code Examples

**Complete fixture and test example for `ldoce5-api`:**

```python
# tests/test_ldoce5_api.py
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient, ASGITransport
import main

SAMPLE_LOOKUP_RESPONSE = {
    "word": "happy",
    "pronunciation": "/ˈhæp.i/",
    "senses": [
        {
            "definition_html": "<b>happy</b>: feeling or showing pleasure",
            "example": "She was happy to see him.",
            "pos": "adjective",
        }
    ],
    "selected_sense_index": 0,
    "audio": {
        "uk": {"url": "/audio/GB_happy.spx"},
        "us": {"url": "/audio/US_happy.spx"},
    },
}

@pytest_asyncio.fixture
async def ldoce5_client():
    mock_mdx = MagicMock()
    mock_mdd = MagicMock()
    mock_llm = None  # LLM disabled by default
    main.app.state.mdx_builder = mock_mdx
    main.app.state.mdd_wrapper = mock_mdd
    main.app.state.llm_client = mock_llm
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as ac:
        yield ac, mock_mdx, mock_mdd, mock_llm


@pytest.mark.asyncio
async def test_lookup_success(ldoce5_client):
    ac, mock_mdx, _, _ = ldoce5_client
    mock_mdx.lookup.return_value = SAMPLE_LOOKUP_RESPONSE

    resp = await ac.get("/lookup", params={"word": "happy"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["word"] == "happy"
    assert len(data["senses"]) >= 1
    assert "pronunciation" in data
    assert "audio" in data


@pytest.mark.asyncio
async def test_lookup_not_found(ldoce5_client):
    ac, mock_mdx, _, _ = ldoce5_client
    mock_mdx.lookup.return_value = None  # word not in dictionary

    resp = await ac.get("/lookup", params={"word": "xyznotaword"})

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]
```

**Complete fixture and test example for `anki-writer`:**

```python
# tests/test_anki_writer.py
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from httpx import AsyncClient, ASGITransport, Response
import anki_writer

SAMPLE_LDOCE5_RESPONSE = {
    "word": "happy",
    "pronunciation": "/ˈhæp.i/",
    "senses": [
        {
            "definition_html": "<b>happy</b>: feeling or showing pleasure",
            "example": "She was happy to see him.",
        }
    ],
    "selected_sense_index": 0,
    "audio": {"uk": {"url": "/audio/GB_happy.spx"}, "us": None},
    "warning": None,
}

@pytest_asyncio.fixture
async def aw_client():
    mock_ldoce5 = AsyncMock()
    anki_writer.app.state.ldoce5_client = mock_ldoce5

    with patch('anki_writer.Collection') as mock_col_cls:
        mock_col = MagicMock()
        mock_col_cls.return_value.__enter__ = MagicMock(return_value=mock_col)
        mock_col_cls.return_value = mock_col
        mock_col.find_notes.return_value = []
        mock_col.decks.id.return_value = 1
        mock_col.models.by_name.return_value = {"id": 1}
        new_note = MagicMock()
        new_note.id = 99999
        mock_col.new_note.return_value = new_note
        mock_col.media.dir.return_value = "/tmp/anki_test_media"
        async with AsyncClient(transport=ASGITransport(app=anki_writer.app), base_url="http://test") as ac:
            yield ac, mock_ldoce5, mock_col_cls, mock_col


@pytest.mark.asyncio
async def test_add_word_success(aw_client):
    ac, mock_ldoce5, _, _ = aw_client
    mock_ldoce5.get.return_value = Response(200, json=SAMPLE_LDOCE5_RESPONSE)

    resp = await ac.post("/add-word", json={"word": "happy", "sentence": "I am happy."})

    assert resp.status_code == 200
    data = resp.json()
    assert data["word"] == "happy"
    assert "note_id" in data
    assert data["sense_used"] == 0


@pytest.mark.asyncio
async def test_add_word_duplicate(aw_client):
    ac, mock_ldoce5, _, mock_col = aw_client
    mock_ldoce5.get.return_value = Response(200, json=SAMPLE_LDOCE5_RESPONSE)
    mock_col.find_notes.return_value = [12345]  # existing note found

    resp = await ac.post("/add-word", json={"word": "happy"})

    assert resp.status_code == 422
    assert "duplicate" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_empty(aw_client):
    ac, _, _, _ = aw_client
    resp = await ac.post("/add-word", json={"word": ""})
    assert resp.status_code == 422
    assert "invalid" in resp.json()["detail"]
```

---

## Appendix B: Knowledge Base References

- **Risk Governance**: `_bmad/tea/knowledge/risk-governance.md` — Risk scoring methodology (Probability × Impact, 1–3 scale)
- **Test Levels Framework**: `_bmad/tea/knowledge/test-levels-framework.md` — E2E vs API vs Unit selection criteria
- **Test Quality**: `_bmad/tea/knowledge/test-quality.md` — Definition of Done (no hard waits, <300 lines per file, clear assertions)
- **Architecture doc**: `_bmad-output/test-artifacts/test-design-architecture.md` — Risk details, testability concerns, mitigation plans

---

**Generated by:** BMad TEA Agent
**Workflow:** `_bmad/tea/workflows/testarch/test-design`
**Version:** 4.0 (BMad v6)
