"""
Tests for iPhone-Anki Sync API.

Strategy:
- `mdict_utils` is not installed in the test environment, so we inject a mock
  into `sys.modules` before importing `main`.
- We set app.state directly in the fixture (ASGITransport in httpx 0.28 does
  NOT trigger the ASGI lifespan, so we bypass lifespan entirely in tests).
- The fixture yields (AsyncClient, mock_mdx, mock_http, mock_llm):
    mock_http  → AnkiConnect client (app.state.http_client)
    mock_llm   → LLM client (app.state.llm_client)
"""

import logging
import os
import sys
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Module-level setup: must happen before `import main`
# ---------------------------------------------------------------------------

os.environ["LDOCE5_MDX_PATH"] = "/fake/LDOCE5.mdx"

# Inject mocks for mdict_utils so the top-level import in main.py succeeds
_mock_mdict_utils = MagicMock()
_mock_mdict_utils.reader.query = MagicMock(return_value="")
sys.modules["mdict_utils"] = _mock_mdict_utils  # type: ignore[assignment]
sys.modules["mdict_utils.reader"] = _mock_mdict_utils.reader  # type: ignore[assignment]

import main  # noqa: E402 — must come after sys.modules patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ANKI_OK = {"result": 12345, "error": None}
ANKI_DUPLICATE = {"result": None, "error": "cannot create note because it is a duplicate"}
ANKI_VERSION = {"result": 6, "error": None}

# Minimal LDOCE5 HTML with two senses (one POS block, two Sense spans)
LDOCE5_TWO_SENSES = (
    '<span class="POS">noun</span>'
    '<span class="Sense"><span class="DEF">the job you do regularly to earn money</span>'
    '<span class="EXAMPLE"><span class="BASE">She works at a bank.</span></span></span>'
    '<span class="Sense"><span class="DEF">tasks that need to be done</span>'
    '<span class="EXAMPLE"><span class="BASE">I have a lot of work to do.</span></span></span>'
)

# Single-sense HTML
LDOCE5_ONE_SENSE = (
    '<span class="POS">adjective</span>'
    '<span class="Sense"><span class="DEF">lasting for only a short time</span>'
    '<span class="EXAMPLE"><span class="BASE">Fame is ephemeral, but art endures.</span></span></span>'
)

# Multi-POS HTML: verb block + noun block separated by <hr>
LDOCE5_MULTI_POS = (
    '<span class="POS">verb</span>'
    '<span class="Sense"><span class="DEF">to do a job for money</span>'
    '<span class="EXAMPLE"><span class="BASE">She works in finance.</span></span></span>'
    "<hr>"
    '<span class="POS">noun</span>'
    '<span class="Sense"><span class="DEF">tasks that need doing</span>'
    '<span class="EXAMPLE"><span class="BASE">I have a lot of work.</span></span></span>'
)


