"""
main.py — CF FastAPI Server · Day 10
Serves lns.life frontend · RUNTIME.md v1.1
Endpoints:
  POST /chat/stream  — SSE streaming CF responses
  POST /chat         — JSON fallback
  GET  /scribe-token — single-use ElevenLabs Scribe token (for frontend WebSocket STT)
  POST /transcribe   — ElevenLabs Scribe REST fallback
  POST /speak        — ElevenLabs voice output (with markdown strip)
  GET  /health       — Railway health check
  GET  /status       — system status dashboard
"""

import os, json, re, asyncio, time
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from pydantic import BaseModel
from typing import Optional
from cf_respond import cf_respond, CRISIS_PATTERNS, CRISIS_RESPONSE

app = FastAPI(title="CF Backend · LNS", version="1.1.0")
START_TIME = time.time()

# ── CORS — lns.life frontend ──────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.lns.life",
        "https://lns.life",
        "https://lns-life-frontend.vercel.app",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPINFRA_API_KEY   = os.environ.get("DEEPINFRA_API_KEY", "")
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "jVIYITU8x2yaOctTAPIU")
DEEPGRAM_API_KEY    = os.environ.get("DEEPGRAM_API_KEY", "")
ELEVENLABS_STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
ELEVENLABS_TTS_MODEL = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")

# ── REQUEST MODELS ────────────────────────────────────────
class ChatRequest(BaseModel):
    message:    str
    history:    Optional[list] = []
    user_id:    Optional[str]  = "anonymous"
    session_id: Optional[str]  = "default"

class SpeakRequest(BaseModel):
    text: str

# ── MARKDOWN STRIP — clean text before sending to TTS ─────
def strip_markdown(text):
    """Remove markdown formatting so ElevenLabs doesn't speak asterisks, hashes, etc."""
    s = text
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)   # ## headers
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)                  # **bold**
    s = re.sub(r'\*(.+?)\*', r'\1', s)                       # *italic*
    s = re.sub(r'__(.+?)__', r'\1', s)                       # __bold__
    s = re.sub(r'_(.+?)_', r'\1', s)                         # _italic_
    s = re.sub(r'~~(.+?)~~', r'\1', s)                       # ~~strike~~
    s = re.sub(r'`(.+?)`', r'\1', s)                         # `code`
    s = re.sub(r'^\s*[-*+]\s+', '', s, flags=re.MULTILINE)   # - bullet
    s = re.sub(r'^\s*\d+\.\s+', '', s, flags=re.MULTILINE)   # 1. numbered
    s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)          # [link](url)
    s = re.sub(r'\n{3,}', '\n\n', s)                          # excess newlines
    return s.strip()

# ── HEALTH CHECK ──────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "CF Backend", "version": "1.1.0"}

@app.get("/")
async def root():
    return {"message": "CF Backend is live. lns.life is home."}

# ── STATUS — system dashboard ─────────────────────────────
@app.get("/status")
async def status():
    """System status for monitoring."""
    uptime_secs = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_secs, 3600)
    minutes, secs = divmod(remainder, 60)

    # Check ChromaDB
    chroma_status = "unknown"
    try:
        import chromadb
        base_dir = os.environ.get("APP_DIR", "/app")
        chroma_path = os.environ.get("CHROMADB_PATH", os.path.join(base_dir, "chromadb"))
        client = chromadb.PersistentClient(path=chroma_path)
        collections = {c.name: c.count() for c in client.list_collections()}
        chroma_status = "connected"
    except Exception as e:
        collections = {}
        chroma_status = f"error: {str(e)[:80]}"

    return {
        "status": "ok",
        "version": "1.1.0",
        "uptime": f"{hours}h {minutes}m {secs}s",
        "model": "google/gemma-4-26B-A4B-it",
        "voice": "ElevenLabs Flash v2.5",
        "stt": "ElevenLabs Scribe v2",
        "chromadb": chroma_status,
        "collections": collections,
        "keys": {
            "deepinfra": "set" if DEEPINFRA_API_KEY else "MISSING",
            "elevenlabs": "set" if ELEVENLABS_API_KEY else "MISSING",
            "deepgram": "set" if DEEPGRAM_API_KEY else "not set (using Scribe)",
        }
    }

# ── SCRIBE TOKEN — single-use token for frontend WebSocket STT ──
@app.get("/scribe-token")
async def scribe_token():
    """
    Returns a single-use token for ElevenLabs Scribe realtime WebSocket.
    Frontend connects directly to wss://api.elevenlabs.io for STT.
    API key never exposed to client.
    """
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="ElevenLabs not configured")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {"token": data.get("token", "")}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"ElevenLabs token error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token error: {str(e)[:100]}")

