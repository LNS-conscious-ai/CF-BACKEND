# CF Supabase Backend Module
# Add this file to your FastAPI project (e.g., cf_supabase.py)
# Then import and add routers in your main.py

import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

# ─── CONFIG ───────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://lpvpfwczaghiowdnzogm.supabase.co")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

router = APIRouter(prefix="/api", tags=["auth"])

# ─── PYDANTIC MODELS ──────────────────────────────────────
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GuestRequest(BaseModel):
    session_id: str

class ConversationCreate(BaseModel):
    title: Optional[str] = "New Conversation"
    intent: Optional[str] = "general"

class MessageCreate(BaseModel):
    conversation_id: str
    role: str  # user | assistant
    content: str
    persona: Optional[str] = "teacher"

class GuestMessageCreate(BaseModel):
    session_id: str
    role: str
    content: str

# ─── AUTH ENDPOINTS ───────────────────────────────────────
@router.post("/auth/signup")
async def signup(req: SignupRequest):
    """Register new user with email/password. Auto-confirms email + auto-login."""
    try:
        res = supabase.auth.sign_up({
            "email": req.email,
            "password": req.password,
            "options": {"data": {"name": req.name or ""}}
        })
        if res.user:
            # Auto-confirm email so user can login without verifying
            try:
                supabase.auth.admin.update_user_by_id(
                    res.user.id,
                    {"email_confirm": True}
                )
            except Exception as confirm_err:
                print(f"[Auto-confirm] Warning: {confirm_err}")

            # Auto-login immediately, return token to frontend
            try:
                login_res = supabase.auth.sign_in_with_password({
                    "email": req.email,
                    "password": req.password
                })
                if login_res.session:
                    return {
                        "success": True,
                        "user_id": str(res.user.id),
                        "email": res.user.email,
                        "access_token": login_res.session.access_token,
                        "refresh_token": login_res.session.refresh_token
                    }
            except Exception as login_err:
                print(f"[Auto-login] Warning: {login_err}")

            return {"success": True, "user_id": str(res.user.id), "email": res.user.email}
        return {"success": False, "error": "Signup failed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/auth/login")
async def login(req: LoginRequest):
    """Login user with email/password."""
    try:
        res = supabase.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password
        })
        if res.session:
            return {
                "success": True,
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user_id": str(res.user.id),
                "email": res.user.email
            }
        return {"success": False, "error": "Invalid credentials"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/auth/guest")
async def create_guest(req: GuestRequest):
    """Create or update a guest session."""
    try:
        # Check if session exists
        existing = supabase.table("guest_sessions").select("*").eq("session_id", req.session_id).execute()
        if existing.data:
            # Update last_active
            supabase.table("guest_sessions").update({"last_active": datetime.utcnow().isoformat()}).eq("session_id", req.session_id).execute()
            return {"success": True, "session_id": req.session_id, "created": False}
        
        # Create new guest session
        supabase.table("guest_sessions").insert({"session_id": req.session_id}).execute()
        return {"success": True, "session_id": req.session_id, "created": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    """Get current user from JWT token."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"authenticated": False}
    try:
        token = authorization.replace("Bearer ", "")
        res = supabase.auth.get_user(token)
        if res.user:
            # Get profile
            profile = supabase.table("profiles").select("*").eq("id", res.user.id).execute()
            return {
                "authenticated": True,
                "user_id": str(res.user.id),
                "email": res.user.email,
                "name": profile.data[0].get("name") if profile.data else None
            }
        return {"authenticated": False}
    except Exception as e:
        return {"authenticated": False, "error": str(e)}

# ─── CONVERSATION ENDPOINTS ───────────────────────────────
@router.post("/conversations")
async def create_conversation(req: ConversationCreate, authorization: Optional[str] = Header(None)):
    """Create a new conversation for logged-in user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        token = authorization.replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        if not user.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        res = supabase.table("conversations").insert({
            "user_id": str(user.user.id),
            "title": req.title,
            "intent": req.intent
        }).execute()
        
        return {"success": True, "conversation": res.data[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/conversations")
async def list_conversations(authorization: Optional[str] = Header(None)):
    """List all conversations for logged-in user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        token = authorization.replace("Bearer ", "")
        user = supabase.auth.get_user(token)
        if not user.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        res = supabase.table("conversations").select("*").eq("user_id", str(user.user.id)).order("updated_at", desc=True).execute()
        return {"success": True, "conversations": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, authorization: Optional[str] = Header(None)):
    """Get all messages for a conversation."""
    try:
        res = supabase.table("messages").select("*").eq("conversation_id", conversation_id).order("created_at").execute()
        return {"success": True, "messages": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/messages")
async def save_message(req: MessageCreate, authorization: Optional[str] = Header(None)):
    """Save a message to a conversation."""
    try:
        res = supabase.table("messages").insert({
            "conversation_id": req.conversation_id,
            "role": req.role,
            "content": req.content,
            "persona": req.persona
        }).execute()
        return {"success": True, "message": res.data[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ─── GUEST MESSAGE ENDPOINTS ──────────────────────────────
@router.post("/guest/messages")
async def save_guest_message(req: GuestMessageCreate):
    """Save a message for a guest session."""
    try:
        # Ensure guest session exists
        existing = supabase.table("guest_sessions").select("*").eq("session_id", req.session_id).execute()
        if not existing.data:
            supabase.table("guest_sessions").insert({"session_id": req.session_id}).execute()
        
        res = supabase.table("guest_messages").insert({
            "session_id": req.session_id,
            "role": req.role,
            "content": req.content
        }).execute()
        return {"success": True, "message": res.data[0]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/guest/messages/{session_id}")
async def get_guest_messages(session_id: str):
    """Get all messages for a guest session."""
    try:
        res = supabase.table("guest_messages").select("*").eq("session_id", session_id).order("created_at").execute()
        return {"success": True, "messages": res.data}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ─── FUNCTIONS FOR USE IN CHAT STREAMING ─────────────────
async def save_chat_message(user_id: Optional[str], session_id: str, role: str, content: str, 
                             conversation_id: Optional[str] = None, persona: str = "teacher"):
    """Save a chat message (used internally from /chat/stream)."""
    try:
        if user_id and conversation_id:
            # Logged-in user with conversation
            supabase.table("messages").insert({
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "persona": persona
            }).execute()
        else:
            # Guest user
            # Ensure guest session exists
            existing = supabase.table("guest_sessions").select("*").eq("session_id", session_id).execute()
            if not existing.data:
                supabase.table("guest_sessions").insert({"session_id": session_id}).execute()
            
            supabase.table("guest_messages").insert({
                "session_id": session_id,
                "role": role,
                "content": content
            }).execute()
    except Exception as e:
        print(f"[Supabase] Save message error: {e}")

async def get_or_create_conversation(user_id: str, intent: str = "general") -> str:
    """Get most recent conversation or create new one."""
    try:
        # Get latest conversation
        res = supabase.table("conversations").select("*").eq("user_id", user_id).eq("intent", intent).order("updated_at", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]["id"]
        
        # Create new conversation
        new_conv = supabase.table("conversations").insert({
            "user_id": user_id,
            "title": f"Conversation {datetime.now().strftime('%b %d')}",
            "intent": intent
        }).execute()
        return new_conv.data[0]["id"]
    except Exception as e:
        print(f"[Supabase] Get/create conversation error: {e}")
        return ""