def _anki_response(payload: dict) -> MagicMock:
    """Build a mock httpx.Response whose .json() returns *payload*."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _llm_response(number: int) -> MagicMock:
    """Build a mock LLM chat-completions response returning a single digit."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": str(number)}}]
    }
    return mock_resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[
    tuple[AsyncClient, MagicMock, AsyncMock, AsyncMock], None
]:
    """
    Yield (AsyncClient, mock_mdx_builder, mock_http_client, mock_llm_client).

    mock_http  → AnkiConnect calls (app.state.http_client)
    mock_llm   → LLM disambiguation calls (app.state.llm_client)

    Sets app.state directly — ASGITransport (httpx 0.28) does not trigger
    the ASGI lifespan, so we skip it and control state ourselves.
    """
    mock_mdx = MagicMock()
    mock_http = AsyncMock()   # AnkiConnect
    mock_llm = AsyncMock()    # LLM

    main.app.state.mdx_builder = mock_mdx
    main.app.state.http_client = mock_http
    main.app.state.llm_client = mock_llm

    async with AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://test"
    ) as ac:
        yield ac, mock_mdx, mock_http, mock_llm

    # Clean up state between tests
    for key in ("mdx_builder", "http_client", "llm_client"):
        try:
            delattr(main.app.state, key)
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Original tests (field names updated: expression→word, reading→pronunciation,
# glossary→definition)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_word_success(client):
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = [
        "<b>ephemeral</b>: lasting for only a short time"
    ]
    mock_http.post.return_value = _anki_response(ANKI_OK)

    resp = await ac.post("/add-word", json={"word": "Ephemeral"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["word"] == "ephemeral"  # lowercased
    assert data["definition"] != ""
    assert data["anki_response"]["result"] == 12345


@pytest.mark.asyncio
async def test_add_word_not_found(client):
    ac, mock_mdx, _, __ = client
    mock_mdx.mdx_lookup.return_value = []  # word not in dictionary

    resp = await ac.post("/add-word", json={"word": "xyznonexistent"})

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_anki_unreachable(client):
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = ["<b>test</b>: a procedure"]

    import httpx as _httpx

    mock_http.post.side_effect = _httpx.ConnectError("connection refused")

    resp = await ac.post("/add-word", json={"word": "test"})

    assert resp.status_code == 502
    assert "AnkiConnect" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_duplicate(client):
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = ["<b>test</b>: a procedure"]
    mock_http.post.return_value = _anki_response(ANKI_DUPLICATE)

    resp = await ac.post("/add-word", json={"word": "test"})

    assert resp.status_code == 422
    assert "duplicate" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_invalid_input_empty(client):
    ac, _, __, ___ = client
    resp = await ac.post("/add-word", json={"word": "   "})
    assert resp.status_code == 422
    assert "invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_invalid_input_too_long(client):
    ac, _, __, ___ = client
    resp = await ac.post("/add-word", json={"word": "a" * 101})
    assert resp.status_code == 422
    assert "invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_sentence_too_long(client):
    """F3: sentence longer than 2000 characters should be rejected by Pydantic."""
    ac, _, __, ___ = client
    resp = await ac.post("/add-word", json={"word": "test", "sentence": "a" * 2001})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_health_all_ok(client):
    ac, _, mock_http, __ = client
    mock_http.post.return_value = _anki_response(ANKI_VERSION)

    resp = await ac.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mdx_loaded"] is True
    assert data["anki_connected"] is True


@pytest.mark.asyncio
async def test_health_anki_down(client):
    ac, _, mock_http, __ = client

    import httpx as _httpx

    mock_http.post.side_effect = _httpx.ConnectError("connection refused")

    resp = await ac.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mdx_loaded"] is True
    assert data["anki_connected"] is False


# ---------------------------------------------------------------------------
# Additional tests: @@@LINK redirect, multi-result, error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_word_link_redirect(client):
    """@@@LINK= entry should follow redirect and return the target's definition."""
    ac, mock_mdx, mock_http, _ = client

    def side_effect(word):
        if word == "ephemeral":
            return ["@@@LINK=ephemeron"]
        if word == "ephemeron":
            return ["<b>ephemeron</b>: something short-lived"]
        return []

    mock_mdx.mdx_lookup.side_effect = side_effect
    mock_http.post.return_value = _anki_response(ANKI_OK)

    resp = await ac.post("/add-word", json={"word": "ephemeral"})

    assert resp.status_code == 200
    assert "ephemeron" in resp.json()["definition"]


@pytest.mark.asyncio
async def test_add_word_link_self_loop(client):
    """A self-referencing @@@LINK= should not loop and should return 404."""
    ac, mock_mdx, _, __ = client
    mock_mdx.mdx_lookup.return_value = ["@@@LINK=ephemeral"]  # points to itself

    resp = await ac.post("/add-word", json={"word": "ephemeral"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_word_link_max_depth(client):
    """A redirect chain deeper than 3 levels should give up and return 404."""
    ac, mock_mdx, _, __ = client

    # a → b → c → d (depth 3 hit on 'd')
    def side_effect(word):
        chain = {"a": ["@@@LINK=b"], "b": ["@@@LINK=c"], "c": ["@@@LINK=d"], "d": ["@@@LINK=e"]}
        return chain.get(word, [])

    mock_mdx.mdx_lookup.side_effect = side_effect

    resp = await ac.post("/add-word", json={"word": "a"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_word_multiple_definitions_joined(client):
    """Multiple MDX entries should be joined with <hr> in the definition."""
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = [
        "<b>run</b> (verb): to move fast",
        "<b>run</b> (noun): a period of running",
    ]
    mock_http.post.return_value = _anki_response(ANKI_OK)

    resp = await ac.post("/add-word", json={"word": "run"})

    assert resp.status_code == 200
    assert "<hr>" in resp.json()["definition"]


@pytest.mark.asyncio
async def test_add_word_anki_generic_error(client):
    """Non-duplicate Anki business errors should return 422 without 'duplicate' prefix."""
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = ["<b>test</b>: a procedure"]
    mock_http.post.return_value = _anki_response(
        {"result": None, "error": "deck was not found"}
    )

    resp = await ac.post("/add-word", json={"word": "test"})

    assert resp.status_code == 422
    assert "duplicate" not in resp.json()["detail"]
    assert "deck was not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_add_word_anki_non_json_response(client):
    """AnkiConnect returning non-JSON should map to 502, not 500."""
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = ["<b>test</b>: a procedure"]

    bad_resp = MagicMock()
    bad_resp.json.side_effect = ValueError("not valid JSON")
    mock_http.post.return_value = bad_resp

    resp = await ac.post("/add-word", json={"word": "test"})

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_add_word_anki_request_error(client):
    """Any httpx.RequestError subclass (e.g. DNS failure) should map to 502."""
    ac, mock_mdx, mock_http, _ = client
    mock_mdx.mdx_lookup.return_value = ["<b>test</b>: a procedure"]

    import httpx as _httpx

    mock_http.post.side_effect = _httpx.RequestError("DNS lookup failed")

    resp = await ac.post("/add-word", json={"word": "test"})

    assert resp.status_code == 502
    assert "AnkiConnect" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Unit tests: _extract_senses
# ---------------------------------------------------------------------------


def test_extract_senses_single_pos_two_senses():
    """_extract_senses returns one entry per Sense span within a POS block."""
    senses = main._extract_senses(LDOCE5_TWO_SENSES)
    assert len(senses) == 2
    assert senses[0]["pos"] == "noun"
    assert "the job you do regularly" in senses[0]["definition"]
    assert "tasks that need to be done" in senses[1]["definition"]
    assert senses[0]["example"] == "She works at a bank."
    assert senses[1]["example"] == "I have a lot of work to do."


def test_extract_senses_pos_header_in_each_sense_html():
    """F6: Each sense's html must include the POS block header."""
    senses = main._extract_senses(LDOCE5_TWO_SENSES)
    for sense in senses:
        assert "noun" in sense["html"], "POS header must be present in each sense HTML"


def test_extract_senses_multi_pos():
    """F11: _extract_senses handles HTML with <hr> separating two POS blocks."""
    senses = main._extract_senses(LDOCE5_MULTI_POS)
    assert len(senses) == 2
    assert senses[0]["pos"] == "verb"
    assert "to do a job for money" in senses[0]["definition"]
    assert senses[1]["pos"] == "noun"
    assert "tasks that need doing" in senses[1]["definition"]
    # Each sense html must contain its own POS header
    assert "verb" in senses[0]["html"]
    assert "noun" in senses[1]["html"]


def test_extract_senses_def_with_nested_span():
    """F9: DEF extraction handles nested spans inside the definition correctly."""
    html_with_nested = (
        '<span class="POS">verb</span>'
        '<span class="Sense">'
        '<span class="DEF">to move <span class="REF">quickly</span> along</span>'
        "</span>"
    )
    senses = main._extract_senses(html_with_nested)
    assert len(senses) == 1
    # All three words should be captured — not truncated at inner </span>
    assert "to move quickly along" == senses[0]["definition"]


# ---------------------------------------------------------------------------
# New tests: AI sense disambiguation (AC9-14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_word_with_sentence_ai_mode(client):
    """AC9: With sentence + LLM_API_KEY, AI picks the matching sense.

    The mock LLM returns "2", so sense_idx=1 (second sense) is selected.
    definition should contain only that sense's HTML (incl. POS header).
    sentence in the Anki payload should combine user context + LDOCE example.
    """
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_TWO_SENSES]

    mock_llm.post.return_value = _llm_response(2)
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        resp = await ac.post(
            "/add-word",
            json={"word": "work", "sentence": "She put a lot of work into the project."},
        )

    assert resp.status_code == 200
    data = resp.json()
    # Only the matched sense HTML (second sense) should be in definition
    assert "tasks that need to be done" in data["definition"]
    assert "the job you do regularly" not in data["definition"]
    # POS header must still be present (F6 fix)
    assert "noun" in data["definition"]

    # F7: verify sentence_field written into the Anki payload
    anki_call = mock_http.post.call_args_list[0]
    anki_payload = anki_call.kwargs["json"]
    sentence_in_anki = anki_payload["params"]["note"]["fields"]["sentence"]
    assert "She put a lot of work into the project." in sentence_in_anki
    assert "— LDOCE: I have a lot of work to do." in sentence_in_anki


@pytest.mark.asyncio
async def test_add_word_ai_fallback_on_error(client, caplog):
    """AC10: If AI call fails, fall back to sense 0, return 200, and log a warning."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_TWO_SENSES]

    import httpx as _httpx

    mock_llm.post.side_effect = _httpx.ConnectError("LLM unreachable")
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        with caplog.at_level(logging.WARNING, logger="main"):
            resp = await ac.post(
                "/add-word",
                json={"word": "work", "sentence": "She put a lot of work into the project."},
            )

    assert resp.status_code == 200
    assert any("AI sense disambiguation failed" in r.message for r in caplog.records)

    # F8: fallback definition should be sense 0's HTML (first sense, with POS header)
    anki_call = mock_http.post.call_args_list[0]
    definition_in_anki = anki_call.kwargs["json"]["params"]["note"]["fields"]["definition"]
    assert "the job you do regularly" in definition_in_anki
    assert "noun" in definition_in_anki  # POS header present


@pytest.mark.asyncio
async def test_add_word_no_sentence_unchanged(client):
    """AC12: Without sentence, behavior is unchanged — full HTML, first-sense example."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_TWO_SENSES]
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        resp = await ac.post("/add-word", json={"word": "work"})

    assert resp.status_code == 200
    # No sentence → full HTML (both senses)
    assert resp.json()["definition"] == LDOCE5_TWO_SENSES
    # LLM was not called
    mock_llm.post.assert_not_called()


@pytest.mark.asyncio
async def test_add_word_no_llm_key(client):
    """AC11: Empty LLM_API_KEY + sentence → skip AI, return full HTML."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_TWO_SENSES]
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", ""):
        resp = await ac.post(
            "/add-word",
            json={"word": "work", "sentence": "She put a lot of work into the project."},
        )

    assert resp.status_code == 200
    # No LLM key → original mode, full HTML
    assert resp.json()["definition"] == LDOCE5_TWO_SENSES
    mock_llm.post.assert_not_called()


@pytest.mark.asyncio
async def test_add_word_single_sense_skips_llm(client):
    """AC13: Single sense → skip LLM call entirely, use that sense directly."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_ONE_SENSE]
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        resp = await ac.post(
            "/add-word",
            json={"word": "ephemeral", "sentence": "The ephemeral beauty of cherry blossoms."},
        )

    assert resp.status_code == 200
    # LLM was NOT called (only one sense available)
    mock_llm.post.assert_not_called()
    # AnkiConnect was called exactly once
    assert mock_http.post.call_count == 1


@pytest.mark.asyncio
async def test_add_word_not_found_ai_not_triggered(client):
    """AC14: Word not found → 404, AI logic should not be triggered."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = []

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        resp = await ac.post(
            "/add-word",
            json={"word": "xyznonexistent", "sentence": "Some context sentence."},
        )

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]
    mock_llm.post.assert_not_called()
    mock_http.post.assert_not_called()


@pytest.mark.asyncio
async def test_add_word_ai_multi_pos_html(client):
    """F12: AI branch selects correct sense from multi-POS word (HTML with <hr>)."""
    ac, mock_mdx, mock_http, mock_llm = client
    mock_mdx.mdx_lookup.return_value = [LDOCE5_MULTI_POS]

    # LLM picks sense 1 (verb: "to do a job for money")
    mock_llm.post.return_value = _llm_response(1)
    mock_http.post.return_value = _anki_response(ANKI_OK)

    with patch.object(main, "LLM_API_KEY", "test-api-key"):
        resp = await ac.post(
            "/add-word",
            json={"word": "work", "sentence": "She works in the city center."},
        )

    assert resp.status_code == 200
    data = resp.json()
    # definition should be the verb sense only
    assert "to do a job for money" in data["definition"]
    assert "tasks that need doing" not in data["definition"]
    # POS header of the verb block should be present
    assert "verb" in data["definition"]

    # LLM prompt should list both senses (one from each POS block)
    llm_call = mock_llm.post.call_args_list[0]
    prompt_content = llm_call.kwargs["json"]["messages"][0]["content"]
    assert "verb" in prompt_content
    assert "noun" in prompt_content
    assert "(1-2)" in prompt_content
