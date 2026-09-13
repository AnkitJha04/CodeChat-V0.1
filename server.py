import uvicorn

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
TEAM_GROUPS = {}

HOST_TOKEN = os.environ.get("CODECHAT_HOST_TOKEN", "")
HOST_NAME = os.environ.get("CODECHAT_HOST_NAME", "Host").strip() or "Host"

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


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def record_event(event_type, actor, target=None, details=None):
    TEAM_EVENTS.append({
        "type": event_type,
        "actor": actor,
        "target": target,
        "details": details or {},
        "timestamp": now_iso()
    })
    if len(TEAM_EVENTS) > 200:
        TEAM_EVENTS.pop(0)


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
    return {"status": "ok", "version": "15.0"}


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

    return {
        "status": "Invite generated",
        "token": token,
        "role": role,
        "invited_name": name,
        "invited_by": member_display(user_data["info"])
    }


@app.get("/check_role")
def check_role(x_access_token: str = Header(...)):
    # An invitation token becomes a member on first successful use.
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
            "joined_at": now_iso()
        }
        USER_SESSIONS[x_access_token] = []

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

    return {
        "status": "removed",
        "member_id": req.member_id,
        "name": target_name,
        "removed_by": member_display(user_data["info"])
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
        "events": TEAM_EVENTS[-50:]
    }


@app.post("/set_mode")
def set_mode(mode_data: dict, user_data: dict = Depends(require_host)):
    global TEAM_MODE

    mode = mode_data.get("mode")
    if mode not in ("single", "append"):
        raise HTTPException(
            status_code=400,
            detail="Mode must be 'single' or 'append'."
        )

    TEAM_MODE = mode
    record_event(
        "mode_changed",
        member_display(user_data["info"]),
        None,
        {"mode": mode}
    )

    print(f"🔧 TEAM MODE CHANGED → {TEAM_MODE.upper()}")
    return {"status": "Mode updated", "mode": TEAM_MODE}


# ============================================================
# HOST BRAIN SYNC
# ============================================================

@app.post("/sync_brain")
def sync_brain(payload: SyncPayload, user_data: dict = Depends(require_host)):
    global BRAIN_VERSION, TEAM_BRAIN_READY

    print("⚡ HOST BRAIN SYNC REQUEST RECEIVED")

    try:
        file_bytes = base64.b64decode(payload.b64_data)

        with open("server_brain.brain", "wb") as f:
            f.write(file_bytes)

        res = brain.load_snapshot("server_brain.brain")
        if "Success" not in res:
            raise HTTPException(status_code=500, detail=f"Failed to load: {res}")

        BRAIN_VERSION += 1
        TEAM_BRAIN_READY = True

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

    res = brain.save_snapshot("server_download.brain")

    if "Success" in res and os.path.exists("server_download.brain"):
        return FileResponse(
            "server_download.brain",
            filename="codechat_brain.brain"
        )

    raise HTTPException(
        status_code=500,
        detail="Could not generate brain snapshot"
    )


# ============================================================
# QUERY / TEAM AI STREAM
# ============================================================

