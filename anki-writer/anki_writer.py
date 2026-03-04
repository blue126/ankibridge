from dotenv import load_dotenv

load_dotenv()

import asyncio
import datetime
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COLLECTION_PATH = os.environ.get(
    "COLLECTION_PATH", "/opt/iphone-anki-sync/collection.anki2"
)
ANKI_SYNC_URL = os.environ.get("ANKI_SYNC_URL", "http://localhost:8080/")
ANKI_SYNC_USER = os.environ.get("ANKI_SYNC_USER", "anki")
ANKI_SYNC_PASSWORD = os.environ.get("ANKI_SYNC_PASSWORD", "anki")
LDOCE5_API_URL = os.environ.get("LDOCE5_API_URL", "http://localhost:5050")
DECK_NAME = os.environ.get("DECK_NAME", "ODH")
NOTE_TYPE_NAME = os.environ.get("NOTE_TYPE_NAME", "ODH")
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5051"))


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class WordRequest(BaseModel):
    word: str
    sentence: Optional[str] = Field(default=None, max_length=2000)
    sense_index: Optional[int] = None


class AddWordResponse(BaseModel):
    word: str
    sense_used: int
    definition: str
    note_id: int
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Anki collection helpers
# ---------------------------------------------------------------------------

_col_lock = threading.Lock()
_last_sync: Optional[datetime.datetime] = None


def _add_note_and_sync(
    word: str,
    pronunciation: str,
    definition: str,
    sentence: str,
    audio_filename: Optional[str],
    audio_data: Optional[bytes],
    deck_name: str,
    note_type_name: str,
) -> int:
    """Run in thread executor. Returns new note ID on success, raises on error.

    anki.Collection is not thread-safe; _col_lock serialises all collection
    access within this process (acceptable for a personal tool with ≤1 concurrent requests).
    """
    global _last_sync

    from anki.collection import Collection  # noqa: PLC0415 — not available on dev machine

    with _col_lock:
        col = Collection(COLLECTION_PATH)
        try:
            # Sync from server first so the local collection is up to date.
            auth = col.sync_login(ANKI_SYNC_USER, ANKI_SYNC_PASSWORD, ANKI_SYNC_URL)
            col.sync_collection(auth, sync_media=False)

            if audio_filename and audio_data:
                media_path = os.path.join(col.media.dir(), audio_filename)
                with open(media_path, "wb") as f:
                    f.write(audio_data)

            deck_id = col.decks.id(deck_name)
            notetype = col.models.by_name(note_type_name)
            if notetype is None:
                raise RuntimeError(
                    f"Note type '{note_type_name}' not found in collection"
                )

            note = col.new_note(notetype)
            note["word"] = word
            note["pronunciation"] = pronunciation
            note["definition"] = definition
            note["sentence"] = sentence
            note["audio"] = f"[sound:{audio_filename}]" if audio_filename else ""
            note["extrainfo"] = ""
            note["url"] = ""
            col.add_note(note, deck_id)
            col.save()

            col.sync_collection(auth, sync_media=True)

            _last_sync = datetime.datetime.utcnow()
            return note.id
        finally:
            col.close()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ldoce5_client = httpx.AsyncClient(
        base_url=LDOCE5_API_URL,
        timeout=15.0,
    )
    yield
    await app.state.ldoce5_client.aclose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Anki Writer Service", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/add-word", response_model=AddWordResponse)
async def add_word(req: WordRequest):
    """Look up word via ldoce5-api and write an Anki card, then sync to AnkiMobile."""
    word = req.word.strip().lower()
    if not word:
        raise HTTPException(status_code=422, detail="invalid: word cannot be empty")
    if len(word) > 100:
        raise HTTPException(
            status_code=422,
            detail="invalid: word exceeds maximum length of 100 characters",
        )

    # 1. Lookup via ldoce5-api
    try:
        params: dict = {"word": word}
        if req.sentence:
            params["sentence"] = req.sentence
        resp = await app.state.ldoce5_client.get("/lookup", params=params)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"not found: '{word}' is not in the dictionary",
            )
        raise HTTPException(status_code=502, detail="ldoce5-api returned an error")
    except Exception:
        raise HTTPException(status_code=502, detail="ldoce5-api is not reachable")

    lookup = resp.json()

    # 2. Sense selection: use caller-specified index, or AI-selected index from /lookup
    senses = lookup.get("senses") or []
    if not senses:
        raise HTTPException(status_code=502, detail="ldoce5-api returned no senses")
    sense_idx = req.sense_index if req.sense_index is not None else lookup["selected_sense_index"]
    sense_idx = max(0, min(sense_idx, len(senses) - 1))
    sense = senses[sense_idx]

    # 3. Fetch audio (UK preferred, fall back to US)
    audio_info = lookup.get("audio") or {}
    audio_url_rel = (audio_info.get("uk") or audio_info.get("us") or {}).get("url")
    audio_filename: Optional[str] = None
    audio_data: Optional[bytes] = None
    if audio_url_rel:
        try:
            audio_resp = await app.state.ldoce5_client.get(audio_url_rel)
            audio_resp.raise_for_status()
            audio_filename = audio_url_rel.rstrip("/").split("/")[-1]
            audio_data = audio_resp.content
        except Exception as exc:
            logger.warning("Failed to fetch audio from ldoce5-api: %s", type(exc).__name__)

    # 4. Build sentence field (user sentence + LDOCE example if available)
    if req.sentence:
        ldoce_ex = sense.get("example", "")
        sentence_field = req.sentence + ("\n\n— LDOCE: " + ldoce_ex if ldoce_ex else "")
    else:
        sentence_field = sense.get("example", "")

    # 5. Write to collection + sync (blocking; run in thread executor)
    loop = asyncio.get_running_loop()
    try:
        note_id = await loop.run_in_executor(
            None,
            _add_note_and_sync,
            word,
            lookup.get("pronunciation", ""),
            sense["definition_html"],
            sentence_field,
            audio_filename,
            audio_data,
            DECK_NAME,
            NOTE_TYPE_NAME,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("Failed to write note or sync: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502, detail="anki sync failed")

    return AddWordResponse(
        word=word,
        sense_used=sense_idx,
        definition=sense["definition_html"],
        note_id=note_id,
        warning=lookup.get("warning"),
    )


@app.get("/health")
async def health():
    """Return service health: collection accessibility + ldoce5-api reachability."""
    collection_accessible = os.path.exists(COLLECTION_PATH)

    ldoce5_api_reachable = False
    try:
        resp = await app.state.ldoce5_client.get("/health")
        ldoce5_api_reachable = resp.status_code == 200
    except Exception:
        pass

    last_sync_str = (
        _last_sync.strftime("%Y-%m-%dT%H:%M:%SZ") if _last_sync else None
    )
    return {
        "status": "ok",
        "collection_accessible": collection_accessible,
        "ldoce5_api_reachable": ldoce5_api_reachable,
        "last_sync": last_sync_str,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=API_HOST, port=API_PORT)
