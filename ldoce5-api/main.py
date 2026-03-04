from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from html import unescape
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from mdict_utils.reader import query as _mdict_query
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
LDOCE5_MDX_PATH = os.environ.get("LDOCE5_MDX_PATH", "")
LDOCE5_MDD_PATH = os.environ.get("LDOCE5_MDD_PATH", "")
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5050"))
# LLM config — read from module scope; llm_client is injected for testability.
# Empty LLM_API_KEY disables AI disambiguation entirely.
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "meta/llama-3.3-70b-instruct")


# ---------------------------------------------------------------------------
# MDX / MDD wrappers
# ---------------------------------------------------------------------------


class MdxWrapper:
    """Wraps mdict-utils + the MDX_INDEX SQLite DB for fast word lookup.

    LDOCE5 stores some words with a part-of-speech suffix in the key,
    e.g. ``run ,noun`` / ``run ,verb``.  ``mdx_lookup`` transparently
    queries both the exact key and all ``word ,<pos>`` variants.
    """

    def __init__(self, mdx_path: str) -> None:
        self.mdx_path = mdx_path
        db_path = mdx_path + ".db"
        if not os.path.exists(db_path):
            raise RuntimeError(f"MDX index DB not found: '{db_path}'")
        self._db = sqlite3.connect(db_path, check_same_thread=False)

    def mdx_lookup(self, word: str) -> list[str]:
        """Return a list of HTML definition strings for *word*.

        Returns an empty list if the word is not in the dictionary.
        """
        rows = self._db.execute(
            "SELECT key_text FROM MDX_INDEX "
            "WHERE key_text=? COLLATE NOCASE OR key_text LIKE ? COLLATE NOCASE",
            (word, word + " ,%"),
        ).fetchall()
        results: list[str] = []
        for (key,) in rows:
            content = _mdict_query(self.mdx_path, key, None)
            if content:
                results.append(content)
        return results