# ── CHAT STREAM — SSE ─────────────────────────────────────
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    Main CF chat endpoint.
    Streams response as Server-Sent Events (SSE).
    lns.life frontend connects here.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    async def generate():
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: cf_respond(req.message, req.history or [])
            )

            meta = {
                "type":     "meta",
                "persona":  result["persona"],
                "intent":   result["intent_type"],
                "emotion":  result["emotion"],
                "crisis":   result["crisis"],
            }
            yield f"data: {json.dumps(meta)}\n\n"

            words = result["response"].split(" ")
            for i, word in enumerate(words):
                chunk = word if i == 0 else " " + word
                payload = {"type": "text", "content": chunk}
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.02)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "https://www.lns.life",
        }
    )

# ── CHAT NON-STREAM — simple JSON fallback ────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    """Simple JSON response — fallback if frontend needs it."""
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cf_respond(req.message, req.history or [])
        )
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── TRANSCRIBE — ElevenLabs Scribe voice input ──
def detect_audio_type(audio_data: bytes, content_type: str = ""):
    """
    Detect audio type from browser upload or raw audio body.
    Supports webm, mp4/m4a, wav, mp3, ogg.
    """
    ct = (content_type or "").split(";")[0].strip().lower()

    if ct in ("audio/webm", "video/webm"):
        return "audio/webm", "webm"
    if ct in ("audio/mp4", "video/mp4", "audio/m4a", "audio/x-m4a"):
        return "audio/mp4", "mp4"
    if ct in ("audio/wav", "audio/wave", "audio/x-wav"):
        return "audio/wav", "wav"
    if ct in ("audio/mpeg", "audio/mp3"):
        return "audio/mpeg", "mp3"
    if ct in ("audio/ogg", "application/ogg"):
        return "audio/ogg", "ogg"

    if audio_data[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm", "webm"
    if len(audio_data) > 8 and audio_data[4:8] == b"ftyp":
        return "audio/mp4", "mp4"
    if audio_data[:4] == b"RIFF":
        return "audio/wav", "wav"
    if audio_data[:3] == b"ID3" or (len(audio_data) > 1 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0):
        return "audio/mpeg", "mp3"
    if audio_data[:4] == b"OggS":
        return "audio/ogg", "ogg"

    return "application/octet-stream", "bin"


@app.post("/transcribe")
async def transcribe(request: Request, file: Optional[UploadFile] = File(None)):
    """
    Accepts user voice and returns text using ElevenLabs Scribe.
    Works with frontend FormData file upload and raw audio body.
    """
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not configured")

    audio_data = b""
    filename = "audio.bin"
    content_type = ""

    if file is not None:
        audio_data = await file.read()
        filename = file.filename or filename
        content_type = file.content_type or ""
    else:
        audio_data = await request.body()
        content_type = request.headers.get("content-type", "")

    if not audio_data or len(audio_data) < 100:
        raise HTTPException(status_code=400, detail="No audio data received")

    mime, ext = detect_audio_type(audio_data, content_type)

    if filename == "audio.bin":
        filename = f"audio.{ext}"

    print(f"[TRANSCRIBE] bytes={len(audio_data)} mime={mime} filename={filename}")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": ELEVENLABS_API_KEY},
                files={"file": (filename, audio_data, mime)},
                data={
                    "model_id": ELEVENLABS_STT_MODEL,
                },
            )

            if resp.status_code >= 400:
                print(f"[SCRIBE ERROR BODY] {resp.text[:500]}")

            resp.raise_for_status()
            data = resp.json()
            transcript = data.get("text", "").strip()

            return {
                "transcript": transcript,
                "provider": "elevenlabs",
                "mime": mime,
                "bytes": len(audio_data),
            }

    except Exception as e:
        print(f"[SCRIBE ERROR] {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)[:200]}")


# ── SPEAK — ElevenLabs voice output ─
@app.post("/speak")
async def speak(req: SpeakRequest):
    """
    Converts CF text response to MP3 audio.
    Safer version: returns completed audio bytes for browser playback.
    """
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not configured")

    if not ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=503, detail="ELEVENLABS_VOICE_ID not configured")

    clean_text = strip_markdown(req.text)
    if not clean_text:
        raise HTTPException(status_code=400, detail="No text to speak")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                params={
                    "output_format": "mp3_44100_128",
                },
                json={
                    "text": clean_text,
                    "model_id": ELEVENLABS_TTS_MODEL,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
            )

            if resp.status_code >= 400:
                print(f"[TTS ERROR BODY] {resp.text[:500]}")

            resp.raise_for_status()

            return Response(
                content=resp.content,
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "no-store",
                    "Content-Disposition": 'inline; filename="cf_voice.mp3"',
                },
            )

    except Exception as e:
        print(f"[TTS ERROR] {repr(e)}")
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)[:200]}")


@app.post("/ingest")
async def run_ingestion():
    """Trigger ChromaDB ingestion on Railway volume."""
    import subprocess
    result = subprocess.run(
        ["python3", "/app/ingest.py"],
        capture_output=True, text=True, timeout=600
    )
    return {
        "status": "done",
        "stdout": result.stdout[-3000:],
        "stderr": result.stderr[-1000:]
    }