@app.post("/query")
def query_brain(q: Query, user_data: dict = Depends(get_user)):
    token = user_data["token"]
    name = member_display(user_data["info"])

    if not TEAM_BRAIN_READY or len(brain.chunks) == 0:
        return {
            "answer": "⚠️ Team Brain is empty. Ask the Host to load code.",
            "sources": []
        }

    if token not in USER_SESSIONS:
        USER_SESSIONS[token] = []

    ans, srcs = brain.ask_question(
        q.text,
        history=USER_SESSIONS[token]
    )

    if q.public:
        TEAM_HISTORY.append({
            "user": name,
            "query": q.text,
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

    if role != "collaborator":
        raise HTTPException(
            status_code=403,
            detail="Only Collaborators can upload code."
        )

    # Important: a collaborator can never create the first Team Brain.
    if not TEAM_BRAIN_READY:
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

    try:
        tuples = [(c.text, c.source) for c in req.chunks]

        result = brain.ingest_remote_data(
            tuples,
            lambda x: print(f"-> {x}"),
            append_mode=append_mode
        )

        save_result = brain.save_snapshot("server_brain.brain")
        if "Success" not in save_result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not save Team Brain: {save_result}"
            )

        BRAIN_VERSION += 1
        TEAM_BRAIN_READY = True

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


# ============================================================
# HOST AI LOG
# ============================================================

@app.post("/host_log")
def log_host_activity(log: HostLog, user_data: dict = Depends(require_host)):
    TEAM_HISTORY.append({
        "user": member_display(user_data["info"]),
        "query": log.query,
        "answer": log.answer,
        "timestamp": now_iso()
    })

    if len(TEAM_HISTORY) > 50:
        TEAM_HISTORY.pop(0)

    return {"status": "logged"}


@app.get("/team_activity")
def get_team_activity(user_data: dict = Depends(get_user)):
    return {
        "history": TEAM_HISTORY,
        "events": TEAM_EVENTS[-100:]
    }


# ============================================================
# ACTIVE USERS
# ============================================================

@app.get("/active_users")
def get_active_users(user_data: dict = Depends(get_user)):
    cleanup_inactive()

    active_list = []
    for member in public_member_list():
        if member["member_id"] == "host" or member["online"]:
            active_list.append(member)

    return {"users": active_list}


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

    conversations = [{
        "id": "team",
        "name": "Team",
        "type": "team",
        "members": conversation_members("team")
    }]

    for info in ACCESS_TOKENS.values():
        if info.get("status") != "active":
            continue
        other_id = info["member_id"]
        if other_id == member_id:
            continue
        conversations.append({
            "id": make_dm_id(member_id, other_id),
            "name": member_display(info),
            "handle": info.get("handle", ""),
            "type": "dm",
            "member_id": other_id
        })

    for group_id, group in TEAM_GROUPS.items():
        if member_id in group.get("members", []):
            conversations.append({
                "id": f"group:{group_id}",
                "name": group["name"],
                "handle": group["handle"],
                "type": "group",
                "members": group["members"],
                "group_id": group_id
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

    messages = [
        get_chat_message_view(m)
        for m in TEAM_CHAT_MESSAGES
        if m["conversation_id"] == conversation_id
    ]

    if after_id:
        ids = [m["id"] for m in messages]
        if after_id in ids:
            messages = messages[ids.index(after_id) + 1:]

    return {"enabled": True, "messages": messages[-200:]}


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

    routed_conversation = None
    display_text = text

    # A leading @mention controls routing.
    if re.match(r"^\s*@", text):
        routed_conversation, _ = parse_route(text, sender_id)

    conversation_id = routed_conversation or req.conversation_id or "team"

    if not can_access_conversation(sender_id, conversation_id):
        raise HTTPException(status_code=403, detail="You do not have access to this conversation.")

    mentions = []
    for mention in re.findall(r"@([A-Za-z0-9_]+)", text):
        mentions.append(mention.lower())

    message = {
        "id": uuid.uuid4().hex,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "sender": sender_name,
        "handle": sender_handle,
        "message": display_text,
        "timestamp": now_iso(),
        "mentions": sorted(set(mentions))
    }

    TEAM_CHAT_MESSAGES.append(message)
    if len(TEAM_CHAT_MESSAGES) > 2000:
        TEAM_CHAT_MESSAGES.pop(0)

    return {
        "status": "sent",
        "message": get_chat_message_view(message)
    }


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

    return {"status": "removed", "group": group}


# ============================================================
# SERVER START
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("       🚀 CODECHAT TEAM SERVER")
    print("       VERSION 15.0")
    print("=" * 55 + "\n")

    if os.path.exists("server_brain.brain"):
        print("📂 Found saved Team Brain, loading...")
        res = brain.load_snapshot("server_brain.brain")
        if "Success" in res:
            TEAM_BRAIN_READY = True
            BRAIN_VERSION = 1
            print(f"✅ Team Brain loaded | {len(brain.chunks)} chunks")

    uvicorn.run(app, host="0.0.0.0", port=8000)
