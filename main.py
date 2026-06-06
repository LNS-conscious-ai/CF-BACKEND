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
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
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
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "sk_6de4853266ca051224d09ef4f0d12421a219153c0c926ec0")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "jVIYITU8x2yaOctTAPIU")
DEEPGRAM_API_KEY    = os.environ.get("DEEPGRAM_API_KEY", "")

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

# ── TRANSCRIBE — ElevenLabs Scribe REST (fallback for non-WebSocket clients) ──
@app.post("/transcribe")
async def transcribe(request: Request):
    """
    Accepts audio file, returns transcribed text.
    Uses ElevenLabs Scribe v2 (primary) or Deepgram Nova-3 (fallback).
    """
    audio_data = await request.body()
    if not audio_data or len(audio_data) < 100:
        raise HTTPException(status_code=400, detail="No audio data received")

    # Primary: ElevenLabs Scribe v2
    if ELEVENLABS_API_KEY:
        try:
            # Auto-detect audio format from magic bytes (not Content-Type header)
            if audio_data[:4] == b'\x1aE\xdf\xa3':
                mime, ext = "audio/webm", "webm"
            elif audio_data[4:8] == b'ftyp':
                mime, ext = "audio/mp4", "mp4"
            elif audio_data[:4] == b'RIFF':
                mime, ext = "audio/wav", "wav"
            elif audio_data[:3] == b'ID3' or (len(audio_data) > 1 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0):
                mime, ext = "audio/mpeg", "mp3"
            elif audio_data[:4] == b'OggS':
                mime, ext = "audio/ogg", "ogg"
            else:
                mime, ext = "application/octet-stream", "bin"

            print(f"[SCRIBE] Received {len(audio_data)} bytes, detected format: {mime}")

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.elevenlabs.io/v1/speech-to-text",
                    headers={"xi-api-key": ELEVENLABS_API_KEY},
                    files={"file": (f"audio.{ext}", audio_data, mime)},
                    data={"model_id": "scribe_v2", "language_code": "en"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                transcript = data.get("text", "").strip()
                return {"transcript": transcript}
        except Exception as e:
            print(f"[SCRIBE ERROR] {e}")
            # Fall through to Deepgram if available

    # Fallback: Deepgram Nova-3
    if DEEPGRAM_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen?model=nova-3&language=en",
                    headers={
                        "Authorization": f"Token {DEEPGRAM_API_KEY}",
                        "Content-Type":  "application/octet-stream",
                    },
                    content=audio_data,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                transcript = (
                    data.get("results", {})
                        .get("channels", [{}])[0]
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                )
                return {"transcript": transcript}
        except Exception as e:
            print(f"[DEEPGRAM ERROR] {e}")

    raise HTTPException(status_code=503, detail="No STT service configured")

# ── SPEAK — ElevenLabs voice output (with markdown strip) ─
@app.post("/speak")
async def speak(req: SpeakRequest):
    """
    Converts CF text response to audio.
    Uses ElevenLabs Flash v2.5.
    Strips markdown before sending to TTS.
    """
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=503, detail="ElevenLabs not configured")

    # Strip markdown so TTS doesn't speak asterisks/hashes
    clean_text = strip_markdown(req.text)
    if not clean_text:
        raise HTTPException(status_code=400, detail="No text to speak")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream",
                headers={
                    "xi-api-key":   ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text":        clean_text,
                    "model_id":    "eleven_flash_v2_5",
                    "voice_settings": {
                        "stability":        0.5,
                        "similarity_boost": 0.75,
                    },
                },
                timeout=60,
            )
            resp.raise_for_status()
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type="audio/mpeg"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
