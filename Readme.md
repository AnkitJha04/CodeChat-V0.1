# 🤖 CodeChat Pro — Team Edition

> **Privacy-first local RAG desktop application for secure AI-assisted team collaboration.**

![Version](https://img.shields.io/badge/version-15.0-blue.svg)
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
- [Team Audit and Activity](#-team-audit-and-activity)
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
- 📊 Team activity and audit tracking
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
- Team activity
- Team audit events
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

# 📄 License

License information is currently **TBD**.

If this project is published publicly, add an appropriate license such as:

```text
MIT
Apache-2.0
GPL-3.0
```

---

# ⭐ CodeChat Pro

> **Build locally. Collaborate securely. Keep your knowledge yours.**

**CodeChat Pro — Team Edition** combines local AI, RAG, team collaboration, and human communication into a single desktop workspace.