class MddWrapper:
    """Wraps mdict-utils + the MDD_INDEX SQLite DB for audio file lookup."""

    def __init__(self, mdd_path: str) -> None:
        self.mdd_path = mdd_path
        db_path = mdd_path + ".db"
        if not os.path.exists(db_path):
            raise RuntimeError(f"MDD index DB not found: '{db_path}'")
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        # Detect table name: MDD_INDEX or MDX_INDEX depending on tool version
        tables = {r[0] for r in self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self._table = "MDD_INDEX" if "MDD_INDEX" in tables else "MDX_INDEX"

    def warmup(self) -> None:
        """Pre-warm OS file cache by performing one real MDD lookup.

        MDict key blocks are read on every ``_mdict_query`` call.  Reading them
        once at startup ensures they are in the OS page cache, eliminating the
        ~10 s cold-start delay that would otherwise block the event loop.
        """
        row = self._db.execute(
            f"SELECT key_text FROM {self._table} LIMIT 1"
        ).fetchone()
        if row:
            try:
                _mdict_query(self.mdd_path, row[0], None)
            except Exception:
                pass  # warmup failure is non-fatal

    def mdd_lookup(self, filename: str) -> Optional[bytes]:
        """Return raw bytes for *filename* (e.g. 'GB_ephemeral0205.spx'), or None."""
        key = "\\" + filename  # MDD keys are prefixed with backslash
        row = self._db.execute(
            f"SELECT key_text FROM {self._table} WHERE key_text=? COLLATE NOCASE",
            (key,),
        ).fetchone()
        if not row:
            # Try without backslash prefix
            row = self._db.execute(
                f"SELECT key_text FROM {self._table} WHERE key_text=? COLLATE NOCASE",
                (filename,),
            ).fetchone()
        if not row:
            return None
        content = _mdict_query(self.mdd_path, row[0], None)
        return content if isinstance(content, bytes) else None


# ---------------------------------------------------------------------------
# HTML extraction helpers
# ---------------------------------------------------------------------------


def _extract_reading(html: str) -> str:
    """Extract IPA phonetic notation from the first PRON span in LDOCE5 HTML.

    Returns a string like ``/ɪˈfemərəl/``, or empty string if not found.
    """
    m = re.search(r'class="PRON"\s*>(.*?)</span>', html, re.DOTALL)
    if not m:
        return ""
    text = re.sub(r"<[^>]+>", "", m.group(1))
    return f"/{unescape(text).strip()}/"


def _extract_audio_filenames(html: str) -> dict:
    """Return {"uk": "GB_xxx.spx", "us": "US_xxx.spx"}, values may be None."""
    bre_match = re.search(r'href="sound://(GB_[^"]+\.spx)"', html)
    ame_match = re.search(r'href="sound://(US_[^"]+\.spx)"', html)
    return {
        "bre": bre_match.group(1) if bre_match else None,
        "ame": ame_match.group(1) if ame_match else None,
    }


def _extract_sentence(html: str) -> str:
    """Extract the first example sentence from LDOCE5 HTML.

    Example sentences live in ``<span class="EXAMPLE" >`` elements; the actual
    text is in the nested ``<span class="BASE" >`` child.  Returns empty string
    if no example is present (some short entries have none).
    """
    m = re.search(
        r'class="EXAMPLE"[^>]*>.*?class="BASE"[^>]*>(.*?)</span>',
        html,
        re.DOTALL,
    )
    if not m:
        return ""
    text = re.sub(r"<[^>]+>", "", m.group(1))
    return unescape(text).strip()


def _extract_all_span_text(html: str, class_name: str) -> list[str]:
    """Extract text from ALL <span class="class_name"> elements using a depth counter."""
    pattern = rf'<span[^>]+class="{re.escape(class_name)}"[^>]*>'
    results = []
    search_from = 0
    while True:
        m = re.search(pattern, html[search_from:])
        if not m:
            break
        content_start = search_from + m.end()
        pos = content_start
        depth = 1
        while pos < len(html) and depth > 0:
            next_open = html.find("<span", pos)
            next_close = html.find("</span>", pos)
            if next_close == -1:
                pos = len(html)
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 1
            else:
                depth -= 1
                if depth == 0:
                    text = unescape(re.sub(r"<[^>]+>", "", html[content_start:next_close])).strip()
                    if text:
                        results.append(text)
                    search_from = next_close + 7
                    break
                pos = next_close + 7
        else:
            search_from = content_start
    return results


def _extract_span_text(html: str, class_name: str) -> str:
    """Extract the full text content of the first <span class="class_name"> element.

    Uses a depth counter to handle nested ``<span>`` elements correctly,
    unlike a naive regex that stops at the first ``</span>``.
    Returns empty string if the span is not found.
    """
    pattern = rf'<span[^>]+class="{re.escape(class_name)}"[^>]*>'
    m = re.search(pattern, html)
    if not m:
        return ""
    pos = m.end()
    depth = 1
    while pos < len(html) and depth > 0:
        next_open = html.find("<span", pos)
        next_close = html.find("</span>", pos)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 1
        else:
            depth -= 1
            if depth == 0:
                content = html[m.end():next_close]
                return unescape(re.sub(r"<[^>]+>", "", content)).strip()
            pos = next_close + 7  # len("</span>") == 7
    return ""


def _strip_span_class(html: str, class_name: str) -> str:
    """Remove all <span class="class_name"> elements and their descendants from html.

    Uses a depth counter to correctly handle nested ``<span>`` elements.
    """
    result: list[str] = []
    pattern = rf'<span[^>]+class="{re.escape(class_name)}"[^>]*>'
    pos = 0
    while True:
        m = re.search(pattern, html[pos:])
        if not m:
            result.append(html[pos:])
            break
        result.append(html[pos : pos + m.start()])
        cur = pos + m.end()
        depth = 1
        while cur < len(html) and depth > 0:
            next_open = html.find("<span", cur)
            next_close = html.find("</span>", cur)
            if next_close == -1:
                cur = len(html)
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                cur = next_open + 1
            else:
                depth -= 1
                cur = next_close + 7  # len("</span>") == 7
        pos = cur
    return "".join(result)


def _extract_senses(html: str) -> list[dict]:
    """Extract individual sense entries from LDOCE5 definition HTML.

    POS blocks are separated by ``<hr>`` (the same separator that
    ``_lookup_word`` uses to join multiple MDX entries).  Within each block,
    ``<span class="Sense"`` boundaries delimit individual senses.

    Each returned dict contains:
    - ``pos``: part-of-speech label (str, may be empty)
    - ``sense_num``: 1-based sense number within the POS block (int)
    - ``definition``: plain-text definition extracted from ``<span class="DEF">``
    - ``example``: plain-text first example from ``<span class="EXAMPLE">``
    - ``definition_html``: raw HTML slice for this sense, **including the POS block header**
      (everything before the first Sense span) so the card retains POS context.
    """
    senses: list[dict] = []

    blocks = re.split(r"<hr\s*/?>", html)
    for block in blocks:
        # Extract POS label for this block (use depth-aware extractor for nested spans)
        pos = _extract_span_text(block, "POS")

        # Find start positions of every <span class="Sense" ...>
        sense_starts = [m.start() for m in re.finditer(r'<span[^>]+class="Sense"', block)]

        if not sense_starts:
            # No Sense spans — treat the whole block as a single sense
            definition = _extract_span_text(block, "DEF")
            ex_match = re.search(
                r'class="EXAMPLE"[^>]*>.*?class="BASE"[^>]*>(.*?)</span>',
                block,
                re.DOTALL,
            )
            example = (
                unescape(re.sub(r"<[^>]+>", "", ex_match.group(1))).strip()
                if ex_match
                else ""
            )
            gram = _extract_span_text(block, "GRAM")
            freq = _extract_all_span_text(block, "FREQ")
            senses.append(
                {
                    "pos": pos,
                    "sense_num": 1,
                    "definition": definition,
                    "example": example,
                    "gram": gram,
                    "freq": freq,
                    "definition_html": block,
                }
            )
            continue

        # The header (POS label, grammar info, etc.) precedes the first Sense span.
        # Prepend it to every sense's HTML so each card retains part-of-speech context.
        block_header = block[:sense_starts[0]]
        gram = _extract_span_text(block_header, "GRAM")
        freq = _extract_all_span_text(block_header, "FREQ")

        # Strip Tail (derivatives / cross-references) from the end of the block.
        tail_match = re.search(r'<span[^>]+class="Tail"', block)
        block_end = tail_match.start() if tail_match else len(block)

        for i, start in enumerate(sense_starts):
            end = sense_starts[i + 1] if i + 1 < len(sense_starts) else block_end
            sense_html = _strip_span_class(
                block_header + block[start:end], "SE_EntryAssets"
            )

            definition = _extract_span_text(sense_html, "DEF")
            ex_match = re.search(
                r'class="EXAMPLE"[^>]*>.*?class="BASE"[^>]*>(.*?)</span>',
                sense_html,
                re.DOTALL,
            )
            example = (
                unescape(re.sub(r"<[^>]+>", "", ex_match.group(1))).strip()
                if ex_match
                else ""
            )
            senses.append(
                {
                    "pos": pos,
                    "sense_num": i + 1,
                    "definition": definition,
                    "example": example,
                    "gram": gram,
                    "freq": freq,
                    "definition_html": sense_html,
                }
            )

    return senses


async def _ai_pick_sense(
    word: str,
    context: str,
    senses: list[dict],
    llm_client: httpx.AsyncClient,
) -> Optional[int]:
    """Call the LLM to pick the best matching sense index (0-based).

    Uses a dedicated ``llm_client`` separate from the AnkiConnect client to
    avoid retry interference and allow independent timeout tuning.

    On any failure, logs a warning and returns 0 (first sense) as a safe
    fallback so card creation still proceeds normally.

    NOTE: ``context`` is raw user input inserted into the prompt.  Prompt
    injection is an accepted risk for a personal-use tool; sanitize or restrict
    the endpoint if it is ever exposed publicly.
    """
    numbered = "\n".join(
        f"{i + 1}. [{s['pos']}] {s['definition']}"
        + (f" — e.g. \"{s['example']}\"" if s["example"] else "")
        for i, s in enumerate(senses)
    )
    prompt = (
        f'You are a dictionary sense disambiguation assistant.\n\n'
        f'Word: "{word}"\n'
        f'Context sentence: "{context}"\n\n'
        f"Available senses:\n{numbered}\n\n"
        f"Which sense number (1-{len(senses)}) best matches the word's meaning "
        f"in the context sentence?\nReply with ONLY the number."
    )

    try:
        resp = await llm_client.post(
            LLM_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
                "temperature": 0,
            },
        )
        resp.raise_for_status()  # surface 4xx/5xx as distinct error types
        content = resp.json()["choices"][0]["message"]["content"]
        raw_idx = int(re.search(r"\d+", content).group()) - 1
        return max(0, min(raw_idx, len(senses) - 1))
    except Exception as exc:
        # Log only the exception type to avoid leaking credentials that
        # some HTTP client implementations embed in error messages.
        logger.warning(
            "AI sense disambiguation failed (%s), falling back to sense 0",
            type(exc).__name__,
        )
        return None


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    if not LDOCE5_MDX_PATH or not os.path.exists(LDOCE5_MDX_PATH):
        raise RuntimeError(
            f"LDOCE5_MDX_PATH is not set or file does not exist: '{LDOCE5_MDX_PATH}'"
        )

    try:
        logger.info("Opening MDX dictionary: %s", LDOCE5_MDX_PATH)
        app.state.mdx_builder = MdxWrapper(LDOCE5_MDX_PATH)
        logger.info("MDX dictionary ready.")
        logger.info("Pre-warming MDX file cache...")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, app.state.mdx_builder.mdx_lookup, "the")
        logger.info("MDX cache warm-up complete.")
    except Exception as exc:
        logger.error("Failed to initialise MDX: %s", exc)
        raise RuntimeError(f"Failed to initialise MDX: {exc}") from exc

    if LDOCE5_MDD_PATH and os.path.exists(LDOCE5_MDD_PATH):
        try:
            app.state.mdd_wrapper = MddWrapper(LDOCE5_MDD_PATH)
            logger.info("MDD audio file ready: %s", LDOCE5_MDD_PATH)
            logger.info("Pre-warming MDD file cache (may take a few seconds on cold start)...")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, app.state.mdd_wrapper.warmup)
            logger.info("MDD cache warm-up complete.")
        except Exception as exc:
            logger.warning("MDD audio not available: %s", exc)
            app.state.mdd_wrapper = None
    else:
        app.state.mdd_wrapper = None
        logger.info("LDOCE5_MDD_PATH not set — audio field will be empty.")

    # LLM client: no retries (avoid double token consumption on slow responses).
    app.state.llm_client = httpx.AsyncClient(timeout=15.0)

    logger.info(
        "LLM sense disambiguation: %s",
        "enabled (model=%s)" % LLM_MODEL if LLM_API_KEY else "disabled (LLM_API_KEY not set)",
    )
    logger.info("LLM client created.")

    yield

    # --- Shutdown ---
    await app.state.llm_client.aclose()
    logger.info("LLM client closed.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LDOCE5 Dictionary API", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lookup_word(mdx_builder, word: str, depth: int = 0) -> Optional[str]:
    """Look up *word* in the MDX dictionary.

    Handles ``@@@LINK=<target>`` redirect entries by following them
    recursively (up to 3 levels deep to prevent infinite loops).
    Multiple valid definitions are joined with ``<hr>``.
    """
    if depth >= 3:
        return None

    results = mdx_builder.mdx_lookup(word)
    valid_entries: list[str] = []

    for entry in results:
        stripped = entry.strip() if entry else ""
        if not stripped:
            continue

        if stripped.startswith("@@@LINK="):
            target = stripped[len("@@@LINK="):].strip()
            if target.lower() == word.lower():
                continue
            resolved = _lookup_word(mdx_builder, target, depth + 1)
            if resolved:
                valid_entries.append(resolved)
        else:
            valid_entries.append(stripped)

    if not valid_entries:
        return None

    return "<hr>".join(valid_entries)


def _spx_to_mp3(spx_data: bytes) -> tuple[bytes, str] | tuple[None, None]:
    """Convert Speex (.spx) audio bytes to MP3 via ffmpeg subprocess.

    Returns (mp3_bytes, mp3_filename) on success, or (None, None) on failure.
    ffmpeg must be installed on the host system.
    """
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".spx", delete=False) as src:
        src.write(spx_data)
        src_path = src.name
    mp3_path = src_path.replace(".spx", ".mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-q:a", "4", mp3_path],
            check=True,
            capture_output=True,
        )
        with open(mp3_path, "rb") as f:
            return f.read(), None
    except Exception as exc:
        logger.warning("ffmpeg conversion failed (%s)", type(exc).__name__)
        return None, None
    finally:
        import os
        for p in (src_path, mp3_path):
            try:
                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Return service health: MDX load status + LLM availability."""
    mdx_loaded = bool(getattr(app.state, "mdx_builder", None))
    mdd_loaded = bool(getattr(app.state, "mdd_wrapper", None))
    llm_enabled = bool(LLM_API_KEY)

    return {
        "status": "ok",
        "mdx_loaded": mdx_loaded,
        "mdd_loaded": mdd_loaded,
        "llm_enabled": llm_enabled,
    }


@app.get("/lookup")
async def lookup(
    word: str,
    sentence: Optional[str] = Query(default=None, max_length=2000),
):
    """Look up word in LDOCE5. Returns senses, pronunciation, audio URLs, and optional AI sense selection."""
    word = word.strip().lower()
    if not word:
        raise HTTPException(status_code=422, detail="invalid: word cannot be empty")
    if len(word) > 100:
        raise HTTPException(
            status_code=422,
            detail="invalid: word exceeds maximum length of 100 characters",
        )

    # Dictionary lookup — run in thread pool to avoid blocking the event loop
    loop = asyncio.get_running_loop()
    ldoce_html = await loop.run_in_executor(None, _lookup_word, app.state.mdx_builder, word)
    if not ldoce_html:
        raise HTTPException(
            status_code=404, detail=f"not found: '{word}' is not in the dictionary"
        )

    pronunciation = _extract_reading(ldoce_html)

    # Build audio dict: map .spx filenames to .mp3 URLs served by /audio/{filename}
    audio_filenames = _extract_audio_filenames(ldoce_html)
    audio: dict = {}
    for region, spx_name in audio_filenames.items():
        if spx_name:
            mp3_name = spx_name[:-4] + ".mp3"
            audio[region] = {"filename": mp3_name, "url": f"/audio/{mp3_name}"}
        else:
            audio[region] = None

    senses = _extract_senses(ldoce_html)

    # AI sense disambiguation if sentence and LLM key are available
    context = sentence.strip() if sentence else None
    selected_idx = 0
    ai_warning: Optional[str] = None

    if context and LLM_API_KEY and len(senses) > 1:
        sense_idx = await _ai_pick_sense(word, context, senses, app.state.llm_client)
        if sense_idx is None:
            selected_idx = 0
            ai_warning = "AI unavailable — used default sense"
        else:
            selected_idx = sense_idx

    response_senses = [
        {
            "index": i,
            "pos": s["pos"],
            "sense_num": s["sense_num"],
            "definition": s["definition"],
            "definition_html": s["definition_html"],
            "example": s["example"],
            "gram": s["gram"],
            "freq": s["freq"],
            "ai_selected": i == selected_idx,
        }
        for i, s in enumerate(senses)
    ]

    return {
        "word": word,
        "pronunciation": pronunciation,
        "audio": audio,
        "senses": response_senses,
        "selected_sense_index": selected_idx,
        "warning": ai_warning,
    }


@app.get("/audio/{filename}")
async def get_audio(filename: str):
    """Return audio file as mp3. Accepts the .mp3 filename from /lookup audio URLs."""
    mdd_wrapper = getattr(app.state, "mdd_wrapper", None)
    if not mdd_wrapper:
        raise HTTPException(status_code=404, detail="audio not available: MDD not loaded")

    # Map .mp3 filename back to .spx for MDD lookup
    if filename.lower().endswith(".mp3"):
        spx_filename = filename[:-4] + ".spx"
    else:
        spx_filename = filename

    loop = asyncio.get_running_loop()
    spx_data = await loop.run_in_executor(None, mdd_wrapper.mdd_lookup, spx_filename)
    if spx_data is None:
        raise HTTPException(status_code=404, detail=f"audio not found: '{filename}'")

    mp3_data, _ = await loop.run_in_executor(None, _spx_to_mp3, spx_data)
    if mp3_data is None:
        raise HTTPException(status_code=500, detail="audio conversion failed")

    return Response(content=mp3_data, media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=API_HOST, port=API_PORT)
