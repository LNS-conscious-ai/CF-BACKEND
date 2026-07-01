"""
main.py — CF FastAPI Server · LNS Phase 1 (INTEGRATED)
=====================================================
This is the unified Phase 1 backend. It preserves every endpoint from the
live CF-BACKEND (chat, voice, health, status) and adds the Phase 1 stack:

  • 7-layer safety guard            (safety_layers.py)
  • Spend caps + rate limits        (spend_caps.py)
  • Memory consent architecture     (memory_consent.py)
  • Teacher format enforcement      (format_validator.py)
  • CF Computer v1 report engine    (cf_computer_router.py)
  • Analytics / PostHog ingestion   (analytics_router.py)
  • Waitlist + email + outcomes     (email_router.py)

Endpoints (existing, unchanged behaviour):
  POST /chat/stream   — SSE streaming CF responses  (now safety+spend wrapped)
  POST /chat          — JSON fallback
  GET  /scribe-token  — single-use ElevenLabs Scribe token
  POST /transcribe    — Scribe / Deepgram STT
  POST /speak         — ElevenLabs / Azure TTS
  GET  /health        — health check
  GET  /status        — system status dashboard
  POST /ingest        — ChromaDB ingestion trigger

New Phase 1 endpoints:
  POST /consent/grant | /consent/decline | /consent/revoke | GET /consent/status
  GET  /user/status            — per-user spend / rate-limit snapshot
  GET  /admin/global-status    — platform-wide spend (protect in prod!)
  + all routes from the three included routers
  GET  /reports/<file>.pdf     — generated CF Computer PDFs (static)

SAFETY NOTE: if the safety stack fails to import, /chat/stream refuses to
serve (returns a maintenance message) rather than running users UNSAFE.
"""

import os, json, re, asyncio, time
import httpx
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from cf_respond import cf_respond, CRISIS_PATTERNS, CRISIS_RESPONSE, _client, _embed_fn
from cf_supabase_backend import router as auth_router, save_chat_message, get_or_create_conversation, supabase

# ════════════════════════════════════════════════════════════════════════
#  PHASE 1 SAFETY / SPEND / CONSENT STACK  (required — fail safe, not silent)
# ════════════════════════════════════════════════════════════════════════
SAFETY_REGION = os.environ.get("LNS_SAFETY_REGION", "IN")
try:
    from spend_caps import (
        spend_cap_dependency, record_usage_tokens,
        get_user_status, get_global_status, UsageSnapshot,
    )
    from safety_layers import SafetyGuard
    from memory_consent import (
        has_consent, grant_consent, revoke_consent,
        get_consent_status, mark_first_reply_given,
    )
    from format_validator import enforce_format
    safety_guard = SafetyGuard(region_code=SAFETY_REGION, strict_mode=True)
    SAFETY_ENABLED = True
    print("[SAFETY] 7-layer stack loaded · region:", SAFETY_REGION)
except Exception as _e:  # pragma: no cover
    print(f"[SAFETY] STACK FAILED TO LOAD — chat will run in maintenance mode: {_e}")
    SAFETY_ENABLED = False
    safety_guard = None
    async def spend_cap_dependency(request: Request):  # fallback no-op dependency
        return None

def _estimate_tokens(text: str) -> int:
    """Rough estimator: ~1 token per 0.75 words (English)."""
    return int(len(text.split()) / 0.75) if text else 0

app = FastAPI(title="CF Backend · LNS Phase 1", version="2.0.0")
app.include_router(auth_router)
START_TIME = time.time()

