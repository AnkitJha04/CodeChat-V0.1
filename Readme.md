# 🤖 CodeChat Pro — Team Edition

> **Privacy-first local RAG desktop application for secure AI-assisted team collaboration.**

![Version](https://img.shields.io/badge/version-35.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)
![GUI](https://img.shields.io/badge/GUI-PyQt6-green.svg)
![Backend](https://img.shields.io/badge/Backend-FastAPI-red.svg)
![AI](https://img.shields.io/badge/AI-Ollama%20%7C%20Llama%203.1-purple.svg)
![License](https://img.shields.io/badge/license-TBD-lightgrey.svg)

---

## 📌 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [System Architecture](#-system-architecture)
- [Project Structure](#-project-structure)
- [Technology Stack](#-technology-stack)
- [How CodeChat Works](#-how-codechat-works)
- [Single Mode](#-single-mode)
- [Team Mode](#-team-mode)
- [Team Brain](#-team-brain)
- [Team Roles and Permissions](#-team-roles-and-permissions)
- [Invitation and Membership System](#-invitation-and-membership-system)
- [History and Privacy](#-history-and-privacy)
- [Team Chat](#-team-chat)
- [AI Chat vs Team Chat](#-ai-chat-vs-team-chat)
- [Chat Routing](#-chat-routing)
- [Security Model](#-security-model)
- [Sessions and Persistence](#-sessions-and-persistence)
- [Voice Mode](#-voice-mode)
- [Remote Access](#-remote-access)
- [Installation](#-installation)
- [Running the Application](#-running-the-application)
- [Building the Executable](#-building-the-executable)
- [Testing](#-testing)
- [Troubleshooting](#-troubleshooting)
- [Future Improvements](#-future-improvements)
- [Project Status](#-project-status)
- [Author](#-author)

---

# 🔎 Overview

**CodeChat Pro — Team Edition** is a desktop-based AI workspace designed to combine:

- 🧠 Local Retrieval-Augmented Generation (RAG)
- 🤖 Local LLM inference through Ollama
- 📚 Private document knowledge bases
- 👥 Multi-user team collaboration
- 💬 Human-to-human team communication
- 🔐 Token-based team authentication
- 📊 Durable team history and activity tracking
- 🎙️ Voice interaction
- 🌐 Optional remote collaboration through ngrok

The primary design principle is:

> **Keep AI processing local whenever possible while allowing controlled team collaboration.**

CodeChat supports both a **private Single Mode** and a **shared Team Mode** without requiring the user to restart the application when switching modes.

---

# ✨ Key Features

## 🧠 Local RAG

CodeChat can build a local knowledge base from documents and use that knowledge when answering questions.

Core capabilities include:

- Document ingestion
- Text chunking
- Embedding generation
- Vector similarity search
- Context retrieval
- Local LLM generation
- Source-aware answers
- Local conversation history

---

## 👤 Single Mode

Single Mode is intended for individual/private use.

The user can:

- Create a project
- Load documents
- Build a local Brain
- Ask questions
- Save/load Brain data
- Save/load sessions
- Use voice interaction

No team member needs to be connected.

---

## 👥 Team Mode

Team Mode allows multiple users to collaborate around a shared Team Brain.

It provides:

- Host and collaborator roles
- Guest access
- Invitations
- Member removal
- Member self-leave
- Shared Team Brain
- Live Team Stream (public AI activity only)
- Read-only durable History tab
- Human team chat
- Direct messages
- Group conversations
- Chat enable/disable controls

---

# 🏗️ System Architecture

```text
                         ┌─────────────────────┐
                         │    CodeChat GUI     │
                         │      PyQt6          │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │      backend.py     │
                         │ CoreBrain / Remote  │
                         │       Brain         │
                         └───────┬───────┬─────┘
                                 │       │
                    Local Mode   │       │ Team Mode
                                 │       │
                                 ▼       ▼
                    ┌──────────────┐  ┌─────────────────┐
                    │   Ollama     │  │   FastAPI       │
                    │ Llama 3.1    │  │    Server       │
                    └──────┬───────┘  └────────┬────────┘
                           │                    │
                           ▼                    ▼
                    ┌──────────────┐    ┌───────────────┐
                    │ Local Brain  │    │  Team Brain   │
                    │ Vector Store │    │ Shared State  │
                    └──────────────┘    └───────┬───────┘
                                                │
                                     ┌──────────┴──────────┐
                                     │                     │
                                     ▼                     ▼
                                Team Members          Team Chat
```

---

# 📁 Project Structure

```text
CodeChat/
│
├── main.py
├── backend.py
├── server.py
├── styles.py
├── ollama_setup.py
│
├── server_brain.brain
├── sessions/
├── projects/
│
├── README.md
└── requirements.txt
```

### File Responsibilities

| File | Responsibility |
|---|---|
| `main.py` | Main PyQt6 desktop GUI and application controller |
| `backend.py` | Local and remote Brain abstractions |
| `server.py` | FastAPI Team Mode backend |
| `styles.py` | GUI styling |
| `ollama_setup.py` | Ollama installation/setup and model management |
| `server_brain.brain` | Server-side Team Brain storage |
| `README.md` | Project documentation |

---

# 🧰 Technology Stack

| Layer | Technology |
|---|---|
| GUI | PyQt6 |
| Backend API | FastAPI |
| ASGI Server | Uvicorn |
| Local LLM | Ollama |
| Model | Llama 3.1 |
| Embeddings | NumPy-based vector processing |
| Voice Input | SpeechRecognition / PyAudio |
| Networking | HTTP / REST |
| Remote Tunnel | ngrok |
| Language | Python |
| Packaging | PyInstaller-compatible architecture |

---

# ⚙️ How CodeChat Works

The application follows a simple RAG pipeline:

```text
Document
   │
   ▼
Text Extraction
   │
   ▼
Chunking
   │
   ▼
Embedding Generation
   │
   ▼
Vector Store
   │
   ▼
User Question
   │
   ▼
Similarity Search
   │
   ▼
Relevant Context
   │
   ▼
Llama 3.1
   │
   ▼
Answer
```

The LLM does not need to independently know the user's documents.

Instead, CodeChat retrieves relevant information and supplies it to the model as context.

---

# 👤 Single Mode

Single Mode provides a completely private local workflow.

```text
User
 │
 ▼
PyQt6 GUI
 │
 ▼
CoreBrain
 │
 ├── Documents
 ├── Embeddings
 ├── Vector Store
 └── Conversation History
 │
 ▼
Ollama
 │
 ▼
Llama 3.1
```

### Typical Workflow

1. Start CodeChat.
2. Create or open a project.
3. Load documents.
4. Build the local Brain.
5. Ask questions.
6. Retrieve relevant document context.
7. Generate the answer locally.
8. Save the Brain/session if required.

---

# 👥 Team Mode

Team Mode introduces a central server that acts as the authority for shared team state.

```text
                    ┌──────────────────┐
                    │   Team Server    │
                    │     FastAPI      │
                    └────────┬─────────┘
                             │
             ┌───────────────┼────────────────┐
             │               │                │
             ▼               ▼                ▼
          Host           Member A          Member B
             │               │                │
             └───────────────┼────────────────┘
                             │
                             ▼
                       Shared Team Brain
```

The server is authoritative for:

- Team membership
- Permissions
- Team Brain state
- Team Brain version
- Team Chat state
- Audit events
- Member presence
- Chat routing

---

# 🧠 Team Brain

The **Team Brain** is the shared knowledge base used by Team Mode.

## Initialization Protection

A critical rule is enforced by the server:

> **Collaborators cannot upload documents before the Host initializes the Team Brain.**

This prevents a member from accidentally creating or modifying an uninitialized shared knowledge base.

### Initial State

```text
Team Created
     │
     ▼
Team Brain NOT READY
     │
     ├── Host → Can initialize Brain
     │
     └── Collaborator → Upload rejected
```

### After Initialization

```text
Team Brain READY
     │
     ├── Single Mode → New upload replaces Brain
     │
     └── Append Mode → New upload appends to Brain
```

The server determines the actual behavior rather than trusting the client.

---

# 🔄 Team Brain Modes

## Replace Mode

In Replace Mode, a new Host/collaborator upload replaces the existing Team Brain.

```text
Old Brain
   │
   ▼
New Upload
   │
   ▼
New Team Brain
```

Useful when the team wants to completely refresh its knowledge base.

---

## Append Mode

In Append Mode, newly uploaded content is added to the existing Team Brain.

```text
Existing Brain
      │
      ├── Document A
      ├── Document B
      │
      ▼
New Upload
      │
      ▼
Updated Brain
      ├── Document A
      ├── Document B
      └── Document C
```

---

# 🛡️ Team Roles and Permissions

CodeChat uses three main roles.

| Capability | Host | Collaborator | Guest |
|---|:---:|:---:|:---:|
| Query Team Brain | ✅ | ✅ | ✅ |
| Upload documents | ✅ | ✅ | ❌ |
| Invite members | ✅ | ✅ | ❌ |
| Remove members | ✅ | ❌ | ❌ |
| Leave team | ❌ | ✅ | ✅ |
| View team activity | ✅ | ✅ | ✅ |
| Team Chat | ✅ | ✅ | ✅ |
| Create groups | ✅ | ✅ | ✅ |
| Manage Chat ON/OFF | ✅ | ❌ | ❌ |
| Change Brain mode | ✅ | ❌ | ❌ |
| Manage Team Brain | ✅ | Limited | ❌ |
| Audit authority | ✅ | ❌ | ❌ |

### Host

The Host has ultimate authority over the team.

The Host can:

- Change Team Brain mode
- Initialize the Team Brain
- Manage Brain state
- Enable/disable Team Chat
- Remove members
- View administrative events
- Invite members

### Collaborator

Collaborators can actively contribute to the team.

They can:

- Query the Team Brain
- Upload documents
- Invite members
- Use Team Chat
- Create/manage their own groups
- Leave the team

### Guest

Guests have limited access.

They can:

- Query the Team Brain
- Participate in Team Chat
- Use conversations
- Leave the team

They cannot:

- Upload documents
- Invite members
- Change team settings
- Remove members

---

# ✉️ Invitation and Membership System

Invitations are generated by authorized team members.

```text
Authorized Member
       │
       ▼
Generate Invite
       │
       ▼
Pending Invite Token
       │
       ▼
New User
       │
       ▼
Join Team
       │
       ▼
Active Team Member
```

The invitation records who created the invite.

The system also records important membership events such as:

- Invitation created
- Member joined
- Member removed
- Member left

---

# 🚫 Member Removal

The Host can remove a member at any time.

When a member is removed:

```text
Host
 │
 ▼
Remove Member
 │
 ├── Membership invalidated
 ├── Access token invalidated
 ├── Session invalidated
 ├── Presence removed
 └── Audit event created
```

The removed user's existing token is no longer accepted by the server.

This means authorization is enforced server-side rather than only through the GUI.

---

# 🚪 Leaving the Team

A Collaborator or Guest can leave the team themselves.

```text
Member
  │
  ▼
Leave Team
  │
  ├── Membership removed
  ├── Access invalidated
  └── Audit event recorded
```

The Host cannot leave using the normal member-leave operation because the team requires an authoritative Host.

---

# 📜 Team Audit and Activity

The server maintains Team events to provide visibility into important actions.

Examples include:

```text
[JOIN]      Member joined the team
[INVITE]    Member invited another user
[REMOVE]    Host removed a member
[LEAVE]     Member left the team
[BRAIN]     Team Brain changed
[MODE]      Team Brain mode changed
[CHAT]      Team Chat settings changed
```

Team activity is displayed through the Team Stream.

---

# 💬 Team Chat

Team Chat is intentionally separate from AI Chat.

It is a **human-to-human communication system**.

### Supported Conversation Types

- 🌐 Team-wide conversation
- 👤 Direct messages
- 👥 Group conversations

---

# 🤖 AI Chat vs Team Chat

| Feature | AI Chat | Team Chat |
|---|---|---|
| Uses Ollama | ✅ | ❌ |
| Uses Llama 3.1 | ✅ | ❌ |
| Uses RAG | ✅ | ❌ |
| Human messages | ❌ | ✅ |
| Team communication | ❌ | ✅ |
| Document context | ✅ | ❌ |
| DMs | ❌ | ✅ |
| Group chat | ❌ | ✅ |
| `@team` routing | ❌ | ✅ |

This separation is deliberate.

> **Team Chat never sends human messages to Ollama.**

---

# 🧭 Chat Routing

CodeChat supports simple mention-based routing.

## Team Message

```text
@team hello everyone
```

Routes the message to:

```text
Entire Team
```

---

## Direct Message

```text
@ankit hello
```

Routes the message to the selected member.

```text
Sender
  │
  ▼
@ankit
  │
  ▼
Ankit's DM
```

---

## Group Message

```text
@robotics hello team
```

Routes the message to the corresponding group conversation.

---

# 👥 Group Conversations

Members can create group conversations.

A group contains:

```text
Group
├── Group Name
├── Creator
└── Members
```

Group messages are visible only to members of that group.

Group membership operations are validated by the server.

---

# 🔌 Team Chat API

The Team Chat backend exposes endpoints for:

```text
POST   /chat/settings
GET    /chat/conversations
GET    /chat/messages
POST   /chat/messages

POST   /chat/groups
POST   /chat/groups/{group_id}/members
DELETE /chat/groups/{group_id}/members/{member_id}
```

The server enforces access to conversations.

---

# 🔐 Security Model

CodeChat uses several layers of protection.

## Token Authentication

Requests to protected Team endpoints use an access token.

Conceptually:

```text
Client
 │
 │ x-access-token
 ▼
FastAPI
 │
 ▼
Authentication
 │
 ▼
Authorization
 │
 ▼
Requested Resource
```

---

## Server-Side Authorization

The GUI is **not considered a security boundary**.

Even if a malicious client attempts to call an endpoint directly, the server checks:

- Is the token valid?
- Is the user a team member?
- What role does the user have?
- Does the role permit the operation?
- Is the Team Brain initialized?
- Is Team Chat enabled?
- Is the user part of the requested conversation?

---

# 🧠 Server-Authoritative State

Important Team state is controlled by the server.

Examples:

```text
TEAM_MODE
BRAIN_VERSION
TEAM_BRAIN_READY
TEAM_CHAT_ENABLED
ACCESS_TOKENS
ACTIVE_USERS
TEAM_HISTORY
TEAM_EVENTS
TEAM_GROUPS
```

This prevents the GUI from becoming the source of truth.

---

# 🔄 Remote Workflow

A remote Team client follows this general process:

```text
Start CodeChat
      │
      ▼
Connect to Team Server
      │
      ▼
Authenticate
      │
      ▼
Retrieve Team State
      │
      ▼
Check Role / Permissions
      │
      ▼
Use Team Brain / Team Chat
```

The client periodically refreshes Team state and activity.

---

# 🌐 Remote Access with ngrok

For remote collaboration, CodeChat can expose the local FastAPI server through an ngrok tunnel.

```text
Remote Member
      │
      │ HTTPS
      ▼
    ngrok
      │
      ▼
Local FastAPI Server
      │
      ▼
Team Brain
```

This allows team members outside the local network to connect when the Host makes the server reachable through ngrok.

> **Important:** Remote access depends on the Host machine, network configuration, and ngrok availability.

---

# 💾 Sessions and Persistence

CodeChat supports saving and loading sessions.

A session can contain the user's working conversation state.

The Brain and session concepts are separate:

```text
Brain
 └── Knowledge / documents / embeddings

Session
 └── Conversation / user workflow
```

This allows users to preserve work without confusing conversation history with the underlying knowledge base.

---

# 🧠 Brain Persistence

The Team server uses:

```text
server_brain.brain
```

for the server-side Team Brain.

The server attempts to load the saved Brain when it starts.

---

# 🎙️ Voice Mode

CodeChat includes voice interaction using:

- `SpeechRecognition`
- `PyAudio`

General workflow:

```text
Microphone
    │
    ▼
Speech Recognition
    │
    ▼
Text Query
    │
    ▼
CodeChat
    │
    ▼
RAG / LLM
```

Voice input is intended as an alternative interface to normal text input.

---

# 📦 Installation

## 1. Clone or copy the project

```bash
git clone <repository-url>
cd CodeChat
```

---

## 2. Create a virtual environment

### Windows

```bash
python -m venv venv
venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

---

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

If `requirements.txt` is not available, install the major dependencies:

```bash
pip install PyQt6 fastapi uvicorn numpy requests
```

Voice functionality may additionally require:

```bash
pip install SpeechRecognition PyAudio
```

---

# 🦙 Ollama Setup

CodeChat uses Ollama for local LLM inference.

Install Ollama and make sure it is accessible from the system.

Then pull the required model:

```bash
ollama pull llama3.1
```

Verify:

```bash
ollama list
```

You should see the Llama 3.1 model available.

---

# ▶️ Running the Application

## Start CodeChat normally

```bash
python main.py
```

The application starts the desktop GUI.

---

## Server Mode

The application architecture uses the same executable/source with a server argument:

```bash
python main.py --server
```

The server process starts the FastAPI application.

This avoids requiring a separate `server.exe`.

---

# 🖥️ Single Executable Architecture

The intended packaged architecture is:

```text
                 CodeChat.exe
                      │
             ┌────────┴────────┐
             │                 │
             ▼                 ▼
        Server Process       GUI Process
             │                 │
             ▼                 ▼
          FastAPI            PyQt6
             │
             ▼
         Team Brain
```

The GUI can launch the same executable with:

```text
--server
```

instead of requiring:

```text
CodeChat.exe
server.exe
```

as two separately distributed applications.

---

# 🧪 Testing

Recommended testing sequence:

## 1. Basic Startup

```text
☐ Application starts
☐ Ollama starts/works
☐ GUI loads
☐ Server starts when required
```

---

## 2. Single Mode

```text
☐ Create project
☐ Load documents
☐ Build Brain
☐ Ask question
☐ Save Brain
☐ Load Brain
☐ Save session
☐ Load session
```

---

## 3. Team Initialization

```text
☐ Start Team Mode
☐ Verify Brain = NOT READY
☐ Try collaborator upload
☐ Confirm upload is rejected
☐ Host initializes Brain
☐ Confirm Brain = READY
```

---

## 4. Team Brain Modes

### Replace

```text
☐ Upload first dataset
☐ Upload second dataset
☐ Confirm second dataset replaces first
```

### Append

```text
☐ Upload first dataset
☐ Upload second dataset
☐ Confirm both datasets remain available
```

---

## 5. Membership

```text
☐ Host creates invite
☐ Collaborator joins
☐ Collaborator creates invite
☐ Guest joins
☐ Host removes member
☐ Removed token is rejected
☐ Collaborator leaves
```

---

## 6. Team Chat

```text
☐ Open Team Chat
☐ Send team message
☐ Send DM
☐ Create group
☐ Send group message
☐ Disable Team Chat
☐ Confirm server rejects/blocks new chat activity
☐ Re-enable Team Chat
```

---

# 🐛 Troubleshooting

## Ollama not responding

Check:

```bash
ollama list
```

Then verify the Ollama service is running.

---

## Model not found

Pull the required model:

```bash
ollama pull llama3.1
```

---

## Team Brain not ready

If a collaborator sees an initialization error, the Host must initialize the Team Brain first.

This is intentional.

---

## Collaborator cannot upload

Check:

1. User is authenticated.
2. User is a Collaborator.
3. Team Brain has already been initialized.
4. The server is reachable.
5. The access token is valid.

---

## Team Chat unavailable

Check whether the Host has disabled Team Chat.

The server intentionally enforces the setting.

---

## Removed member can no longer connect

This is expected.

Removing a member invalidates their server-side access.

---

# 🧩 API Overview

The Team server currently exposes functionality around:

### Authentication / Membership

```text
POST /generate_invite
POST /check_role
POST /members/remove
POST /leave
POST /logout
```

### Team State

```text
GET  /health
GET  /team_state
GET  /team_activity
GET  /active_users
```

### Brain

```text
POST /set_mode
POST /sync_brain
GET  /download_brain
POST /query
POST /ingest
POST /host_log
```

### Chat

```text
POST /chat/settings
GET  /chat/conversations
GET  /chat/messages
POST /chat/messages
POST /chat/groups
POST /chat/groups/{group_id}/members
DELETE /chat/groups/{group_id}/members/{member_id}
```

---

# 📊 Data Flow

## AI Query

```text
User Question
      │
      ▼
Authentication
      │
      ▼
Team Brain Check
      │
      ▼
Vector Search
      │
      ▼
Relevant Context
      │
      ▼
Llama 3.1
      │
      ▼
AI Response
```

---

## Human Chat

```text
User Message
      │
      ▼
Authentication
      │
      ▼
Mention Parsing
      │
      ├──────────► @team
      │
      ├──────────► @person
      │
      └──────────► @group
      │
      ▼
Conversation Validation
      │
      ▼
Store Message
      │
      ▼
Recipient(s)
```

No LLM is involved in this flow.

---

# 🧱 Design Philosophy

CodeChat is designed around five principles.

## 1. Local First

Use local AI infrastructure whenever possible.

## 2. Server Authoritative

Security-sensitive decisions belong on the server.

## 3. Human and AI Communication Are Separate

Team Chat should never accidentally become an AI prompt.

## 4. Role-Based Access

Different team members should have different capabilities.

## 5. One Application Experience

The user should not have to manage multiple executables just to use Team Mode.

---

# 🚀 Future Improvements

Potential future improvements include:

- Persistent team database
- Persistent Team Chat history
- Persistent group definitions
- More advanced group management UI
- Host transfer
- Multiple Hosts / administrators
- Database-backed authentication
- Stronger token generation
- Token expiration
- Encrypted communication
- HTTPS certificates
- File-level permissions
- Document versioning
- Better embedding models
- More advanced vector databases
- Message search
- Message deletion/editing
- Read receipts
- Typing indicators
- Notifications
- User profiles
- File sharing in Team Chat
- Docker deployment
- Cloud deployment
- Enterprise authentication

---

# ⚠️ Current Architectural Notes

The current implementation keeps several Team states in server memory.

In particular, Team Chat and group state are currently designed around the running server process.

Therefore, restarting the server can require re-establishing in-memory collaboration state unless persistence is added.

The current architecture is best viewed as a strong prototype / development implementation that can later be upgraded to a persistent production backend.

---

# 📈 Project Status

| Component | Status |
|---|---|
| PyQt6 GUI | ✅ Implemented |
| Local RAG | ✅ Implemented |
| Ollama integration | ✅ Implemented |
| Single Mode | ✅ Implemented |
| Team Mode | ✅ Implemented |
| Team Brain | ✅ Implemented |
| Brain initialization protection | ✅ Implemented |
| Replace / Append modes | ✅ Implemented |
| Token authentication | ✅ Implemented |
| Invitations | ✅ Implemented |
| Member removal | ✅ Implemented |
| Member self-leave | ✅ Implemented |
| Team audit events | ✅ Implemented |
| Team Chat | ✅ Implemented |
| Direct Messages | ✅ Implemented |
| Group Chat | ✅ Implemented |
| `@team` routing | ✅ Implemented |
| `@person` routing | ✅ Implemented |
| `@group` routing | ✅ Implemented |
| Chat ON/OFF | ✅ Implemented |
| Voice Mode | ✅ Implemented |
| ngrok remote access | ✅ Supported |
| Single executable architecture | 🔧 Packaging required |
| Persistent Team database | 🔮 Future |
| Production-grade encryption | 🔮 Future |

---

# 👨‍💻 Author

**Ankit Jha**

B.Tech — Automation & Robotics

Vivekanand Education Society's Institute of Technology (VESIT)

---

# ⭐ CodeChat Pro

> **Build locally. Collaborate securely. Keep your knowledge yours.**

**CodeChat Pro — Team Edition** combines local AI, RAG, team collaboration, and human communication into a single desktop workspace.


## Stability & Runtime Notes

Version 16 adds safer startup ownership, persistent Host authentication, non-blocking Team polling, atomic Remote Brain uploads, thread-safe server Brain operations, safer Brain snapshot handling, and stronger Qt6-only packaging guidance.

### Recommended Windows build

Keep PyQt5 installed if required by other projects; PyInstaller can exclude it:

```powershell
pyinstaller --clean --onefile --windowed --name CodeChat --icon=CodeChat.ico --exclude-module PyQt5 --exclude-module PySide2 --exclude-module PySide6 main.py
```

The packaged application expects Ollama and `llama3.1` to be available through the existing `ollama_setup.py`. `ngrok.exe` should be placed beside `CodeChat.exe` or available on PATH.


---

# 🚀 Stability & Brain Rules — v21

## File selection

- **Single Mode:** exactly one file may be selected per update. That file becomes the complete Team Brain.
- **Append Mode:** one or many files may be selected and are appended atomically.
- Switching **Append → Single** keeps only the newest uploaded file (all chunks belonging to that file remain) and forgets older files.
- Every destructive project/mode change asks for explicit confirmation before execution.
- Failed embedding or server persistence does not intentionally destroy the previous Brain.

## Team synchronization

The Team Server is authoritative for Team Brain state. The Host refreshes its local Brain when the server Brain version changes, preventing a collaborator's upload from silently being overwritten by a stale Host copy.

## `@ai` inside Team Chat

In the **Team Chat** tab:

- `@ai explain this code` asks CodeChat AI without writing to **My Session**.
- In a DM, `@ai ...` keeps the AI response inside that DM.
- In a group, `@ai ...` keeps the AI response inside that group.
- `@team`, `@person`, and `@group` continue to route human messages.
- CodeChat AI uses the Team Brain and workspace context (files, file count, Team Brain version, Team mode, and administrator information) when available.

This keeps human chat and the private/project AI window separate.


---

# 🕘 History, Profiles and Private Chat — v18

Version 18 makes collaboration state durable and separates live communication from historical records.

## 👤 Active User Profiles

Double-click any active user in the right-hand **ACTIVE USERS** panel. Every connected user can inspect the member profile, including:

- Display name and `@handle`
- Current role
- Online/offline status
- Member ID
- Who invited the member
- Join timestamp
- Groups the member belongs to
- Removal/leave information when applicable

The information is served by the Team Server, so it is consistent for connected users.

## 🕘 History Tab

The live **Team Stream** no longer contains the audit log. A dedicated read-only **🕘 History** tab stores collaboration sessions as:

```text
history_YYYYMMDD_HHMMSS
```

Every Team Server startup begins a new history session while retaining previous sessions for later review.

History records include, where visible to the current user:

- Invitations and who invited whom
- Member joins and timestamps
- Roles
- Member removal and self-leave events
- Brain initialization/updates and mode changes
- Team Chat messages
- `@ai` Team Chat messages and AI replies
- Public Team AI questions and answers
- Group creation and membership changes
- Other recorded collaboration activity

Selecting an older session loads that session without modifying it. The History tab is read-only.

## 🔐 Chat Privacy

Team Chat is persisted across server restarts so previous conversations remain available.

- Team/public chat is visible to connected team members.
- Group chat is visible only to members of that group.
- Direct/private messages are visible only to their two participants.
- The Host is **not** a global reader of private DMs between other members.
- The Host can only see a DM conversation when the Host is actually one of its participants.

This is enforced by the Team Server on both conversation discovery and message/history retrieval.

> The application-level Host cannot use the normal Team UI/API to inspect another member's private DM. The server state file is stored on the Host machine because this is a self-hosted architecture; operating-system/file-system access to that machine is outside the application's permission model.

## 👥 Group Creation

Any connected member who is allowed to create groups can now use a clear multi-select member picker when creating a group. The creator is automatically included, and any active teammates can be selected before confirmation.

## 💾 Durable Team State

The Team Server now persists collaboration metadata under the CodeChat application data directory, including membership metadata, invitations, Team Chat, groups, events and history sessions. Live online presence is intentionally not persisted and is rebuilt from active connections.

# v21 — Live History & CoreBrain Stability Fixes

## Live Team History
- The **🕘 History** tab is read-only.
- The current live session is named `history_YYYYMMDD_HHMMSS` and updates in real time for connected members.
- Live history contains team-wide activity, invitations, joins/leaves/removals, role changes, Team Brain changes, public AI activity and public Team Chat.
- Private DMs are never included in another member's live/history view.

## Historical Sessions
- Session files are stored only under the application's project directory in `sessions/`.
- Each session is stored as `sessions/history_YYYYMMDD_HHMMSS.json`.
- Selecting an older session changes only the local viewer; it is never broadcast to the team.
- The server checks that the requesting member participated in the selected session and filters private conversations to that member.

## Member Profiles
- Double-click an active member in the right-side Active Users panel to open their profile.
- Profile information includes role, handle, online status, invited-by information, invitation/join timestamps and group membership.

## Team Stream
- Team Stream is live/public activity only.
- Administrative audit history is not displayed there; it belongs in the read-only History tab.

## Group Creation
- Group creation uses a multi-select member picker so the creator can choose any connected teammate(s).

## Stability
- Team polling never calls `get_team_state()` on `CoreBrain`; CoreBrain and RemoteBrain are handled through separate safe paths.


## v21 — Live & Historical Session History

- Every Team Server launch creates a unique `history_YYYYMMDD_HHMMSS_microseconds` session.
- Session JSON files are stored only under the project-local `sessions/` directory beside the application.
- The live History view shows a `session_started` entry with the Host and exact start time, then updates as invites, joins, removals, Brain changes, public AI activity, groups, and Team Chat occur.
- Older sessions are immutable/read-only and selecting one changes only the requesting user's History tab.
- Private DMs are filtered server-side and never exposed through another user's history view.
- CoreBrain runtime context is defensively initialized so stale/frozen instances cannot raise `workspace_context` attribute errors.


## v21 fixes
- Live History has a dedicated `/history/live` API and updates without changing a user's archived-session selection.
- Session history starts with an explicit Host/session-start event and records invite/join/chat activity.
- Server restores the saved Team Brain during the actual `main.py --server` launch path.
- Startup rejects/restarts stale CodeChat server versions so an old server cannot shadow the current API.
- Member details resolve from the authoritative membership table, not only active presence.
- Team Chat uses the authoritative server conversation/message endpoints for live polling and sending.


# v22 — Team Chat Stability Fixes

Version 22 fixes Team Chat transport and QThread lifecycle issues. Human chat is independent of Team Brain readiness. Team, direct-message, @person, @group, @ai, and group creation operations use a dedicated chat worker so network/Ollama latency cannot block or tear down the GUI. All owned QThreads are retained and waited on during shutdown to prevent `QThread: Destroyed while thread is still running`.

### Per-user chat deletion
- Any member can use **Delete Chat for Me** in Team Chat.
- This clears the selected conversation from that member's chat/history view only.
- Other participants keep their messages and conversation.
- The same conversation remains available for future messages.

- Group controls: members can leave groups; Host/group creator can delete a group for everyone.


## v34 Brain File Invariants

- **Single Mode:** every successful upload replaces the Team Brain; only the newest uploaded file remains.
- **Append Mode:** successful uploads are appended and all uploaded files remain available to RAG.
- **Append → Single:** switching modes immediately retains only the newest existing uploaded file.
- **After Append → Single:** a later upload in Single Mode replaces that retained file, so only the new latest file remains.
- These rules are enforced by the Team Server, so client-side mode flags cannot accidentally violate them.

## v34 Fresh-Session + Source-Identity Guarantees

- Every CodeChat launch starts a **new isolated Ollama server process** on a fresh local port. CodeChat never reuses an already-running Ollama daemon.
- The isolated Ollama process uses `OLLAMA_KEEP_ALIVE=0`, one parallel request and one loaded model at a time.
- Every launch also starts a **fresh CodeChat Team Server process**. An existing CodeChat server on port 8000 is not reused.
- No previous Ollama conversation, CodeChat AI session history, Team Brain, or AI chat-session state is restored automatically.
- Saved `.brain` and `.ccsession` files remain available as explicit user-controlled restore points. Loading one is the deliberate operation that restores old evidence/chat.
- A new Brain upload or mode change clears the previous My AI context so old questions cannot contaminate the new file set.
- Embeddings use `nomic-embed-text`; the chat model remains `llama3.1`. Brain snapshots store the embedding-model identity so older compatible snapshots can be detected safely.
- Retrieval is **file-first**: it ranks logical files before selecting chunks, uses cosine similarity, rejects weak evidence, and normally limits general questions to the strongest one or two files.
- File-specific queries can use `@file filename` and are constrained to that file.
- Two contributors can upload files with the same filename without collision because the server assigns a stable logical source identity using the member ID plus filename.
- If a filename is ambiguous across contributors, CodeChat refuses to guess instead of mixing both files.
- Every retrieved context block is labeled with both its logical source and filename.
- Single Mode is server-authoritative and contains exactly the latest uploaded logical file. Append Mode retains multiple logical files in upload order. Switching Append → Single keeps only the latest file.
- Collaborators cannot create the first Team Brain; Host initialization is required.


### v35 session + mode rule
- Switching Single/Append mode NEVER starts or refreshes an AI session.
- The current My AI conversation and Ollama runtime remain alive during a mode switch.
- A fresh AI/session boundary occurs only when CodeChat is closed and started again.
- Append → Single keeps only the latest logical file, but does not erase the current conversation.
- Single → Append changes only the future upload policy; existing Brain content remains unchanged.
- Every new application launch starts a fresh isolated Ollama server and empty Team Brain unless the user explicitly loads a saved Brain/session.
