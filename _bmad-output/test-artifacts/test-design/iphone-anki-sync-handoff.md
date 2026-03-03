---
title: 'TEA Test Design → BMAD Handoff Document'
version: '1.0'
workflowType: 'testarch-test-design-handoff'
inputDocuments:
  - _bmad-output/test-artifacts/test-design-architecture.md
  - _bmad-output/test-artifacts/test-design-qa.md
sourceWorkflow: 'testarch-test-design'
generatedBy: 'TEA Master Test Architect'
generatedAt: '2026-03-03'
projectName: 'iphone-anki-sync'
---

# TEA → BMAD Integration Handoff: iphone-anki-sync

## Purpose

This document bridges TEA's test design outputs with BMAD's epic/story decomposition workflow. It provides structured integration guidance so that quality requirements, risk assessments, and test strategies flow into implementation planning.

## TEA Artifacts Inventory

| Artifact                        | Path                                                                   | BMAD Integration Point                                        |
| ------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------- |
| Architecture Test Design        | `_bmad-output/test-artifacts/test-design-architecture.md`             | Developer blockers, architectural prerequisites, risk gates   |
| QA Test Design                  | `_bmad-output/test-artifacts/test-design-qa.md`                       | Story acceptance criteria, test scenarios, effort estimates   |
| Risk Assessment (embedded)      | See Architecture doc, Risk Assessment section                         | Epic risk classification, story priority (R-001 to R-008)     |
| Coverage Strategy (embedded)    | See QA doc, Test Coverage Plan section                                | Story test requirements (P0–P3, ~44 scenarios)                |

## Epic-Level Integration Guidance

### Risk References (P0/P1 Risks → Epic Quality Gates)

| Risk ID | Score | Category | Description                                          | Epic Gate                                    |
| ------- | ----- | -------- | ---------------------------------------------------- | -------------------------------------------- |
| R-001   | 9     | TECH     | Stale tests — all 25+ existing tests are invalid     | **BLOCKER**: Fix before any other epic work  |
| R-002   | 6     | TECH     | `anki_writer.py` zero test coverage                  | Epic must include test implementation story  |
| R-003   | 4     | TECH     | `anki` package not importable in dev/CI              | Dependency: resolve before anki-writer tests |
| R-005   | 4     | OPS      | Network sync call to anki-sync-server in all writes  | Mock required in all test environments       |

### Quality Gates

**Before epic can be marked "done":**

1. `tests/test_main.py` removed or archived (R-001 resolved)
2. `Collection` mock strategy in place (ASR-001 resolved)
3. All P0 tests passing (7 tests across both services)
4. All P1 tests passing or failures triaged (17 tests)
5. `anki_writer.py` coverage ≥80%

## Story-Level Integration Guidance

### P0 Test Scenarios → Story Acceptance Criteria

These test scenarios should appear as acceptance criteria in their respective implementation stories:

| Test ID     | Service       | Scenario                                              | Story Acceptance Criterion                                              |
| ----------- | ------------- | ----------------------------------------------------- | ----------------------------------------------------------------------- |
| LDO-P0-01   | ldoce5-api    | `GET /lookup` success with word+sentence              | API returns `word`, `senses[]`, `audio`, `pronunciation` for known word |
| LDO-P0-02   | ldoce5-api    | `GET /lookup` word not found                          | API returns 404 with "not found" detail                                 |
| LDO-P0-03   | ldoce5-api    | `GET /health` all components loaded                   | Health endpoint returns `status: ok`, `mdx_loaded: true`                |
| AW-P0-01    | anki-writer   | `POST /add-word` success with sentence                | Returns 200 with `note_id`, `sense_used`, `definition`                  |
| AW-P0-02    | anki-writer   | `POST /add-word` duplicate word rejected              | Returns 422 with `"duplicate:"` in detail for existing word             |
| AW-P0-03    | anki-writer   | `POST /add-word` empty word rejected                  | Returns 422 with `"invalid: word cannot be empty"`                      |
| AW-P0-04    | anki-writer   | `GET /health` collection accessible                   | Returns `collection_accessible: true` when collection file exists       |

### FIX Work (Must-Do Before Any Tests)

| Fix ID  | Action                                                  | Owner     | Acceptance Criterion                                             |
| ------- | ------------------------------------------------------- | --------- | ---------------------------------------------------------------- |
| FIX-001 | Delete/archive `tests/test_main.py`                     | Developer | No `http_client` or `anki_connected` in test codebase           |
| FIX-002 | Create `tests/test_ldoce5_api.py` with correct fixtures | Developer | Fixture uses `mdx_builder`, `mdd_wrapper`, `llm_client` mocks   |
| FIX-003 | Create `tests/test_anki_writer.py` with Collection mock | Developer | Fixture patches `anki_writer.Collection` and `ldoce5_client`    |

## Risk-to-Story Mapping

| Risk ID | Category | P×I | Story/Work Item                          | Test Level | QA Scenario(s)           |
| ------- | -------- | --- | ---------------------------------------- | ---------- | ------------------------ |
| R-001   | TECH     | 9   | FIX: Delete stale tests + new fixtures   | API        | FIX-001, FIX-002, FIX-003 |
| R-002   | TECH     | 6   | STORY: Write anki-writer test suite      | API        | AW-P0-*, AW-P1-*          |
| R-003   | TECH     | 4   | TASK: Document Collection mock strategy  | —          | ASR-001 prerequisite      |
| R-004   | TECH     | 4   | TASK: Add lock timeout to `_col_lock`    | Integration | AW-P3-01                 |
| R-005   | OPS      | 4   | TASK: Mock sync calls in all fixtures    | API        | AW-P1-07, AW-P1-09       |
| R-006   | OPS      | 3   | TEST: LLM disabled fallback path         | API        | LDO-P1-07                |
| R-007   | DATA     | 2   | TEST: Audio failure warning field        | API        | AW-P2-02                 |
| R-008   | DATA     | 2   | Monitor: Duplicate by word-only          | —          | AW-P0-02                 |

## Recommended BMAD → TEA Workflow Sequence

1. **TEA Test Design** (`testarch-test-design`) → produces this handoff ✅ DONE
2. **FIX stale tests** → developer removes `tests/test_main.py`, creates fixture boilerplate
3. **TEA ATDD** (`testarch-atdd`) → generates failing P0 acceptance tests
4. **Implementation** → developer writes code with test-first guidance
5. **TEA Automate** (`testarch-automate`) → expands test coverage to P1/P2
6. **TEA Trace** (`testarch-trace`) → validates coverage completeness vs. requirements

## Phase Transition Quality Gates

| From Phase              | To Phase            | Gate Criteria                                                    |
| ----------------------- | ------------------- | ---------------------------------------------------------------- |
| Test Design (current)   | FIX Work            | ASR-001 resolved (Collection mock strategy decided)              |
| FIX Work                | ATDD                | `tests/test_main.py` removed; new fixture files created          |
| ATDD                    | Implementation      | Failing P0 acceptance tests exist for both services              |
| Implementation          | Test Automation     | All P0 tests passing; P1 tests in progress                       |
| Test Automation         | Release             | ≥80% coverage on `main.py` + `anki_writer.py`; all P0+P1 passing |
