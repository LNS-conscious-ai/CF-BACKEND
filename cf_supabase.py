import os
from datetime import datetime
from fastapi import APIRouter, Header
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://lpvpfwczaghiowdnzogm.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
router = APIRouter(prefix="/api", tags=["auth"])

# --- GUEST ---
@router.post("/auth/guest")
async def guest(req: dict):
    try:
        sid = req.get("session_id", "")
        existing = supabase.table("guest_sessions").select("*").eq("session_id", sid).execute()
        if not existing.data:
            supabase.table("guest_sessions").insert({"session_id": sid}).execute()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- SIGNUP ---
@router.post("/auth/signup")
async def signup(req: dict):
    try:
        res = supabase.auth.sign_up({
            "email": req["email"],
            "password": req["password"],
            "options": {"data": {"name": req.get("name", "")}}
        })
        return {"success": True, "user_id": str(res.user.id), "email": res.user.email}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- LOGIN ---
@router.post("/auth/login")
async def login(req: dict):
    try:
        res = supabase.auth.sign_in_with_password({
            "email": req["email"], "password": req["password"]
        })
        return {"success": True, "access_token": res.session.access_token, "user_id": str(res.user.id)}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- ME ---
@router.get("/auth/me")
async def me(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return {"authenticated": False}
    try:
        token = authorization.replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        if user.user:
            prof = supabase.table("profiles").select("name").eq("id", user.user.id).execute()
            name = prof.data[0]["name"] if prof.data else None
            return {"authenticated": True, "user_id": str(user.user.id), "email": user.user.email, "name": name}
    except: pass
    return {"authenticated": False}

# --- CONVERSATIONS ---
@router.post("/conversations")
async def create_conv(req: dict, authorization: str = Header(None)):
    token = authorization.replace("Bearer ", "") if authorization else ""
    try:
        user = supabase.auth.get_user(token)
        if not user.user: return {"success": False}
        res = supabase.table("conversations").insert({
            "user_id": str(user.user.id), "title": req.get("title", "Chat"), "intent": req.get("intent", "general")
        }).execute()
        return {"success": True, "conversation": res.data[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/conversations")
async def list_conv(authorization: str = Header(None)):
    token = authorization.replace("Bearer ", "") if authorization else ""
    try:
        user = supabase.auth.get_user(token)
        if not user.user: return {"success": False}
        res = supabase.table("conversations").select("*").eq("user_id", str(user.user.id)).order("updated_at", desc=True).execute()
        return {"success": True, "conversations": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/conversations/{conv_id}/messages")
async def get_msgs(conv_id: str):
    try:
        res = supabase.table("messages").select("*").eq("conversation_id", conv_id).order("created_at").execute()
        return {"success": True, "messages": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- MESSAGES ---
@router.post("/messages")
async def save_msg(req: dict, authorization: str = Header(None)):
    try:
        res = supabase.table("messages").insert({
            "conversation_id": req["conversation_id"], "role": req["role"],
            "content": req["content"], "persona": req.get("persona", "teacher")
        }).execute()
        return {"success": True, "message": res.data[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- GUEST MESSAGES ---
@router.post("/guest/messages")
async def save_guest(req: dict):
    try:
        sid = req.get("session_id", "")
        existing = supabase.table("guest_sessions").select("*").eq("session_id", sid).execute()
        if not existing.data:
            supabase.table("guest_sessions").insert({"session_id": sid}).execute()
        res = supabase.table("guest_messages").insert({
            "session_id": sid, "role": req["role"], "content": req["content"]
        }).execute()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/guest/messages/{sid}")
async def get_guest(sid: str):
    try:
        res = supabase.table("guest_messages").select("*").eq("session_id", sid).order("created_at").execute()
        return {"success": True, "messages": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}