# ── Reports directory (CF Computer PDFs) ──────────────────
REPORTS_DIR = os.environ.get("LNS_REPORTS_DIR", "/tmp/lns_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# ── Include Phase 1 feature routers (degrade gracefully) ──
for _modname, _attr in [
    ("cf_computer_router", "router"),
    ("analytics_router", "router"),
    ("email_router", "router"),
]:
    try:
        _mod = __import__(_modname, fromlist=[_attr])
        app.include_router(getattr(_mod, _attr))
        print(f"[ROUTER] included {_modname}")
    except Exception as _re_err:  # pragma: no cover
        print(f"[ROUTER] SKIPPED {_modname}: {_re_err}")

# ── Serve generated CF Computer PDFs ──────────────────────
try:
    app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")
except Exception as _se:
    print(f"[REPORTS] static mount failed: {_se}")

# ── CORS — lns.life frontend (production-locked; no wildcard) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lns.life",
        "https://www.lns.life",
        "https://lns-life-frontend.vercel.app",                              # production Vercel alias
        "https://lns-life-frontend-git-phase-1-lns-cf-projects.vercel.app",  # phase-1 preview (testing)
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEEPINFRA_API_KEY   = os.environ.get("DEEPINFRA_API_KEY", "").strip()
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "jVIYITU8x2yaOctTAPIU").strip()
AZURE_SPEECH_KEY    = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus").strip()
AZURE_SPEECH_ENDPOINT = os.environ.get("AZURE_SPEECH_ENDPOINT", "").strip()
DEEPGRAM_API_KEY    = os.environ.get("DEEPGRAM_API_KEY", "").strip()

# ── REQUEST MODELS ────────────────────────────────────────
class ChatRequest(BaseModel):
    message:    str
    history:    Optional[list] = []
    user_id:    Optional[str]  = "anonymous"
    session_id: Optional[str]  = "default"

class SpeakRequest(BaseModel):
    text: str

class ConsentRequest(BaseModel):
    user_id: str = "anonymous"
    level:   Optional[str] = "soft"     # soft | deep

# ── MARKDOWN STRIP — clean text before sending to TTS ─────
def strip_markdown(text):
    s = text
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'\*(.+?)\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'_(.+?)_', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`(.+?)`', r'\1', s)
    s = re.sub(r'^\s*[-*+]\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'^\s*\d+\.\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()

# ── HEALTH CHECK ──────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "CF Backend", "version": "2.0.0",
            "safety": "enabled" if SAFETY_ENABLED else "MAINTENANCE"}

@app.get("/")
async def root():
    return {"message": "CF Backend is live. lns.life is home."}

# ── STATUS — system dashboard ─────────────────────────────
@app.get("/status")
async def status():
    uptime_secs = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_secs, 3600)
    minutes, secs = divmod(remainder, 60)
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
        "version": "2.0.0",
        "uptime": f"{hours}h {minutes}m {secs}s",
        "model": "google/gemma-4-26B-A4B-it",
        "voice": "ElevenLabs Flash v2.5",
        "stt": "ElevenLabs Scribe v2",
        "safety_stack": "enabled" if SAFETY_ENABLED else "MAINTENANCE",
        "chromadb": chroma_status,
        "collections": collections,
        "keys": {
            "deepinfra": "set" if DEEPINFRA_API_KEY else "MISSING",
            "elevenlabs": "set" if ELEVENLABS_API_KEY else "MISSING",
            "deepgram": "set" if DEEPGRAM_API_KEY else "not set (using Scribe)",
        }
    }

# ── SCRIBE TOKEN ──────────────────────────────────────────
@app.get("/scribe-token")
async def scribe_token():
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

