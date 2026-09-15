import uvicorn
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from backend import CoreBrain

import secrets
import os
import base64
import re
import uuid
import threading
import multiprocessing as mp
import tempfile
import json
import numpy as np
from datetime import datetime

app = FastAPI(title="CodeChat Team Server")

brain = CoreBrain()

# ============================================================
# TEAM / AUTH STATE
# ============================================================

ACCESS_TOKENS = {}
USER_SESSIONS = {}
PENDING_INVITES = {}
ACTIVE_USERS = {}

TEAM_HISTORY = []
TEAM_EVENTS = []

TEAM_MODE = "single"
BRAIN_VERSION = 0
TEAM_BRAIN_READY = False

TEAM_CHAT_ENABLED = True
TEAM_CHAT_MESSAGES = []
# Conversations each member has chosen to remove from their own chat view.
# This is intentionally per-user: deleting a chat never deletes it for other participants.
CHAT_DELETED_FOR = {}
# Per-user/per-conversation timestamp after which messages remain visible.
# This gives each member a private "delete/clear chat for me" boundary.
CHAT_CLEARED_AT = {}
# Per-user read cursor for WhatsApp-style unread counts/notifications.
CHAT_READ_UP_TO = {}
# {member_id: {conversation_id: last_message_id_seen}}
TEAM_GROUPS = {}
CHAT_AI_SESSIONS = {}
# Per-process AI memory only. It is deliberately never persisted.
AI_SESSION_EPOCH = uuid.uuid4().hex

HOST_TOKEN = os.environ.get("CODECHAT_HOST_TOKEN", "")
HOST_NAME = os.environ.get("CODECHAT_HOST_NAME", "Host").strip() or "Host"

PROJECT_DIR = os.environ.get("CODECHAT_PROJECT_DIR") or os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(PROJECT_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)
APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "CodeChat")
os.makedirs(APP_DATA_DIR, exist_ok=True)
SERVER_BRAIN_FILE = os.path.join(APP_DATA_DIR, "server_brain.brain")
SERVER_STATE_FILE = os.path.join(APP_DATA_DIR, "team_state.json")
BRAIN_LOCK = threading.RLock()
# Dedicated inference pool: a slow Ollama request must never consume the
# FastAPI coordination/control-plane worker handling /team_state, chat, members, etc.
AI_EXECUTOR = None
AI_ACTIVE = 0
AI_ACTIVE_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
SERVER_INIT_LOCK = threading.RLock()
# Serialize Team AI inference: Ollama is configured for one loaded model/parallel request.
# This prevents competing worker processes from causing runner churn and latency spikes.
AI_QUERY_LOCK = threading.Lock()
# Serialize uploads without blocking fast control-plane reads during embedding.
INGEST_LOCK = threading.Lock()
SERVER_INITIALIZED = False
CURRENT_SESSION_ID = None
CURRENT_SESSION_STARTED = None
SESSION_RECORDS = {}

# ============================================================
# DATA MODELS
# ============================================================

class Query(BaseModel):
    text: str
    public: bool = False


class Invite(BaseModel):
    # Kept as "email" for compatibility with the existing GUI.
    # It is a display name, not an email requirement.
    email: str
    role: str


class FileChunk(BaseModel):
    text: str
    source: str


class IngestRequest(BaseModel):
    chunks: List[FileChunk]
    append_mode: bool = True


class SyncPayload(BaseModel):
    b64_data: str


class HostLog(BaseModel):
    query: str
    answer: str


class ChatSettings(BaseModel):
    enabled: bool


class ChatMessageRequest(BaseModel):
    text: str
    conversation_id: Optional[str] = None


class GroupCreateRequest(BaseModel):
    name: str
    member_ids: List[str] = []


class GroupMemberRequest(BaseModel):
    member_id: str


class RemoveMemberRequest(BaseModel):
    member_id: str


class ChangeMemberRoleRequest(BaseModel):
    member_id: str
    role: str


class TeamLoginRequest(BaseModel):
    # Login uses the member's existing team credential. No team ID is needed.
    token: str


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _safe_json_state():
    # Persist membership metadata, groups, public activity and chat. Tokens are
    # retained so members remain members after a server restart; live presence is not.
    return {
        "access_tokens": ACCESS_TOKENS,
        "pending_invites": PENDING_INVITES,
        "team_history": TEAM_HISTORY,
        "team_events": TEAM_EVENTS,
        "team_groups": TEAM_GROUPS,
        "team_chat_messages": TEAM_CHAT_MESSAGES,
        "chat_deleted_for": {k: sorted(v) for k, v in CHAT_DELETED_FOR.items() if v},
        "chat_cleared_at": CHAT_CLEARED_AT,
        "chat_read_up_to": CHAT_READ_UP_TO,
        "team_mode": TEAM_MODE,
        "brain_version": BRAIN_VERSION,
        "team_brain_ready": TEAM_BRAIN_READY,
        "team_chat_enabled": TEAM_CHAT_ENABLED,
        "current_session_id": CURRENT_SESSION_ID,
        "current_session_started": CURRENT_SESSION_STARTED,
        "session_records": SESSION_RECORDS,
    }


def persist_server_state():
    try:
        with STATE_LOCK:
            tmp = SERVER_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_safe_json_state(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, SERVER_STATE_FILE)
    except Exception as e:
        print(f"State persistence warning: {e}")


