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
from cf_respond import cf_respond, CRISIS_PATTERNS, CRISIS_RESPONSE, _client, _embed_fn
from cf_supabase_backend import router as auth_router, save_chat_message, get_or_create_conversation, supabase

app = FastAPI(title="CF Backend · LNS", version="1.1.0")
app.include_router(auth_router)
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

DEEPINFRA_API_KEY   = os.environ.get("DEEPINFRA_API_KEY", "").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "jVIYITU8x2yaOctTAPIU").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
AZURE_SPEECH_ENDPOINT = os.environ.get("AZURE_SPEECH_ENDPOINT", "").strip().replace(chr(10),"").replace(chr(13),"")
DEEPGRAM_API_KEY    = os.environ.get("DEEPGRAM_API_KEY", "").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")

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
    return s.strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")

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
    chroma_status = 'unknown'
    try:
        collections = {}
        for cname in ['foundational_books', 'meaning_first_startups', 'live_courses']:
            try:
                collections[cname] = _client.get_collection(cname, embedding_function=_embed_fn).count()
            except Exception as ce:
                collections[cname] = f'error: {str(ce)[:60]}'
        chroma_status = 'connected'
    except Exception as e:
        collections = {'_error': f'{str(e)[:120]}'}
        chroma_status = f'error: {str(e)[:80]}'
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
            if resp.status_code != 200:
                print(f"[SCRIBE] ElevenLabs error {resp.status_code}: {resp.text[:300]}")
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
    if not req.message or not req.message.strip().replace(chr(10),"").replace(chr(13),"").replace(" ",""):
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

            # ─── Save messages to Supabase (best-effort) ───
            try:
                await save_chat_message(
                    user_id=None,
                    session_id=req.session_id,
                    role="user",
                    content=req.message
                )
                await save_chat_message(
                    user_id=None,
                    session_id=req.session_id,
                    role="assistant",
                    content=result.get("response", ""),
                    persona=result.get("persona", "teacher")
                )
            except Exception as e:
                print(f"[Supabase] Save error: {e}")

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
    if not req.message or not req.message.strip().replace(chr(10),"").replace(chr(13),"").replace(" ",""):
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
                print(f"[SCRIBE] Response {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                transcript = data.get("text", "").strip().replace(chr(10),"").replace(chr(13),"").replace(" ","")
                return {"transcript": transcript}
        except Exception as e:
            print(f"[SCRIBE] Exception: {type(e).__name__}: {e}")
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
    # ---- ELEVENLABS PRIMARY (founder cloned voice) ----
    if ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID:
        try:
            el_url = "https://api.elevenlabs.io/v1/text-to-speech/" + ELEVENLABS_VOICE_ID
            async with httpx.AsyncClient(timeout=20.0) as el_client:
                el_r = await el_client.post(
                    el_url,
                    headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
                    json={"text": req.text, "model_id": "eleven_flash_v2_5"},
                )
            if el_r.status_code == 200 and el_r.content:
                return StreamingResponse(
                    iter([el_r.content]),
                    media_type="audio/mpeg",
                    headers={"X-TTS-Provider": "elevenlabs"},
                )
        except Exception:
            pass  # fall through to Azure fallback
    # ---- AZURE FALLBACK (proven path, unchanged) ----

    import traceback
    try:
        text = (req.text or "").strip()
        if not text:
            return JSONResponse(status_code=400, content={"error": "empty text"})

        if not AZURE_SPEECH_KEY:
            return JSONResponse(status_code=500, content={"error": "AZURE_SPEECH_KEY not set"})

        region = AZURE_SPEECH_REGION or "eastus"
        url = "https://" + region + ".tts.speech.microsoft.com/cognitiveservices/v1"

        safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ssml = (
            "<speak version=\"1.0\" xml:lang=\"en-US\">"
            "<voice name=\"en-US-AvaMultilingualNeural\">"
            + safe_text +
            "</voice></speak>"
        )

        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "CF-BACKEND",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, headers=headers, content=ssml.encode("utf-8"))

        if r.status_code != 200:
            return JSONResponse(
                status_code=502,
                content={"error": "azure_tts_failed", "status": r.status_code, "body": r.text[:500]},
            )

        return StreamingResponse(
            iter([r.content]),
            media_type="audio/mpeg",
            headers={"X-TTS-Provider": "azure"},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "type": type(e).__name__, "trace": traceback.format_exc()[-1500:]},
        )

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