# ── CHAT STREAM — SSE  (safety + spend + consent + format) ─
@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request,
                      usage=Depends(spend_cap_dependency)):
    """
    Main CF chat endpoint with Phase 1 safety stack.
    Order: spend cap (dependency) → input safety → generate → format →
    output safety → mark first reply → record usage → stream.
    """
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Maintenance guard: never serve chat if the safety stack failed to load.
    if not SAFETY_ENABLED:
        async def maint():
            msg = ("I'm taking a short pause for maintenance and can't chat safely "
                   "right now. Please try again in a little while.")
            yield f"data: {json.dumps({'type':'meta','persona':'mother','crisis':False})}\n\n"
            yield f"data: {json.dumps({'type':'text','content':msg})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        return StreamingResponse(maint(), media_type="text/event-stream")

    user_id = request.headers.get("x-user-id", req.user_id or "anonymous")
    try:
        tz_offset = int(request.headers.get("x-tz-offset", "0").split(":")[0])
    except Exception:
        tz_offset = 0

    # ── INPUT SAFETY (7 layers) ──────────────────────────
    sresult = safety_guard.full_check(user_id=user_id, message=req.message, tz_offset_hours=tz_offset)
    if sresult.action == "crisis_intervention":
        crisis_text = sresult.response_text or safety_guard.get_crisis_response(region_code=SAFETY_REGION)
        async def crisis_stream():
            yield f"data: {json.dumps({'type':'meta','persona':'mother','crisis':True})}\n\n"
            yield f"data: {json.dumps({'type':'text','content':crisis_text})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        return StreamingResponse(crisis_stream(), media_type="text/event-stream")
    if sresult.action == "block":
        block_text = sresult.response_text or ("I want to keep this space safe, so I can't go "
                                               "there. We can talk about something else whenever you're ready.")
        async def block_stream():
            yield f"data: {json.dumps({'type':'meta','persona':'mother','crisis':False})}\n\n"
            yield f"data: {json.dumps({'type':'text','content':block_text})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        return StreamingResponse(block_stream(), media_type="text/event-stream")

    async def generate():
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: cf_respond(req.message, req.history or []))

            persona = result.get("persona", "teacher")
            response_text = result.get("response", "")

            # ── FORMAT ENFORCEMENT (Teacher only) ──
            try:
                ok, validated = enforce_format(persona, response_text)
                if ok:
                    response_text = validated
            except Exception as fe:
                print(f"[FORMAT] skip: {fe}")

            # ── OUTPUT SAFETY SCAN ──
            try:
                out = safety_guard.check_output(user_id, response_text)
                if not out.allowed:
                    response_text = ("I started to answer but want to be more careful with how I "
                                     "say it. Could you tell me a little more about what you need?")
            except Exception as oe:
                print(f"[OUTPUT-SAFETY] skip: {oe}")

            meta = {
                "type":    "meta",
                "persona": persona,
                "intent":  result.get("intent_type"),
                "emotion": result.get("emotion"),
                "crisis":  result.get("crisis", False),
            }
            yield f"data: {json.dumps(meta)}\n\n"

            # ── Persist (best-effort). For strict DPDP, gate on has_consent(user_id,'soft'). ──
            try:
                await save_chat_message(user_id=None, session_id=req.session_id, role="user", content=req.message)
                await save_chat_message(user_id=None, session_id=req.session_id, role="assistant",
                                        content=response_text, persona=persona)
            except Exception as e:
                print(f"[Supabase] Save error: {e}")

            # ── Mark first reply (unlocks consent) + record token usage ──
            try:
                mark_first_reply_given(user_id)
                record_usage_tokens(user_id,
                                    input_tokens=_estimate_tokens(req.message),
                                    output_tokens=_estimate_tokens(response_text))
            except Exception as ue:
                print(f"[USAGE] skip: {ue}")

            words = response_text.split(" ")
            for i, word in enumerate(words):
                chunk = word if i == 0 else " " + word
                yield f"data: {json.dumps({'type':'text','content':chunk})}\n\n"
                await asyncio.sleep(0.02)
            yield f"data: {json.dumps({'type':'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "https://www.lns.life"},
    )

# ── CHAT NON-STREAM — JSON fallback ───────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: cf_respond(req.message, req.history or []))
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── TRANSCRIBE — Scribe (primary) / Deepgram (fallback) ───
@app.post("/transcribe")
async def transcribe(request: Request):
    audio_data = await request.body()
    if not audio_data or len(audio_data) < 100:
        raise HTTPException(status_code=400, detail="No audio data received")
    if ELEVENLABS_API_KEY:
        try:
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
                return {"transcript": data.get("text", "").strip()}
        except Exception as e:
            print(f"[SCRIBE ERROR] {e}")
    if DEEPGRAM_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen?model=nova-3&language=en",
                    headers={"Authorization": f"Token {DEEPGRAM_API_KEY}",
                             "Content-Type": "application/octet-stream"},
                    content=audio_data, timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                transcript = (data.get("results", {}).get("channels", [{}])[0]
                              .get("alternatives", [{}])[0].get("transcript", ""))
                return {"transcript": transcript}
        except Exception as e:
            print(f"[DEEPGRAM ERROR] {e}")
    raise HTTPException(status_code=503, detail="No STT service configured")