def load_server_state():
    global ACCESS_TOKENS, PENDING_INVITES, TEAM_HISTORY, TEAM_EVENTS
    global TEAM_GROUPS, TEAM_CHAT_MESSAGES, CHAT_DELETED_FOR, CHAT_CLEARED_AT, CHAT_READ_UP_TO, TEAM_MODE, BRAIN_VERSION
    global TEAM_BRAIN_READY, TEAM_CHAT_ENABLED, CURRENT_SESSION_ID, CURRENT_SESSION_STARTED, SESSION_RECORDS
    try:
        if not os.path.exists(SERVER_STATE_FILE):
            return
        with open(SERVER_STATE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        ACCESS_TOKENS = d.get("access_tokens", {}) or {}
        PENDING_INVITES = d.get("pending_invites", {}) or {}
        TEAM_HISTORY = d.get("team_history", []) or []
        TEAM_EVENTS = d.get("team_events", []) or []
        TEAM_GROUPS = d.get("team_groups", {}) or {}
        TEAM_CHAT_MESSAGES = d.get("team_chat_messages", []) or []
        CHAT_DELETED_FOR = {str(k): set(v or []) for k, v in (d.get("chat_deleted_for", {}) or {}).items()}
        CHAT_CLEARED_AT = {str(k): dict(v or {}) for k, v in (d.get("chat_cleared_at", {}) or {}).items()}
        CHAT_READ_UP_TO = {str(k): dict(v or {}) for k, v in (d.get("chat_read_up_to", {}) or {}).items()}
        TEAM_MODE = d.get("team_mode", "single")
        BRAIN_VERSION = int(d.get("brain_version", 0) or 0)
        TEAM_BRAIN_READY = bool(d.get("team_brain_ready", False))
        TEAM_CHAT_ENABLED = bool(d.get("team_chat_enabled", True))
        CURRENT_SESSION_ID = d.get("current_session_id", CURRENT_SESSION_ID)
        CURRENT_SESSION_STARTED = d.get("current_session_started", CURRENT_SESSION_STARTED)
        SESSION_RECORDS = d.get("session_records", {}) or {}
    except Exception as e:
        print(f"State load warning: {e}")


def _history_file(session_id):
    return os.path.join(SESSIONS_DIR, session_id + ".json")


def _session_meta(session_id):
    meta = SESSION_RECORDS.get(session_id, {}) or {}
    return {
        "started_at": meta.get("started_at", CURRENT_SESSION_STARTED if session_id == CURRENT_SESSION_ID else ""),
        "created_by": meta.get("created_by", HOST_NAME),
        "participants": list(meta.get("participants", ["host"])),
    }

def snapshot_history(session_id=None):
    """Build the complete read-only history view for one Team session.
    Private DMs are retained only for server-side filtering and are never exposed
    to users who are not members of that conversation."""
    session_id = session_id or CURRENT_SESSION_ID
    meta = _session_meta(session_id)
    return {
        "session_id": session_id,
        "started_at": meta["started_at"],
        "created_by": meta["created_by"],
        "updated_at": now_iso(),
        "live": session_id == CURRENT_SESSION_ID,
        "participants": meta["participants"],
        "events": [e for e in TEAM_EVENTS if e.get("session_id") == session_id],
        "public_ai": [h for h in TEAM_HISTORY if h.get("session_id") == session_id],
        "chat_messages": [m for m in TEAM_CHAT_MESSAGES if m.get("session_id") == session_id],
        "groups": dict(TEAM_GROUPS),
        "members": public_member_list(),
        "mode": TEAM_MODE,
        "brain_version": BRAIN_VERSION,
    }


def persist_history():
    try:
        path = _history_file(CURRENT_SESSION_ID)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot_history(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"History persistence warning: {e}")


def record_activity():
    persist_server_state()
    persist_history()


def start_new_live_session():
    """Archive the previous session and create a fresh live session.
    Every server launch creates a uniquely named history_ timestamp file under
    the project-local sessions/ directory. Only the new live session is updated
    in real time; older files are immutable read-only archives."""
    global CURRENT_SESSION_ID, CURRENT_SESSION_STARTED, TEAM_EVENTS, TEAM_HISTORY, SESSION_RECORDS
    old_id = CURRENT_SESSION_ID
    try:
        if old_id:
            # Ensure the previous session has metadata before archiving it.
            if old_id not in SESSION_RECORDS:
                SESSION_RECORDS[old_id] = {
                    "started_at": CURRENT_SESSION_STARTED,
                    "created_by": HOST_NAME,
                    "participants": ["host"],
                }
            old_path = _history_file(old_id)
            archived = snapshot_history(old_id)
            archived["live"] = False
            with open(old_path, "w", encoding="utf-8") as f:
                json.dump(archived, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Session archive warning: {e}")

    CURRENT_SESSION_ID = "history_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    CURRENT_SESSION_STARTED = now_iso()
    SESSION_RECORDS[CURRENT_SESSION_ID] = {
        "started_at": CURRENT_SESSION_STARTED,
        "created_by": HOST_NAME,
        "participants": ["host"],
    }
    TEAM_EVENTS = []
    TEAM_HISTORY = []
    persist_server_state()

    # Explicit first event: the live History tab immediately shows who started
    # the session and exactly when it started.
    TEAM_EVENTS.append({
        "type": "session_started",
        "actor": HOST_NAME,
        "session_id": CURRENT_SESSION_ID,
        "target": None,
        "details": {"session": CURRENT_SESSION_ID, "started_at": CURRENT_SESSION_STARTED},
        "timestamp": CURRENT_SESSION_STARTED,
    })
    persist_server_state()
    persist_history()


def record_event(event_type, actor, target=None, details=None):
    SESSION_RECORDS.setdefault(CURRENT_SESSION_ID, {
        "started_at": CURRENT_SESSION_STARTED,
        "created_by": HOST_NAME,
        "participants": ["host"],
    })
    TEAM_EVENTS.append({
        "type": event_type,
        "actor": actor,
        "session_id": CURRENT_SESSION_ID,
        "target": target,
        "details": details or {},
        "timestamp": now_iso()
    })
    if len(TEAM_EVENTS) > 5000:
        TEAM_EVENTS.pop(0)
    record_activity()


def host_info():
    return {
        "member_id": "host",
        "name": HOST_NAME,
        "handle": "host",
        "role": "host",
        "status": "active",
        "online": True
    }


def make_handle(name):
    base = re.sub(r"[^a-zA-Z0-9_]", "", name.lower().replace(" ", "_")) or "member"
    base = base[:24]
    used = {v.get("handle") for v in ACCESS_TOKENS.values() if v.get("status") == "active"}
    used.update(g.get("handle") for g in TEAM_GROUPS.values())
    handle = base
    counter = 2
    while handle in used:
        handle = f"{base}{counter}"
        counter += 1
    return handle


def member_by_id(member_id):
    if member_id == "host":
        return host_info()
    for info in ACCESS_TOKENS.values():
        if info.get("member_id") == member_id and info.get("status") == "active":
            return info
    return None


def member_by_handle(handle):
    handle = handle.lower()
    if handle == "host":
        return host_info()
    for info in ACCESS_TOKENS.values():
        if info.get("status") == "active" and info.get("handle", "").lower() == handle:
            return info
    return None


def member_display(info):
    return info.get("name") or info.get("email") or "Unknown"


def conversation_members(conversation_id):
    if conversation_id == "team":
        return ["host"] + [
            info["member_id"]
            for info in ACCESS_TOKENS.values()
            if info.get("status") == "active"
        ]

    if conversation_id.startswith("dm:"):
        parts = conversation_id.split(":")
        return parts[1:] if len(parts) == 3 else []

    if conversation_id.startswith("group:"):
        group_id = conversation_id.split(":", 1)[1]
        group = TEAM_GROUPS.get(group_id)
        return list(group.get("members", [])) if group else []

    return []


def can_access_conversation(member_id, conversation_id):
    return member_id in conversation_members(conversation_id)


def make_dm_id(a, b):
    return "dm:" + ":".join(sorted([a, b]))


def parse_route(text, sender_id):
    """
    Routing rules:
      @team hello      -> team
      @person hello    -> person's DM
      @group hello     -> group's conversation
    The routing mention is retained in the message body.
    """
    match = re.match(r"^\s*@([A-Za-z0-9_]+)\b(.*)$", text, flags=re.DOTALL)
    if not match:
        return None, None

    target = match.group(1).lower()
    remainder = match.group(2).strip()

    if target == "team":
        return "team", remainder

    person = member_by_handle(target)
    if person:
        target_id = person["member_id"]
        if target_id == sender_id:
            raise HTTPException(status_code=400, detail="You cannot DM yourself.")
        return make_dm_id(sender_id, target_id), remainder

    for group_id, group in TEAM_GROUPS.items():
        if group.get("handle", "").lower() == target and sender_id in group.get("members", []):
            return f"group:{group_id}", remainder

    raise HTTPException(status_code=404, detail=f"No team member or group found for @{target}.")


def auth_from_token(token):
    if token == HOST_TOKEN and token:
        return {
            "token": token,
            "member_id": "host",
            "info": {
                "member_id": "host",
                "name": HOST_NAME,
                "handle": "host",
                "email": HOST_NAME,
                "role": "host",
                "status": "active"
            }
        }

    info = ACCESS_TOKENS.get(token)
    if not info or info.get("status") != "active":
        raise HTTPException(status_code=401, detail="Invalid or inactive team token.")

    ACTIVE_USERS[token] = datetime.now()
    return {"token": token, "member_id": info["member_id"], "info": info}


def get_user(x_access_token: str = Header(...)):
    return auth_from_token(x_access_token)


def require_host(x_access_token: str = Header(...)):
    if x_access_token != HOST_TOKEN or not HOST_TOKEN:
        raise HTTPException(status_code=403, detail="Only Host can perform this action.")
    return auth_from_token(x_access_token)


def require_inviter(user_data):
    role = user_data["info"]["role"]
    if role not in ("host", "collaborator"):
        raise HTTPException(
            status_code=403,
            detail="Guests cannot invite new team members."
        )
    return user_data


def public_member_list():
    members = [host_info()]
    for info in ACCESS_TOKENS.values():
        if info.get("status") == "active":
            members.append({
                "member_id": info["member_id"],
                "name": member_display(info),
                "email": info.get("email", ""),
                "handle": info.get("handle", ""),
                "role": info.get("role", "guest"),
                "status": info.get("status", "active"),
                "online": is_online(info["token"])
            })
    return members


def is_online(token):
    seen = ACTIVE_USERS.get(token)
    if not seen:
        return False
    return (datetime.now() - seen).total_seconds() <= 60


def cleanup_inactive():
    now = datetime.now()
    for token, last_seen in list(ACTIVE_USERS.items()):
        if (now - last_seen).total_seconds() > 60:
            ACTIVE_USERS.pop(token, None)


def get_chat_message_view(message):
    return {
        "id": message["id"],
        "conversation_id": message["conversation_id"],
        "sender_id": message["sender_id"],
        "sender": message["sender"],
        "handle": message["handle"],
        "message": message["message"],
        "timestamp": message["timestamp"],
        "mentions": message.get("mentions", [])
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "45.0",
        "service": "codechat-team-server",
        "ai_session_epoch": AI_SESSION_EPOCH,
        "ai_active": AI_ACTIVE,
    }


# ============================================================
# INVITES / MEMBERS
# ============================================================

@app.post("/generate_invite")
def create_invite(inv: Invite, user_data: dict = Depends(get_user)):
    require_inviter(user_data)

    name = inv.email.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")

    role = inv.role.lower().strip()
    if role not in ("guest", "collaborator"):
        raise HTTPException(status_code=400, detail="Role must be guest or collaborator.")

    token = secrets.token_hex(24)
    invite = {
        "token": token,
        "name": name,
        "email": name,
        "role": role,
        "invited_by": user_data["member_id"],
        "invited_by_name": member_display(user_data["info"]),
        "created_at": now_iso()
    }
    PENDING_INVITES[token] = invite

    record_event(
        "invited",
        member_display(user_data["info"]),
        name,
        {"role": role, "invite_token_created": True}
    )
    persist_server_state()

    return {
        "status": "Invite generated",
        "token": token,
        "role": role,
        "invited_name": name,
        "invited_by": member_display(user_data["info"])
    }


@app.get("/check_role")
def check_role(x_access_token: str = Header(...)):
    # A pending invite becomes a member on first use.
    # A member who voluntarily LEFT may use the same credential again to
    # rejoin. A REMOVED member may never self-rejoin; the Host must issue a
    # genuinely new invite/credential. This distinction also makes offline
    # and revoked access fail closed through normal authentication.
    if x_access_token in PENDING_INVITES:
        invite = PENDING_INVITES.pop(x_access_token)

        member_id = uuid.uuid4().hex
        handle = make_handle(invite["name"])
        ACCESS_TOKENS[x_access_token] = {
            "token": x_access_token,
            "member_id": member_id,
            "email": invite["email"],
            "name": invite["name"],
            "role": invite["role"],
            "handle": handle,
            "status": "active",
            "invited_by": invite["invited_by"],
            "invited_by_name": invite["invited_by_name"],
            "invited_at": invite.get("created_at", now_iso()),
            "joined_at": now_iso()
        }
        USER_SESSIONS[x_access_token] = []

        SESSION_RECORDS.setdefault(CURRENT_SESSION_ID, {"started_at": CURRENT_SESSION_STARTED, "created_by": HOST_NAME, "participants": ["host"]})
        if member_id not in SESSION_RECORDS[CURRENT_SESSION_ID]["participants"]:
            SESSION_RECORDS[CURRENT_SESSION_ID]["participants"].append(member_id)
        record_event(
            "joined",
            invite["name"],
            None,
            {
                "member_id": member_id,
                "role": invite["role"],
                "invited_by": invite["invited_by_name"]
            }
        )
        persist_server_state()
    else:
        existing = ACCESS_TOKENS.get(x_access_token)
        if existing and existing.get("status") == "left":
            # Rejoin with the same original credential after a voluntary leave.
            existing["status"] = "active"
            existing["rejoined_at"] = now_iso()
            existing.pop("left_at", None)
            USER_SESSIONS.setdefault(x_access_token, [])
            record_event(
                "rejoined",
                member_display(existing),
                None,
                {"member_id": existing.get("member_id"), "role": existing.get("role")}
            )
            persist_server_state()

    user_data = auth_from_token(x_access_token)
    return {
        "role": user_data["info"]["role"],
        "name": member_display(user_data["info"]),
        "handle": user_data["info"].get("handle", "host"),
        "member_id": user_data["member_id"]
    }


@app.post("/members/remove")
def remove_member(req: RemoveMemberRequest, user_data: dict = Depends(require_host)):
    target = member_by_id(req.member_id)
    if not target or req.member_id == "host":
        raise HTTPException(status_code=404, detail="Team member not found.")

    target_token = target.get("token")
    target_name = member_display(target)

    target["status"] = "removed"
    target["removed_by"] = member_display(user_data["info"])
    target["removed_at"] = now_iso()

    ACTIVE_USERS.pop(target_token, None)
    USER_SESSIONS.pop(target_token, None)

    record_event(
        "removed",
        member_display(user_data["info"]),
        target_name,
        {"member_id": req.member_id, "role": target.get("role")}
    )
    persist_server_state()

    return {
        "status": "removed",
        "member_id": req.member_id,
        "name": target_name,
        "removed_by": member_display(user_data["info"])
    }


@app.post("/members/change_role")
def change_member_role(req: ChangeMemberRoleRequest, user_data: dict = Depends(require_host)):
    target = member_by_id(req.member_id)
    if not target or req.member_id == "host":
        raise HTTPException(status_code=404, detail="Team member not found.")
    new_role = req.role.strip().lower()
    if new_role not in ("guest", "collaborator"):
        raise HTTPException(status_code=400, detail="Role must be guest or collaborator.")
    old_role = target.get("role", "guest")
    if old_role == new_role:
        return {"status": "unchanged", "member_id": req.member_id, "role": new_role}
    target["role"] = new_role
    target["role_changed_at"] = now_iso()
    target["role_changed_by"] = member_display(user_data["info"])
    record_event(
        "role_changed",
        member_display(user_data["info"]),
        member_display(target),
        {"member_id": req.member_id, "from": old_role, "to": new_role}
    )
    persist_server_state()
    return {"status": "role_changed", "member_id": req.member_id, "name": member_display(target), "role": new_role}


@app.post("/team/login")
def team_login(req: TeamLoginRequest):
    token = req.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Login credential is required.")
    user_data = auth_from_token(token)
    info = user_data["info"]
    return {
        "status": "ok",
        "role": info.get("role", "guest"),
        "name": member_display(info),
        "handle": info.get("handle", ""),
        "member_id": user_data["member_id"]
    }


@app.post("/leave")
def leave_team(user_data: dict = Depends(get_user)):
    if user_data["member_id"] == "host":
        raise HTTPException(
            status_code=403,
            detail="Host cannot leave the team. Shut down the Host application instead."
        )

    info = user_data["info"]
    name = member_display(info)
    token = user_data["token"]

    info["status"] = "left"
    info["left_at"] = now_iso()

    ACTIVE_USERS.pop(token, None)
    USER_SESSIONS.pop(token, None)

    record_event("left", name)
    persist_server_state()

    return {"status": "left", "name": name}


@app.post("/logout")
def logout_user(x_access_token: str = Header(...)):
    # Logout only marks the session offline; it does not revoke membership.
    ACTIVE_USERS.pop(x_access_token, None)
    return {"status": "Logged out"}


# ============================================================
# TEAM STATE
# ============================================================

@app.get("/team_state")
def get_team_state(user_data: dict = Depends(get_user)):
    cleanup_inactive()
    info = user_data["info"]
    return {
        "mode": TEAM_MODE,
        "brain_ready": TEAM_BRAIN_READY,
        "brain_version": BRAIN_VERSION,
        "chunks": len(brain.chunks),
        "chat_enabled": TEAM_CHAT_ENABLED,
        "role": info["role"],
        "name": member_display(info),
        "handle": info.get("handle", "host"),
        "member_id": user_data["member_id"],
        "members": public_member_list(),
        "files": [os.path.basename(str(x)) for x in getattr(brain, "source_order", []) if x in set(brain.sources)],
        "file_count": len(getattr(brain, "source_order", []) or set(brain.sources)),
        "admin": HOST_NAME,
        "session_id": CURRENT_SESSION_ID,
        "events": TEAM_EVENTS[-100:],
        "live_history": history_view_for(user_data["member_id"], snapshot_history()),
        # Consolidated control-plane data: the GUI can refresh most team UI with
        # one request instead of a cascade of polling requests.
        "users": [m for m in public_member_list() if m["member_id"] == "host" or m["online"]],
        "conversations": get_chat_conversations.__wrapped__(user_data) if False else get_chat_conversations(user_data),
        "history_sessions": [
            {"session_id": sid, "started_at": meta.get("started_at", ""), "live": sid == CURRENT_SESSION_ID}
            for sid, meta in SESSION_RECORDS.items()
        ]
    }


@app.post("/set_mode")
def set_mode(mode_data: dict, user_data: dict = Depends(require_host)):
    global TEAM_MODE, BRAIN_VERSION, TEAM_BRAIN_READY

    mode = mode_data.get("mode")
    if mode not in ("single", "append"):
        raise HTTPException(status_code=400, detail="Mode must be 'single' or 'append'.")

    with BRAIN_LOCK:
        previous = TEAM_MODE
        # append -> single is destructive by design: retain ONLY the newest source file.
        if previous == "append" and mode == "single" and TEAM_BRAIN_READY and brain.sources:
            # IMPORTANT: changing MODE is NOT a new AI session. The user explicitly
            # expects the current conversation/session to survive a mode switch.
            # Only the authoritative evidence set is changed here.
            old_chunks = list(brain.chunks)
            old_sources = list(brain.sources)
            old_embeddings = np.asarray(brain.embeddings, dtype=np.float32).copy()
            old_source_order = list(getattr(brain, "source_order", []) or [])
            old_history = list(brain.local_history)
            result = brain.retain_latest_file()
            if result.startswith("Retained"):
                save_result = brain.save_snapshot(SERVER_BRAIN_FILE)
                if "Success" not in save_result:
                    brain.chunks = old_chunks
                    brain.sources = old_sources
                    brain.embeddings = old_embeddings
                    brain.source_order = old_source_order
                    brain.local_history = old_history
                    raise HTTPException(status_code=500, detail=f"Could not persist Single Mode: {save_result}")
            else:
                raise HTTPException(status_code=409, detail=result)

        # Mode is workspace configuration, NOT a session boundary.
        # Do not clear USER_SESSIONS, CHAT_AI_SESSIONS, human chat, or Ollama.
        TEAM_MODE = mode
        brain.team_mode = mode
        if hasattr(brain, "workspace_context") and isinstance(brain.workspace_context, dict):
            brain.workspace_context["mode"] = mode
        # Bump the authoritative workspace version so all clients can observe the
        # mode transition without having their local AI conversation refreshed.
        BRAIN_VERSION += 1

    record_event(
        "mode_changed",
        member_display(user_data["info"]),
        None,
        {"from": previous, "mode": mode, "latest_file_only": previous == "append" and mode == "single"}
    )
    return {
        "status": "Mode updated",
        "mode": TEAM_MODE,
        "brain_version": BRAIN_VERSION,
        "chunks": len(brain.chunks)
    }


# ============================================================
# HOST BRAIN SYNC
# ============================================================

@app.post("/sync_brain")
def sync_brain(payload: SyncPayload, user_data: dict = Depends(require_host)):
    global BRAIN_VERSION, TEAM_BRAIN_READY

    print("⚡ HOST BRAIN SYNC REQUEST RECEIVED")

    try:
        file_bytes = base64.b64decode(payload.b64_data)

        fd, temp_path = tempfile.mkstemp(prefix="codechat_sync_", suffix=".brain", dir=APP_DATA_DIR)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_bytes)
            with BRAIN_LOCK:
                res = brain.load_snapshot(temp_path)
            if "Success" in res:
                os.replace(temp_path, SERVER_BRAIN_FILE)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        if "Success" not in res:
            raise HTTPException(status_code=500, detail=f"Failed to load: {res}")

        BRAIN_VERSION += 1
        TEAM_BRAIN_READY = True
        # Brain updates do not reset the current AI/chat session.

        record_event(
            "brain_synced",
            member_display(user_data["info"]),
            None,
            {"version": BRAIN_VERSION, "chunks": len(brain.chunks)}
        )

        print(
            f"✅ TEAM BRAIN READY | Version: {BRAIN_VERSION} | "
            f"Chunks: {len(brain.chunks)}"
        )

        return {
            "status": "Team Brain Synced",
            "chunks": len(brain.chunks),
            "brain_version": BRAIN_VERSION,
            "mode": TEAM_MODE
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ SYNC ERROR: {e}")
        raise HTTPException(status_code=500, detail=f"Sync Error: {str(e)}")


# ============================================================
# DOWNLOAD BRAIN
# ============================================================

@app.get("/download_brain")
def download_brain(user_data: dict = Depends(get_user)):
    if not TEAM_BRAIN_READY:
        raise HTTPException(status_code=404, detail="Team Brain is not initialized.")

    download_path = os.path.join(APP_DATA_DIR, "server_download.brain")
    with BRAIN_LOCK:
        res = brain.save_snapshot(download_path)

    if "Success" in res and os.path.exists(download_path):
        return FileResponse(download_path, filename="codechat_brain.brain")

    raise HTTPException(
        status_code=500,
        detail="Could not generate brain snapshot"
    )


# ============================================================
# QUERY / TEAM AI STREAM
# ============================================================

def _set_brain_workspace_context():
    # Defensive initialization fixes CoreBrain instances created by older builds
    # or stale frozen modules.
    if not hasattr(brain, "workspace_context") or not isinstance(getattr(brain, "workspace_context", None), dict):
        brain.workspace_context = {}
    if not hasattr(brain, "team_mode"):
        brain.team_mode = TEAM_MODE
    brain.workspace_context = {
        "identity": "CodeChat AI inside CodeChat Pro Team Edition",
        "mode": TEAM_MODE,
        "admin": HOST_NAME,
        "members": [
            {"name": m.get("name"), "handle": m.get("handle"), "role": m.get("role")}
            for m in public_member_list()
        ],
        "file_count": len(set(brain.sources)),
        "files": [str(x) for x in getattr(brain, "source_order", []) if x in set(brain.sources)],
        "file_details": [
            {"source": str(src), "filename": os.path.basename(str(src)),
             "chunks": sum(1 for x in brain.sources if x == src)}
            for src in getattr(brain, "source_order", []) if src in set(brain.sources)
        ],
        "brain_version": BRAIN_VERSION,
    }


def _isolated_ai_target(conn, payload):
    try:
        from backend import run_isolated_ai_query
        result = run_isolated_ai_query(payload)
        conn.send((True, result))
    except Exception as exc:
        conn.send((False, str(exc)))
    finally:
        conn.close()


def _run_team_ai_query(query_text, token):
    global AI_ACTIVE
    # One Team AI inference at a time keeps the isolated Ollama runner stable.
    with AI_QUERY_LOCK:
        return _run_team_ai_query_locked(query_text, token)

def _run_team_ai_query_locked(query_text, token):
    global AI_ACTIVE
    with AI_ACTIVE_LOCK:
        AI_ACTIVE += 1
    parent_conn, child_conn = mp.Pipe(duplex=False)
    try:
        _set_brain_workspace_context()
        with STATE_LOCK:
            history = list(USER_SESSIONS.setdefault(token, []))
        with BRAIN_LOCK:
            payload = {
                "query": query_text,
                "chunks": list(brain.chunks),
                "sources": list(brain.sources),
                "embeddings": np.asarray(brain.embeddings, dtype=np.float32).copy(),
                "source_order": list(getattr(brain, "source_order", []) or []),
                "history": history,
                "model": getattr(brain, "model", "llama3.1"),
                "embedding_model": getattr(brain, "embedding_model", "nomic-embed-text"),
                "team_mode": getattr(brain, "team_mode", TEAM_MODE),
                "workspace_context": dict(getattr(brain, "workspace_context", {}) or {}),
            }

        ctx = mp.get_context("spawn") if os.name == "nt" else mp.get_context("fork")
        proc = ctx.Process(target=_isolated_ai_target, args=(child_conn, payload), daemon=True)
        proc.start()
        child_conn.close()
        if not parent_conn.poll(120):
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
            return "⚠️ AI inference timed out safely. Team services remain available; please try again.", []
        ok, result = parent_conn.recv()
        proc.join(5)
        if ok:
            ans, srcs = result
            with STATE_LOCK:
                session = USER_SESSIONS.setdefault(token, [])
                session.extend([
                    {"role": "user", "content": query_text},
                    {"role": "assistant", "content": ans},
                ])
                del session[:-8]
            return ans, srcs
        return f"⚠️ AI worker failed safely: {result}", []
    finally:
        try: parent_conn.close()
        except Exception: pass
        try: child_conn.close()
        except Exception: pass
        with AI_ACTIVE_LOCK:
            AI_ACTIVE = max(0, AI_ACTIVE - 1)


@app.post("/query")
async def query_brain(q: Query, user_data: dict = Depends(get_user)):
    token = user_data["token"]
    name = member_display(user_data["info"])

    with BRAIN_LOCK:
        brain_ready = bool(TEAM_BRAIN_READY and len(brain.chunks) > 0)
    if not brain_ready:
        return {
            "answer": "⚠️ Team Brain is empty. Ask the Host to load code.",
            "sources": []
        }

    # The AI worker is an independently killable process. FastAPI itself only
    # coordinates the request and therefore remains responsive even if Ollama
    # or a model runner becomes stuck.
    loop = asyncio.get_running_loop()
    ans, srcs = await loop.run_in_executor(None, _run_team_ai_query, q.text, token)

    if q.public:
        TEAM_HISTORY.append({
            "user": name,
            "query": q.text,
            "session_id": CURRENT_SESSION_ID,
            "answer": ans,
            "timestamp": now_iso()
        })
        if len(TEAM_HISTORY) > 50:
            TEAM_HISTORY.pop(0)

    return {"answer": ans, "sources": srcs}


# ============================================================
# TEAM FILE INGEST
# ============================================================

@app.post("/ingest")
def ingest_remote(req: IngestRequest, user_data: dict = Depends(get_user)):
    global BRAIN_VERSION, TEAM_BRAIN_READY

    role = user_data["info"]["role"]

    if role not in ("host", "collaborator"):
        raise HTTPException(
            status_code=403,
            detail="Only Host or Collaborators can upload code."
        )

    # A collaborator can never create the first Team Brain.
    if role != "host" and not TEAM_BRAIN_READY:
        raise HTTPException(
            status_code=403,
            detail=(
                "Team Brain is not initialized. "
                "Only the Host can create the initial Team Brain."
            )
        )

    append_mode = TEAM_MODE == "append"
    uploader = member_display(user_data["info"])

    print(
        f"📥 TEAM UPLOAD from {uploader} | "
        f"Server Mode: {TEAM_MODE}"
    )

    INGEST_LOCK.acquire()
    try:
        # The client-provided source path is metadata only. The server creates a
        # stable per-member logical identity, so `main.py` from two contributors
        # can never silently overwrite or masquerade as the same file.
        tuples = []
        for c in req.chunks:
            raw_name = os.path.basename(str(c.source).replace("\\", "/")) or "unnamed_file"
            logical_source = f"{user_data['member_id']}/{raw_name}"
            tuples.append((c.text, logical_source))

        with BRAIN_LOCK:
            old_chunks = list(brain.chunks)
            old_sources = list(brain.sources)
            old_embeddings = np.asarray(brain.embeddings, dtype=np.float32).copy() if len(brain.embeddings) else np.asarray([], dtype=np.float32)
            old_history = list(brain.local_history)
            result = brain.ingest_remote_data(
                tuples,
                lambda x: print(f"-> {x}"),
                append_mode=append_mode
            )

            # Hard invariant: Single Mode contains exactly the latest uploaded
            # file. This is enforced server-side even if a client sends a stale
            # append_mode flag or an older client implementation.
            if TEAM_MODE == "single" and brain.sources:
                latest_source = brain.source_order[-1] if getattr(brain, "source_order", []) else brain.sources[-1]
                keep = [i for i, src in enumerate(brain.sources) if src == latest_source]
                if keep and len(keep) != len(brain.sources):
                    brain.chunks = [brain.chunks[i] for i in keep]
                    brain.sources = [brain.sources[i] for i in keep]
                    brain.embeddings = np.asarray(brain.embeddings, dtype=np.float32)[keep]
                    brain.source_order = [latest_source]
                    brain.local_history = []

            # Keep the authoritative manifest clean and ordered.
            seen_manifest = []
            for src in getattr(brain, "source_order", []) or brain.sources:
                if src in brain.sources and src not in seen_manifest:
                    seen_manifest.append(src)
            for src in brain.sources:
                if src not in seen_manifest:
                    seen_manifest.append(src)
            brain.source_order = seen_manifest

            save_result = brain.save_snapshot(SERVER_BRAIN_FILE)
            if "Success" not in save_result:
                brain.chunks = old_chunks
                brain.sources = old_sources
                brain.embeddings = old_embeddings
                brain.local_history = old_history
        if "Success" not in save_result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not save Team Brain: {save_result}"
            )

        BRAIN_VERSION += 1
        TEAM_BRAIN_READY = True
        # Brain updates do not reset the current AI/chat session.

        record_event(
            "brain_updated",
            uploader,
            None,
            {
                "mode": TEAM_MODE,
                "version": BRAIN_VERSION,
                "chunks": len(brain.chunks)
            }
        )

        print(
            f"✅ TEAM BRAIN UPDATED | Version: {BRAIN_VERSION} | "
            f"Chunks: {len(brain.chunks)}"
        )

        return {
            "status": "Indexed",
            "mode": TEAM_MODE,
            "brain_version": BRAIN_VERSION,
            "chunks": len(brain.chunks),
            "result": result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        INGEST_LOCK.release()


# ============================================================
# HOST AI LOG
# ============================================================

@app.post("/host_log")
def log_host_activity(log: HostLog, user_data: dict = Depends(require_host)):
    # This endpoint is the authoritative bridge for Host-local public queries.
    # Make retries idempotent so a successful retry can never duplicate a
    # Team Stream entry.
    entry = {
        "user": member_display(user_data["info"]),
        "query": log.query,
        "session_id": CURRENT_SESSION_ID,
        "answer": log.answer,
        "timestamp": now_iso()
    }
    with STATE_LOCK:
        duplicate = any(
            h.get("session_id") == CURRENT_SESSION_ID
            and h.get("query") == log.query
            and h.get("answer") == log.answer
            and h.get("user") == entry["user"]
            for h in TEAM_HISTORY[-20:]
        )
        if not duplicate:
            TEAM_HISTORY.append(entry)
            if len(TEAM_HISTORY) > 5000:
                TEAM_HISTORY.pop(0)
    record_activity()

    return {"status": "logged"}


@app.get("/team_activity")
def get_team_activity(user_data: dict = Depends(get_user)):
    # Team Stream is intentionally public/live only. Administrative history is
    # served by /history and is never mixed into this stream.
    return {"history": [h for h in TEAM_HISTORY if h.get("session_id") == CURRENT_SESSION_ID][-100:]}


# ============================================================
# LIVE HISTORY
# ============================================================

@app.get("/history/live")
def get_live_history(user_data: dict = Depends(get_user)):
    # This endpoint is deliberately separate from archived sessions. Every
    # client can poll the same live session while an older selection remains
    # local to that client.
    return history_view_for(user_data["member_id"], snapshot_history(CURRENT_SESSION_ID))


# ============================================================
# ACTIVE USERS
# ============================================================

@app.get("/members/{member_id}/details")
def get_member_details(member_id: str, user_data: dict = Depends(get_user)):
    target = member_by_id(member_id)
    if not target and member_id != "host":
        # Resolve from the complete membership table, including left/removed
        # members. The active-user list is only a presence view and must never
        # be the source of truth for identity.
        for info in ACCESS_TOKENS.values():
            if info.get("member_id") == member_id:
                target = info
                break
    if not target and member_id != "host":
        # Last-resort identity recovery from the current session events. This
        # protects against a UI race during invite/join/remove transitions.
        for ev in reversed(TEAM_EVENTS):
            details = ev.get("details", {}) or {}
            if details.get("member_id") == member_id:
                target = {
                    "member_id": member_id,
                    "name": ev.get("actor") or ev.get("target") or "Unknown",
                    "handle": "",
                    "role": details.get("role", "guest"),
                    "status": "active"
                }
                break
    if not target:
        if member_id == "host":
            target = host_info()
        else:
            raise HTTPException(status_code=404, detail="Member details not found.")
    groups = []
    for gid, group in TEAM_GROUPS.items():
        if member_id in group.get("members", []):
            groups.append({"group_id": gid, "name": group.get("name"), "handle": group.get("handle")})
    return {
        "member_id": member_id,
        "name": member_display(target),
        "handle": target.get("handle", "host"),
        "role": target.get("role", "host"),
        "status": target.get("status", "active"),
        "online": True if member_id == "host" else is_online(target.get("token", "")),
        "current_session": CURRENT_SESSION_ID,
        "session_started_at": CURRENT_SESSION_STARTED,
        "invited_by": target.get("invited_by_name", "System / Team Host"),
        "invited_at": target.get("invited_at", target.get("created_at", "")),
        "joined_at": target.get("joined_at", ""),
        "removed_by": target.get("removed_by", ""),
        "removed_at": target.get("removed_at", ""),
        "left_at": target.get("left_at", ""),
        "groups": groups,
    }


@app.get("/active_users")
def get_active_users(user_data: dict = Depends(get_user)):
    cleanup_inactive()

    active_list = []
    for member in public_member_list():
        if member["member_id"] == "host" or member["online"]:
            active_list.append(member)

    return {"users": active_list}


# ============================================================
# HISTORY — LIVE PUBLIC + PRIVATE PER-MEMBER ARCHIVES
# ============================================================

def history_view_for(member_id, payload):
    out = dict(payload)
    msgs = []
    for m in payload.get("chat_messages", []):
        cid = m.get("conversation_id", "")
        if cid == "team":
            visible = True
        elif cid.startswith("dm:") and member_id in conversation_members(cid):
            visible = True
        elif cid.startswith("group:") and member_id in conversation_members(cid):
            visible = True
        else:
            visible = False
        if not visible:
            continue
        clear_after_id = CHAT_CLEARED_AT.get(member_id, {}).get(cid)
        if clear_after_id:
            session_msgs = [x for x in payload.get("chat_messages", []) if x.get("conversation_id") == cid]
            ids = [x.get("id") for x in session_msgs]
            # If the clear marker belongs to this archived session, hide up to it.
            if clear_after_id in ids:
                if m.get("id") == clear_after_id or ids.index(m.get("id")) < ids.index(clear_after_id):
                    continue
        msgs.append(m)
    out["chat_messages"] = msgs
    out["private_only_for"] = member_id
    return out


@app.get("/history/sessions")
def list_history_sessions(user_data: dict = Depends(get_user)):
    member_id = user_data["member_id"]
    sessions = [{
        "session_id": CURRENT_SESSION_ID,
        "name": CURRENT_SESSION_ID,
        "started_at": CURRENT_SESSION_STARTED,
        "updated_at": now_iso(),
        "live": True,
        "current": True
    }]
    try:
        files = sorted([x for x in os.listdir(SESSIONS_DIR) if x.startswith("history_") and x.endswith(".json")], reverse=True)
        for name in files:
            sid = name[:-5]
            if sid == CURRENT_SESSION_ID:
                continue
            try:
                with open(os.path.join(SESSIONS_DIR, name), "r", encoding="utf-8") as f:
                    d = json.load(f)
                # Team history is read-only and selectable by every connected
                # team member. Private DM contents are filtered separately.
                sessions.append({
                    "session_id": sid,
                    "name": sid,
                    "started_at": d.get("started_at", ""),
                    "updated_at": d.get("updated_at", ""),
                    "live": False,
                    "current": False
                })
            except Exception:
                continue
    except Exception:
        pass
    return {"sessions": sessions[:100]}


@app.get("/history/session/{session_id}")
def get_history_session(session_id: str, user_data: dict = Depends(get_user)):
    member_id = user_data["member_id"]
    if session_id == CURRENT_SESSION_ID:
        return history_view_for(member_id, snapshot_history())
    if not re.fullmatch(r"history_[0-9]{8}_[0-9]{6}(?:_[0-9]{6})?", session_id):
        raise HTTPException(status_code=400, detail="Invalid history session id.")
    path = _history_file(session_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="History session not found.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read history: {e}")
    return history_view_for(member_id, d)


# ============================================================
# TEAM CHAT
# ============================================================

@app.post("/chat/settings")
def set_chat_settings(
    settings: ChatSettings,
    user_data: dict = Depends(require_host)
):
    global TEAM_CHAT_ENABLED

    TEAM_CHAT_ENABLED = bool(settings.enabled)

    record_event(
        "chat_settings",
        member_display(user_data["info"]),
        None,
        {"enabled": TEAM_CHAT_ENABLED}
    )

    return {"enabled": TEAM_CHAT_ENABLED}


@app.get("/chat/conversations")
def get_chat_conversations(user_data: dict = Depends(get_user)):
    member_id = user_data["member_id"]

    deleted = CHAT_DELETED_FOR.get(member_id, set())
    conversations = []
    if "team" not in deleted:
        conversations.append({
            "id": "team",
            "name": "Team",
            "type": "team",
            "members": conversation_members("team")
        })

    # Every active member must be able to start a DM with the Host.
    # The Host is not stored in ACCESS_TOKENS, so explicitly add the Host
    # conversation for collaborators/guests.
    if member_id != "host":
        h = host_info()
        host_conv_id = make_dm_id(member_id, "host")
        if host_conv_id in deleted:
            host_conv_id = None
        if host_conv_id:
            conversations.append({
            "id": host_conv_id,
            "name": member_display(h),
            "handle": h.get("handle", "host"),
            "type": "dm",
            "member_id": "host"
        })

    for info in ACCESS_TOKENS.values():
        if info.get("status") != "active":
            continue
        other_id = info["member_id"]
        if other_id == member_id:
            continue
        dm_id = make_dm_id(member_id, other_id)
        if dm_id in deleted:
            continue
        conversations.append({
            "id": dm_id,
            "name": member_display(info),
            "handle": info.get("handle", ""),
            "type": "dm",
            "member_id": other_id
        })

    for group_id, group in TEAM_GROUPS.items():
        group_conv_id = f"group:{group_id}"
        if member_id in group.get("members", []) and group_conv_id not in deleted:
            conversations.append({
                "id": group_conv_id,
                "name": group["name"],
                "handle": group["handle"],
                "type": "group",
                "members": group["members"],
                "group_id": group_id,
                "created_by": group.get("created_by"),
                "created_by_name": group.get("created_by_name", "")
            })

    return {
        "enabled": TEAM_CHAT_ENABLED,
        "conversations": conversations,
        "members": public_member_list()
    }


@app.get("/chat/messages")
def get_chat_messages(
    conversation_id: str,
    after_id: Optional[str] = None,
    user_data: dict = Depends(get_user)
):
    if not TEAM_CHAT_ENABLED:
        return {"enabled": False, "messages": []}

    if not can_access_conversation(user_data["member_id"], conversation_id):
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation.")

    clear_after_id = CHAT_CLEARED_AT.get(user_data["member_id"], {}).get(conversation_id)
    conversation_messages = [m for m in TEAM_CHAT_MESSAGES if m["conversation_id"] == conversation_id]
    if clear_after_id:
        ids = [m.get("id") for m in conversation_messages]
        if clear_after_id in ids:
            conversation_messages = conversation_messages[ids.index(clear_after_id) + 1:]
    messages = [get_chat_message_view(m) for m in conversation_messages]

    if after_id:
        ids = [m["id"] for m in messages]
        if after_id in ids:
            messages = messages[ids.index(after_id) + 1:]

    return {"enabled": True, "messages": messages[-200:]}


@app.delete("/chat/conversations/{conversation_id}")
def delete_chat_conversation(
    conversation_id: str,
    user_data: dict = Depends(get_user)
):
    """Clear this conversation for the requesting member only.

    This never deletes messages for other participants. The conversation stays
    available so the member can continue chatting normally. New messages sent
    or received after the clear point are visible again.
    """
    member_id = user_data["member_id"]
    if not can_access_conversation(member_id, conversation_id):
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation.")

    existing = [m for m in TEAM_CHAT_MESSAGES if m.get("conversation_id") == conversation_id]
    last_message_id = existing[-1].get("id") if existing else ""
    cleared_at = now_iso()
    CHAT_CLEARED_AT.setdefault(member_id, {})[conversation_id] = last_message_id
    # Keep the older hide structure harmless for backward-compatible state files.
    CHAT_DELETED_FOR.setdefault(member_id, set()).discard(conversation_id)
    if last_message_id:
        CHAT_READ_UP_TO.setdefault(member_id, {})[conversation_id] = last_message_id
    persist_server_state()
    record_event(
        "chat_deleted_for_me",
        member_display(user_data["info"]),
        conversation_id,
        {"conversation_id": conversation_id, "cleared_at": cleared_at, "last_message_id": last_message_id}
    )
    return {"status": "deleted_for_me", "conversation_id": conversation_id, "cleared_at": cleared_at}


@app.get("/chat/notifications")
def get_chat_notifications(user_data: dict = Depends(get_user)):
    """Return WhatsApp-style unread counts and recent notifications for one user.
    A message is unread until the user opens that conversation (or explicitly
    marks it read). Mentions are detected anywhere in the message body.
    """
    member_id = user_data["member_id"]
    deleted = CHAT_DELETED_FOR.get(member_id, set())
    cleared = CHAT_CLEARED_AT.get(member_id, {})
    read = CHAT_READ_UP_TO.setdefault(member_id, {})
    unread = {}
    notifications = []

    for m in TEAM_CHAT_MESSAGES:
        cid = m.get("conversation_id")
        sender = m.get("sender_id")
        if not cid or sender in (member_id, "codechat-ai"):
            continue
        if cid in deleted or not can_access_conversation(member_id, cid):
            continue
        clear_id = cleared.get(cid)
        if clear_id:
            ids = [x.get("id") for x in TEAM_CHAT_MESSAGES if x.get("conversation_id") == cid]
            if clear_id in ids and m.get("id") in ids and ids.index(m.get("id")) <= ids.index(clear_id):
                continue
        last_read = read.get(cid, "")
        if last_read:
            ids = [x.get("id") for x in TEAM_CHAT_MESSAGES if x.get("conversation_id") == cid]
            if m.get("id") in ids and last_read in ids and ids.index(m.get("id")) <= ids.index(last_read):
                continue
        unread[cid] = unread.get(cid, 0) + 1
        mentioned = member_id != "host" and bool(re.search(r"@(?:" + re.escape(user_data["info"].get("handle", "")) + r"|" + re.escape(member_id) + r")\b", str(m.get("message", "")), re.IGNORECASE))
        if member_id == "host":
            mentioned = bool(re.search(r"@(?:host|" + re.escape(HOST_NAME.replace(" ", "_")) + r")\b", str(m.get("message", "")), re.IGNORECASE))
        if mentioned or len(notifications) < 30:
            notifications.append({
                "id": m.get("id"), "conversation_id": cid,
                "sender": m.get("sender", "Unknown"), "handle": m.get("handle", ""),
                "message": m.get("message", ""), "timestamp": m.get("timestamp", ""),
                "mentioned": mentioned
            })

    # Only active conversations need unread badges; cap payload size.
    return {"unread": unread, "notifications": notifications[-30:]}


class ChatReadRequest(BaseModel):
    conversation_id: str
    message_id: Optional[str] = None


@app.post("/chat/read")
def mark_chat_read(req: ChatReadRequest, user_data: dict = Depends(get_user)):
    member_id = user_data["member_id"]
    cid = req.conversation_id
    if not can_access_conversation(member_id, cid):
        raise HTTPException(status_code=403, detail="You are not a participant in this conversation.")
    ids = [m.get("id") for m in TEAM_CHAT_MESSAGES if m.get("conversation_id") == cid]
    message_id = req.message_id or (ids[-1] if ids else "")
    if message_id:
        CHAT_READ_UP_TO.setdefault(member_id, {})[cid] = message_id
        persist_server_state()
    return {"status": "read", "conversation_id": cid, "message_id": message_id}


@app.post("/chat/messages")
def post_chat_message(
    req: ChatMessageRequest,
    user_data: dict = Depends(get_user)
):
    if not TEAM_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Team Chat is disabled by the Host.")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Message is too long (max 4000 characters).")

    sender_id = user_data["member_id"]
    sender_name = member_display(user_data["info"])
    sender_handle = user_data["info"].get("handle", "host")

    # @ai is a chat-local AI command. It NEVER writes into the My AI/project
    # conversation. The current team/DM/group conversation is preserved.
    ai_match = re.match(r"^\s*@ai\b(.*)$", text, flags=re.DOTALL | re.IGNORECASE)
    if ai_match:
        ai_prompt = ai_match.group(1).strip()
        if not ai_prompt:
            raise HTTPException(status_code=400, detail="Use @ai followed by your question.")

        conversation_id = req.conversation_id or "team"
        if not can_access_conversation(sender_id, conversation_id):
            raise HTTPException(status_code=403, detail="You do not have access to this conversation.")
        if not TEAM_BRAIN_READY or not brain.chunks:
            answer = "🧠 Team Brain is not ready yet. Ask the Host to initialize it."
        else:
            session_key = f"{sender_id}:{conversation_id}"
            history = CHAT_AI_SESSIONS.setdefault(session_key, [])
            with BRAIN_LOCK:
                _set_brain_workspace_context()
                answer, _ = brain.ask_question(ai_prompt, history=history, is_public=False)

        user_message = {
            "id": uuid.uuid4().hex,
            "conversation_id": conversation_id,
            "session_id": CURRENT_SESSION_ID,
            "sender_id": sender_id,
            "sender": sender_name,
            "handle": sender_handle,
            "message": text,
            "timestamp": now_iso(),
            "mentions": ["ai"]
        }
        TEAM_CHAT_MESSAGES.append(user_message)
        record_activity()

        ai_message = {
            "id": uuid.uuid4().hex,
            "conversation_id": conversation_id,
            "session_id": CURRENT_SESSION_ID,
            "sender_id": "codechat-ai",
            "sender": "CodeChat AI",
            "handle": "ai",
            "message": answer,
            "timestamp": now_iso(),
            "mentions": []
        }
        TEAM_CHAT_MESSAGES.append(ai_message)
        if len(TEAM_CHAT_MESSAGES) > 5000:
            del TEAM_CHAT_MESSAGES[:len(TEAM_CHAT_MESSAGES) - 5000]
        record_activity()

        return {
            "status": "ai_replied",
            "message": get_chat_message_view(ai_message),
            "user_message": get_chat_message_view(user_message)
        }

    routed_conversation = None
    if re.match(r"^\s*@", text):
        routed_conversation, _ = parse_route(text, sender_id)

    conversation_id = routed_conversation or req.conversation_id or "team"
    if not can_access_conversation(sender_id, conversation_id):
        raise HTTPException(status_code=403, detail="You do not have access to this conversation.")

    mentions = sorted(set(m.lower() for m in re.findall(r"@([A-Za-z0-9_]+)", text)))
    message = {
        "id": uuid.uuid4().hex,
        "conversation_id": conversation_id,
        "session_id": CURRENT_SESSION_ID,
        "sender_id": sender_id,
        "sender": sender_name,
        "handle": sender_handle,
        "message": text,
        "timestamp": now_iso(),
        "mentions": mentions
    }

    TEAM_CHAT_MESSAGES.append(message)
    if len(TEAM_CHAT_MESSAGES) > 5000:
        TEAM_CHAT_MESSAGES.pop(0)
    record_activity()

    return {"status": "sent", "message": get_chat_message_view(message)}


@app.post("/chat/groups")
def create_group(
    req: GroupCreateRequest,
    user_data: dict = Depends(get_user)
):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required.")
    if len(name) > 40:
        raise HTTPException(status_code=400, detail="Group name is too long.")

    creator = user_data["member_id"]
    members = set(req.member_ids or [])
    members.add(creator)

    for member_id in members:
        if member_id != "host" and not member_by_id(member_id):
            raise HTTPException(status_code=404, detail=f"Unknown team member: {member_id}")

    group_id = uuid.uuid4().hex[:12]
    base = re.sub(r"[^a-zA-Z0-9_]", "", name.lower().replace(" ", "_")) or "group"
    used = {g.get("handle") for g in TEAM_GROUPS.values()}
    handle = base[:20]
    i = 2
    while handle in used:
        handle = f"{base[:17]}_{i}"
        i += 1

    TEAM_GROUPS[group_id] = {
        "group_id": group_id,
        "name": name,
        "handle": handle,
        "members": sorted(members),
        "created_by": creator,
        "created_by_name": member_display(user_data["info"]),
        "created_at": now_iso()
    }

    record_activity()
    record_event(
        "group_created",
        member_display(user_data["info"]),
        name,
        {"group_id": group_id, "handle": handle}
    )

    return {
        "status": "created",
        "group": TEAM_GROUPS[group_id]
    }


@app.post("/chat/groups/{group_id}/members")
def add_group_member(
    group_id: str,
    req: GroupMemberRequest,
    user_data: dict = Depends(get_user)
):
    group = TEAM_GROUPS.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found.")

    actor = user_data["member_id"]
    if actor != "host" and actor != group.get("created_by"):
        raise HTTPException(status_code=403, detail="Only the Host or group creator can manage group members.")

    if req.member_id != "host" and not member_by_id(req.member_id):
        raise HTTPException(status_code=404, detail="Team member not found.")

    if req.member_id not in group["members"]:
        group["members"].append(req.member_id)
        record_event("group_member_added", member_display(user_data["info"]), member_display(member_by_id(req.member_id) or {"name": req.member_id}), {"group_id": group_id, "group": group.get("name")})
        persist_server_state()
    return {"status": "added", "group": group}


@app.delete("/chat/groups/{group_id}/members/{member_id}")
def remove_group_member(
    group_id: str,
    member_id: str,
    user_data: dict = Depends(get_user)
):
    group = TEAM_GROUPS.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found.")

    actor = user_data["member_id"]
    if actor != "host" and actor != group.get("created_by"):
        raise HTTPException(status_code=403, detail="Only the Host or group creator can manage group members.")

    if member_id == group.get("created_by"):
        raise HTTPException(status_code=400, detail="Group creator cannot be removed from the group.")

    if member_id in group["members"]:
        group["members"].remove(member_id)
        record_event("group_member_removed", member_display(user_data["info"]), member_id, {"group_id": group_id, "group": group.get("name")})
        persist_server_state()
    return {"status": "removed", "group": group}


@app.post("/chat/groups/{group_id}/leave")
def leave_group(
    group_id: str,
    user_data: dict = Depends(get_user)
):
    """Leave a group voluntarily. The Host/group creator cannot be forcibly
    removed as creator; if the creator leaves, ownership transfers to the
    first remaining member. If nobody remains, the group is deleted."""
    group = TEAM_GROUPS.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found.")

    member_id = user_data["member_id"]
    if member_id not in group.get("members", []):
        raise HTTPException(status_code=403, detail="You are not a member of this group.")

    group["members"].remove(member_id)
    old_creator = group.get("created_by")
    if member_id == old_creator:
        if group["members"]:
            group["created_by"] = group["members"][0]
            new_owner = member_by_id(group["members"][0]) or (host_info() if group["members"][0] == "host" else {})
            group["created_by_name"] = member_display(new_owner) if new_owner else group["members"][0]
            record_event("group_owner_transferred", member_display(user_data["info"]), group.get("name", ""), {"group_id": group_id, "new_owner": group["created_by"]})
        else:
            del TEAM_GROUPS[group_id]
            record_event("group_deleted_after_last_member_left", member_display(user_data["info"]), group.get("name", ""), {"group_id": group_id})
            persist_server_state()
            return {"status": "group_deleted", "group_id": group_id}

    record_event("group_left", member_display(user_data["info"]), group.get("name", ""), {"group_id": group_id})
    persist_server_state()
    return {"status": "left", "group": group}


@app.delete("/chat/groups/{group_id}")
def delete_group(
    group_id: str,
    user_data: dict = Depends(get_user)
):
    """Delete the entire group. Only the Host or group creator may do this."""
    group = TEAM_GROUPS.get(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found.")

    actor = user_data["member_id"]
    if actor != "host" and actor != group.get("created_by"):
        raise HTTPException(status_code=403, detail="Only the Host or group creator can delete this group.")

    group_name = group.get("name", "Group")
    del TEAM_GROUPS[group_id]
    record_event("group_deleted", member_display(user_data["info"]), group_name, {"group_id": group_id})
    persist_server_state()
    return {"status": "deleted", "group_id": group_id}


# ============================================================
# SERVER START / INITIALIZATION
# ============================================================

def initialize_server():
    """Initialize persistent state exactly once for both source and PyInstaller runs.
    The GUI launches this module through ``main.py --server``, so server.py's
    ``__main__`` block is not executed in that path.  The previous build therefore
    skipped session initialization entirely.
    """
    global SERVER_INITIALIZED
    with SERVER_INIT_LOCK:
        if SERVER_INITIALIZED:
            return
        load_server_state()
        # IMPORTANT: every CodeChat launch is a fresh AI session. Persistent
        # membership/history metadata may be retained for the team UI, but the
        # Team Brain and all AI conversation memory are intentionally discarded.
        # A saved .brain/.ccsession becomes active only through an explicit Load.
        global TEAM_BRAIN_READY, BRAIN_VERSION, TEAM_MODE, CHAT_AI_SESSIONS, USER_SESSIONS
        brain.chunks = []
        brain.sources = []
        brain.embeddings = []
        brain.source_order = []
        brain.local_history = []
        brain.workspace_context = {}
        TEAM_BRAIN_READY = False
        BRAIN_VERSION = 0
        # TEAM_MODE must be reset as part of the fresh-server boundary. Without
        # declaring it global this assignment would create a local variable and
        # accidentally leave the persisted previous mode active.
        TEAM_MODE = "single"
        USER_SESSIONS = {}
        CHAT_AI_SESSIONS = {}
        start_new_live_session()
        SERVER_INITIALIZED = True


if __name__ == "__main__":
    initialize_server()
    print("\n" + "=" * 55)
    print("       🚀 CODECHAT TEAM SERVER")
    print("       VERSION 42.0")
    print("=" * 55 + "\n")

    print("🧹 Fresh AI session: no previous Brain or AI conversation was restored.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