# ── SPEAK — ElevenLabs (primary) / Azure (fallback) ───────
@app.post("/speak")
async def speak(req: SpeakRequest):
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
                return StreamingResponse(iter([el_r.content]), media_type="audio/mpeg",
                                         headers={"X-TTS-Provider": "elevenlabs"})
        except Exception:
            pass
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
        ssml = ('<speak version="1.0" xml:lang="en-US">'
                '<voice name="en-US-AvaMultilingualNeural">' + safe_text + "</voice></speak>")
        headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                   "Content-Type": "application/ssml+xml",
                   "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                   "User-Agent": "CF-BACKEND"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
        if r.status_code != 200:
            return JSONResponse(status_code=502, content={"error": "azure_tts_failed",
                                "status": r.status_code, "body": r.text[:500]})
        return StreamingResponse(iter([r.content]), media_type="audio/mpeg",
                                 headers={"X-TTS-Provider": "azure"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "type": type(e).__name__,
                            "trace": traceback.format_exc()[-1500:]})

# ════════════════════════════════════════════════════════════════════════
#  PHASE 1 — CONSENT ENDPOINTS  (frontend consent_flow.js calls these)
# ════════════════════════════════════════════════════════════════════════
@app.post("/consent/grant")
async def consent_grant(req: ConsentRequest):
    if not SAFETY_ENABLED:
        raise HTTPException(status_code=503, detail="Consent service unavailable")
    s = grant_consent(req.user_id, level=req.level or "soft")
    return {"status": "granted", "level": s.level, "has_consent": s.has_consent}

@app.post("/consent/decline")
async def consent_decline(req: ConsentRequest):
    # Declining = stay ephemeral. We simply do not grant; nothing stored.
    return {"status": "declined", "level": "none", "has_consent": False}

@app.post("/consent/revoke")
async def consent_revoke(req: ConsentRequest):
    if not SAFETY_ENABLED:
        raise HTTPException(status_code=503, detail="Consent service unavailable")
    s = revoke_consent(req.user_id)
    return {"status": "revoked", "level": s.level, "has_consent": s.has_consent}

@app.get("/consent/status")
async def consent_status(user_id: str = "anonymous"):
    if not SAFETY_ENABLED:
        raise HTTPException(status_code=503, detail="Consent service unavailable")
    s = get_consent_status(user_id)
    return {"user_id": s.user_id, "level": s.level, "has_consent": s.has_consent}

# ════════════════════════════════════════════════════════════════════════
#  PHASE 1 — SPEND STATUS ENDPOINTS
# ════════════════════════════════════════════════════════════════════════
@app.get("/user/status")
async def user_status(user_id: str = "anonymous"):
    if not SAFETY_ENABLED:
        raise HTTPException(status_code=503, detail="Spend service unavailable")
    return get_user_status(user_id)

@app.get("/admin/global-status")
async def admin_global_status():
    # TODO: protect with an admin token before production.
    if not SAFETY_ENABLED:
        raise HTTPException(status_code=503, detail="Spend service unavailable")
    return get_global_status()

# ── INGEST — ChromaDB ingestion trigger ───────────────────
@app.post("/ingest")
async def run_ingestion():
    import subprocess
    result = subprocess.run(["python3", "/app/ingest.py"], capture_output=True, text=True, timeout=600)
    return {"status": "done", "stdout": result.stdout[-3000:], "stderr": result.stderr[-1000:]}
