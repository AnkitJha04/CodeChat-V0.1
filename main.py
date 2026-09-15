import sys
import os
import subprocess
import time
import markdown
import speech_recognition as sr
import requests
import json
import base64
from pathlib import Path
import secrets
import zipfile
import shutil
import tempfile
import html
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextBrowser, QLineEdit, QPushButton,
    QFileDialog, QLabel, QFrame, QInputDialog, QMessageBox,
    QDialog, QRadioButton, QTextEdit, QTabWidget,
    QListWidget, QListWidgetItem, QCheckBox, QDialogButtonBox, QSystemTrayIcon, QStyle
)

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QRunnable, QThreadPool, QObject

from backend import CoreBrain, RemoteBrain
from styles import (
    PRO_STYLE,
    STATUS_LOCAL,
    STATUS_REMOTE,
    STATUS_GUEST,
    STATUS_COLLAB
)
from ollama_setup import setup_ollama, stop_ollama


TEXT_GRAY = "#888888"

# ============================================================
# SERVER PROCESS
# ============================================================

server_process = None
ngrok_process = None
public_url = None
host_token = None

# Remember team credentials locally so a returning member can sign in by
# selecting a previously joined team. The server URL + member token are the
# credential pair; no separate Team ID is required.
TEAM_LOGIN_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "CodeChat", "teams.json"
)

def load_saved_teams():
    try:
        if os.path.exists(TEAM_LOGIN_FILE):
            data = json.loads(Path(TEAM_LOGIN_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []

def save_saved_teams(teams):
    try:
        os.makedirs(os.path.dirname(TEAM_LOGIN_FILE), exist_ok=True)
        tmp = TEAM_LOGIN_FILE + ".tmp"
        Path(tmp).write_text(json.dumps(teams, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, TEAM_LOGIN_FILE)
    except Exception as e:
        print(f"Team login persistence warning: {e}")

def remember_team(url, token, name, role, member_id):
    teams = load_saved_teams()
    key = url.rstrip("/").lower()
    teams = [t for t in teams if str(t.get("url", "")).rstrip("/").lower() != key]
    teams.insert(0, {"url": url.rstrip("/"), "token": token, "name": name, "role": role, "member_id": member_id})
    save_saved_teams(teams[:20])

def start_server():
    global server_process, host_token
    data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "CodeChat")
    os.makedirs(data_dir, exist_ok=True)
    token_file = os.path.join(data_dir, "host.token")
    try:
        if not host_token and os.path.exists(token_file):
            host_token = Path(token_file).read_text(encoding="utf-8").strip()
        if not host_token:
            host_token = secrets.token_hex(32)
            Path(token_file).write_text(host_token, encoding="utf-8")
        os.environ["CODECHAT_HOST_TOKEN"] = host_token
    except Exception:
        if not host_token:
            host_token = secrets.token_hex(32)

    # Never reuse a CodeChat server from an older GUI launch. Reusing it would
    # also reuse its in-memory AI state. A fresh GUI launch must get a fresh
    # server process and therefore a fresh USER_SESSIONS/CHAT_AI_SESSIONS epoch.
    try:
        health = requests.get("http://127.0.0.1:8000/health", timeout=1.0)
        if health.status_code == 200:
            hv = str(health.json().get("version", "unknown"))
            print(f"⚠️ Existing server detected (version {hv}); terminating it for a fresh session.")
            if sys.platform == "win32":
                out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore")
                pids = set()
                for line in out.splitlines():
                    if ":8000" in line and "LISTENING" in line:
                        parts = line.split()
                        if parts and parts[-1].isdigit():
                            pids.add(parts[-1])
                for pid in pids:
                    subprocess.run(["taskkill", "/F", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
            else:
                print("⚠️ Port 8000 is occupied; please stop the existing process.")
    except requests.RequestException:
        pass

    print("🚀 Starting fresh CodeChat server...")
    if getattr(sys, "frozen", False):
        server_command = [sys.executable, "--server"]
        run_dir = os.path.dirname(sys.executable)
    else:
        server_command = [sys.executable, os.path.abspath(__file__), "--server"]
        run_dir = os.path.dirname(os.path.abspath(__file__))
    server_env = os.environ.copy()
    server_env["CODECHAT_HOST_TOKEN"] = host_token
    server_env["CODECHAT_PROJECT_DIR"] = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, "frozen", False) else os.path.dirname(sys.executable)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        server_process = subprocess.Popen(server_command, creationflags=flags, cwd=run_dir, env=server_env)
    except Exception as e:
        print(f"❌ Server launch failed: {e}")
        server_process = None
        return False

    print("⏳ Waiting for fresh CodeChat server...")
    for _ in range(40):
        time.sleep(0.5)
        if server_process.poll() is not None:
            print(f"❌ CodeChat server stopped unexpectedly (exit code {server_process.returncode}).")
            server_process = None
            return False
        try:
            r = requests.get("http://127.0.0.1:8000/health", timeout=2.0)
            if r.status_code == 200 and str(r.json().get("version", "")) == "45.0":
                print(f"✅ Fresh CodeChat v43 server started | AI epoch {r.json().get('ai_session_epoch', '?')}")
                return True
        except requests.RequestException:
            pass
    print("❌ CodeChat server failed to start.")
    return False


def start_ngrok():
    global ngrok_process, public_url
    print("🌐 Starting ngrok...")
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        ngrok_exe = os.path.join(base, "ngrok.exe")
        if not os.path.exists(ngrok_exe): ngrok_exe = shutil.which("ngrok") or "ngrok"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ngrok_process = subprocess.Popen([ngrok_exe, "http", "8000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(30):
            time.sleep(0.5)
            if ngrok_process.poll() is not None: break
            try:
                r = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2.0)
                if r.status_code == 200:
                    for tunnel in r.json().get("tunnels", []):
                        if tunnel.get("proto") == "https":
                            public_url = tunnel.get("public_url")
                            if public_url:
                                print(f"✅ ngrok started successfully.\n🌍 Public URL: {public_url}"); return True
            except requests.RequestException: pass
        print("❌ Could not obtain ngrok public URL."); return False
    except FileNotFoundError:
        print("❌ ngrok.exe not found. Place ngrok.exe beside CodeChat.exe or add it to PATH."); return False
    except Exception as e:
        print(f"❌ ngrok failed: {e}"); return False


def stop_server():
    """
    Stops the CodeChat server when the GUI closes.
    """

    global server_process

    if server_process is not None:

        try:

            if server_process.poll() is None:
                print("🛑 Stopping CodeChat server...")
                server_process.terminate()

                try:
                    server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_process.kill()

        except Exception as e:
            print(f"Server shutdown error: {e}")

        server_process = None


def stop_ngrok():
    global ngrok_process, public_url

    if ngrok_process is not None:

        try:
            if ngrok_process.poll() is None:
                print("🛑 Stopping ngrok...")
                ngrok_process.terminate()

                try:
                    ngrok_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    ngrok_process.kill()

        except Exception as e:
            print(f"ngrok shutdown error: {e}")

    ngrok_process = None
    public_url = None
host_token = None


# ============================================================
# VOICE LOOP
# ============================================================

class VoiceLoop(QThread):

    update_status = pyqtSignal(str)
    speech_recognized = pyqtSignal(str)
    finished = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.is_running = True
        self.paused = False
        self.recognizer = sr.Recognizer()

    def run(self):

        try:

            with sr.Microphone() as source:
                pass

        except Exception as e:

            self.error_signal.emit(
                f"Mic Error: {str(e)}"
            )

            return

        while self.is_running:

            if self.paused:
                self.msleep(200)
                continue

            try:

                self.update_status.emit("🎤 Listening...")

                with sr.Microphone() as source:

                    self.recognizer.adjust_for_ambient_noise(
                        source,
                        duration=0.5
                    )

                    try:

                        audio = self.recognizer.listen(
                            source,
                            timeout=3,
                            phrase_time_limit=10
                        )

                    except sr.WaitTimeoutError:

                        continue

                if self.paused:
                    continue

                self.update_status.emit("Processing...")

                try:

                    text = self.recognizer.recognize_google(
                        audio
                    )

                except sr.UnknownValueError:

                    continue

                if text:
                    self.speech_recognized.emit(text)

            except Exception as e:

                print(f"Voice Error: {e}")

        self.finished.emit()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.is_running = False


# ============================================================
# TASK WORKER
# ============================================================

class TaskWorker(QThread):

    msg_signal = pyqtSignal(str)
    result_signal = pyqtSignal(object)

    def __init__(
        self,
        brain,
        task,
        data=None,
        append_mode=False,
        extra=None,
        public_flag=False,
        history=None
    ):

        super().__init__()

        self.brain = brain
        self.task = task
        self.data = data
        self.append_mode = append_mode
        self.extra = extra
        self.public_flag = public_flag
        self.history = history

    def run(self):

        res = "Error: Unknown Task Failure"

        try:

            if self.task == "ingest":

                # HOST TEAM UPLOADS MUST GO THROUGH THE AUTHORITATIVE SERVER.
                # The previous build passed extra="publish_team" from the GUI,
                # but TaskWorker ignored it and indexed only the Host's local
                # CoreBrain. That made the upload appear successful while remote
                # members still saw an empty/old Team Brain.
                if (self.extra == "publish_team"
                        and isinstance(self.brain, CoreBrain)):
                    res = self.brain.publish_files_to_server(
                        self.data,
                        self.msg_signal.emit,
                        append_mode=self.append_mode
                    )
                else:
                    res = self.brain.ingest_codebase(
                        self.data,
                        self.msg_signal.emit,
                        self.append_mode
                    )

            elif self.task == "save_brain":

                res = self.brain.save_snapshot(
                    self.data
                )

            elif self.task == "load_brain":

                res = self.brain.load_snapshot(
                    self.data
                )

            elif self.task == "save_session":
                try:
                    with tempfile.TemporaryDirectory(prefix="codechat_session_") as td:
                        temp_brain = os.path.join(td, "session.brain")
                        temp_chat = os.path.join(td, "session.json")
                        save_res = self.brain.save_snapshot(temp_brain)
                        if "Success" not in save_res:
                            raise Exception(save_res)
                        with open(temp_chat, "w", encoding="utf-8") as f:
                            json.dump(self.extra, f, indent=2)
                        with zipfile.ZipFile(self.data, "w", zipfile.ZIP_DEFLATED) as zf:
                            zf.write(temp_brain, "temp_session_brain.brain")
                            zf.write(temp_chat, "temp_session_chat.json")
                    res = "Success"
                except Exception as e:
                    res = f"Error saving session: {e}"

            elif self.task == "load_session":
                try:
                    with tempfile.TemporaryDirectory(prefix="codechat_session_load_") as td:
                        with zipfile.ZipFile(self.data, "r") as zf:
                            names = set(zf.namelist())
                            if "temp_session_brain.brain" not in names:
                                raise Exception("Session does not contain a Brain.")
                            zf.extract("temp_session_brain.brain", td)
                            if "temp_session_chat.json" in names:
                                zf.extract("temp_session_chat.json", td)

                        brain_path = os.path.join(td, "temp_session_brain.brain")
                        load_res = self.brain.load_snapshot(brain_path)
                        if "Success" not in load_res:
                            raise Exception(load_res)

                        chat_data = []
                        chat_path = os.path.join(td, "temp_session_chat.json")
                        if os.path.exists(chat_path):
                            with open(chat_path, "r", encoding="utf-8") as f:
                                chat_data = json.load(f)
                        res = chat_data
                except Exception as e:
                    res = f"Error loading session: {e}"

            elif self.task == "query":

                res = self.brain.ask_question(
                    self.data,
                    history=self.history,
                    is_public=self.public_flag
                )

            elif self.task == "sync_server":

                if not isinstance(
                    self.brain,
                    CoreBrain
                ):

                    res = "ERROR|Cannot sync from Remote Mode"

                else:

                    fd, temp_file = tempfile.mkstemp(prefix="codechat_sync_", suffix=".brain")
                    os.close(fd)

                    save_res = self.brain.save_snapshot(
                        temp_file
                    )

                    if "Success" not in save_res:

                        res = (
                            f"ERROR|Save Failed: {save_res}"
                        )

                    else:

                        try:

                            with open(
                                temp_file,
                                "rb"
                            ) as f:

                                b64_data = base64.b64encode(
                                    f.read()
                                ).decode("utf-8")

                            resp = requests.post(
                                "http://localhost:8000/sync_brain",
                                json={
                                    "b64_data": b64_data
                                },
                                headers={
                                    "x-access-token": host_token or ""
                                },
                                timeout=30
                            )

                            if resp.status_code == 200:

                                res = "SUCCESS|Synced"

                            else:

                                res = (
                                    f"ERROR|Server Reject: "
                                    f"{resp.text}"
                                )

                        except Exception as e:

                            res = (
                                f"ERROR|Upload Failed: {e}"
                            )

                        finally:

                            if os.path.exists(temp_file):
                                os.remove(temp_file)

            elif self.task == "invite":

                try:

                    if isinstance(self.brain, RemoteBrain):
                        invite_url = f"{self.brain.url}/generate_invite"
                        invite_token = self.brain.token
                    else:
                        invite_url = "http://localhost:8000/generate_invite"
                        invite_token = host_token or ""

                    resp = requests.post(
                        invite_url,
                        json={
                            "email": self.data,
                            "role": self.extra
                        },
                        headers={
                            "x-access-token": invite_token
                        },
                        timeout=10
                    )

                    if resp.status_code == 200:

                        res = (
                            f"SUCCESS|"
                            f"{resp.json()['token']}"
                        )

                    else:

                        res = f"ERROR|{resp.text}"

                except:

                    res = "ERROR|Server Offline"

            self.result_signal.emit(res)

        except Exception as e:

            self.result_signal.emit(
                f"CRITICAL ERROR: {str(e)}"
            )


class ChatJobSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal(object)


class ChatJob(QRunnable):
    """Non-QThread chat network job. QRunnable is owned by QThreadPool, so
    pressing Send can never orphan/destroy a QThread wrapper while running."""
    def __init__(self, brain, text, conversation_id, local_token):
        super().__init__()
        self.setAutoDelete(True)
        self.brain = brain
        self.text = text
        self.conversation_id = conversation_id
        self.local_token = local_token
        self.signals = ChatJobSignals()

    def run(self):
        try:
            if getattr(self.brain, "url", None) and getattr(self.brain, "token", None):
                url = f"{self.brain.url.rstrip('/')}/chat/messages"
                headers = dict(getattr(self.brain, "headers", {}) or {})
                token = self.brain.token
            else:
                url = "http://127.0.0.1:8000/chat/messages"
                headers = {"x-access-token": self.local_token or ""}
            response = requests.post(
                url,
                json={"text": self.text, "conversation_id": self.conversation_id},
                headers=headers,
                timeout=120
            )
            try:
                payload = response.json()
            except Exception:
                payload = {"error": response.text or f"HTTP {response.status_code}"}
            if response.status_code != 200:
                if not payload.get("error"):
                    detail = payload.get("detail", response.text or "Chat request failed")
                    payload = {"error": detail}
                self.signals.error.emit(str(payload.get("error")))
            else:
                self.signals.result.emit(payload)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit(self)



class JoinJobSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal(object)


class JoinJob(QRunnable):
    """Authenticate with a new invite token or a remembered member token.
    A remembered token is sufficient; no new invite is required unless it was
    revoked/removed or the user is joining a different server."""
    def __init__(self, url, token):
        super().__init__()
        self.setAutoDelete(True)
        self.url = url.rstrip("/")
        self.token = token.strip()
        self.signals = JoinJobSignals()

    def run(self):
        try:
            headers = {"x-access-token": self.token}
            try:
                login_resp = requests.post(
                    f"{self.url}/team/login", json={"token": self.token},
                    headers=headers, timeout=(3, 10)
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Team server is offline or unreachable. Check the server/URL and try again. ({exc})")

            # A token can be either a remembered member credential OR a brand-new
            # invite. /team/login intentionally accepts only existing members,
            # while /check_role promotes a pending invite into a member.  The old
            # flow tried only /team/login, so every NEW invite was incorrectly
            # reported as an invalid saved login.
            if login_resp.status_code in (401, 403):
                try:
                    invite_resp = requests.get(
                        f"{self.url}/check_role", headers=headers, timeout=(3, 10)
                    )
                except requests.RequestException as exc:
                    raise RuntimeError(f"Team server is offline or unreachable. ({exc})")
                if invite_resp.status_code != 200:
                    raise RuntimeError("This team login/invite is not valid. If this is a new team, verify the Host URL and invite token. If it is a saved team, the member credential may have been removed or revoked.")
                role_data = invite_resp.json()
            elif login_resp.status_code != 200:
                raise RuntimeError(f"Server returned {login_resp.status_code}: {login_resp.text}")
            else:
                role_data = login_resp.json()
            try:
                state_resp = requests.get(
                    f"{self.url}/team_state", headers=headers, timeout=(3, 10)
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Login succeeded, but the Team server went offline while loading Team state. Try again when it is online. ({exc})")
            if state_resp.status_code in (401, 403):
                raise RuntimeError("Login credential was rejected by the Team server. A new invite may be required.")
            if state_resp.status_code != 200:
                raise RuntimeError(f"Could not read Team state ({state_resp.status_code}): {state_resp.text}")
            self.signals.result.emit({"url": self.url, "token": self.token, "role": role_data, "state": state_resp.json()})
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit(self)


class TeamPollWorker(QThread):
    result_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(self, brain, conversation_id="team", local_version=0):
        super().__init__()
        self.brain = brain
        self.conversation_id = conversation_id
        self.local_version = local_version

    def run(self):
        try:
            if getattr(self.brain, "url", None) and getattr(self.brain, "token", None):
                url = self.brain.url.rstrip("/")
                headers = dict(getattr(self.brain, "headers", {}) or {})
                state_resp = requests.get(f"{url}/team_state", headers=headers, timeout=(2, 5))
                if state_resp.status_code in (401, 403):
                    self.result_signal.emit({"brain": self.brain, "auth_error": True})
                    return
                if state_resp.status_code != 200:
                    self.result_signal.emit({"brain": self.brain, "offline": True})
                    return
                state = state_resp.json()
                data = {"brain": self.brain, "state": state}
                data["users"] = state.get("users", [])
                data["conversations"] = state.get("conversations")
                data["history_sessions"] = state.get("history_sessions", [])
                data["live_history"] = state.get("live_history", {})
                data["history"] = state.get("live_history", {}).get("public_ai", [])
                if state.get("chat_enabled") and self.conversation_id:
                    try:
                        m = requests.get(f"{url}/chat/messages", headers=headers, timeout=(2, 5),
                                         params={"conversation_id": self.conversation_id})
                        if m.status_code == 200:
                            data["messages"] = m.json().get("messages", [])
                            data["messages_conversation_id"] = self.conversation_id
                    except Exception:
                        pass
            else:
                state_resp = requests.get("http://127.0.0.1:8000/team_state",
                                          headers={"x-access-token": host_token or ""}, timeout=(2, 5))
                if state_resp.status_code != 200:
                    self.result_signal.emit({"brain": self.brain, "offline": True})
                    return
                state = state_resp.json()
                data = {"brain": self.brain, "state": state}
                data["users"] = state.get("users", [])
                data["conversations"] = state.get("conversations")
                data["history_sessions"] = state.get("history_sessions", [])
                data["live_history"] = state.get("live_history", {})
                data["history"] = state.get("live_history", {}).get("public_ai", [])

            self.result_signal.emit(data)
        except Exception as e:
            self.error_signal.emit(str(e))


# ============================================================
# INVITE DIALOG
# ============================================================

class InviteDialog(QDialog):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("Invite Teammate")
        self.resize(300, 150)
        self.setStyleSheet(PRO_STYLE)

        v = QVBoxLayout(self)

        self.email = QLineEdit()
        self.email.setPlaceholderText("Enter Name")

        v.addWidget(QLabel("Name:"))
        v.addWidget(self.email)
        v.addWidget(QLabel("Role:"))

        self.r_guest = QRadioButton(
            "Guest (Read Only)"
        )

        self.r_guest.setChecked(True)

        self.r_collab = QRadioButton(
            "Collaborator (Can Upload)"
        )

        v.addWidget(self.r_guest)
        v.addWidget(self.r_collab)

        btn = QPushButton(
            "Generate Invite Token"
        )

        btn.clicked.connect(
            self.accept
        )

        v.addWidget(btn)

    def get_data(self):

        return (
            self.email.text(),
            "collaborator"
            if self.r_collab.isChecked()
            else "guest"
        )


# ============================================================
# TOKEN POPUP
# ============================================================

class TokenPopup(QDialog):

    def __init__(self, token, server_url):

        super().__init__()

        self.setWindowTitle(
            "Invite Generated"
        )

        self.resize(400, 300)
        self.setStyleSheet(PRO_STYLE)

        v = QVBoxLayout(self)

        lbl = QLabel(
            "✅ Invite Created!\n\n"
            "Share these 2 things:"
        )

        lbl.setStyleSheet(
            "color: #23a559; "
            "font-weight: bold; "
            "font-size: 14px;"
        )

        v.addWidget(lbl)

        v.addWidget(
            QLabel(
                "1. The Server URL (Ngrok/Local IP):"
            )
        )

        url_box = QLineEdit(
            server_url or "NGROK URL unavailable"
        )

        url_box.setReadOnly(True)

        v.addWidget(url_box)

        v.addWidget(
            QLabel(
                "2. This Invite Token:"
            )
        )

        token_box = QTextEdit(token)

        token_box.setReadOnly(True)

        token_box.setStyleSheet(
            "font-size: 13px; "
            "background-color: #111; "
            "padding: 10px;"
        )

        v.addWidget(token_box)

        ok = QPushButton("Close")

        ok.clicked.connect(
            self.accept
        )

        v.addWidget(ok)


# ============================================================
# MAIN APPLICATION
# ============================================================

class CoreApp(QMainWindow):

    def __init__(self):

        super().__init__()

        self.brain = CoreBrain()
        self.voice_thread = None
        self.chat_history_log = []
        self.team_mode = "single"
        self.team_brain_ready = False
        self.team_brain_version = 0
        self.team_brain_chunks = 0
        self.is_team_client = False
        self.user_role = "host"
        self.member_id = "host"
        self.user_name = "Host"
        self.user_handle = "host"
        self.chat_enabled = True
        self.chat_conversations = []
        self.current_conversation_id = "team"
        self._chat_last_id = {}
        self._poll_worker = None
        self._chat_pool = QThreadPool(self)
        self._chat_pool.setMaxThreadCount(4)
        self._chat_jobs = set()
        self._task_workers = set()
        self._closing = False
        self._join_in_progress = False
        self._join_job = None
        self._poll_generation = 0
        self._busy = False
        self._notified_message_ids = set()
        self._unread_counts = {}

        self.init_ui()

        self._setup_notifications()

        self.team_timer = QTimer()

        self.team_timer.timeout.connect(
            self.poll_updates
        )

        self.team_timer.start(5000)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def init_ui(self):

        self.setWindowTitle(
            "CodeChat Pro - Team Edition"
        )

        self.resize(1100, 700)

        self.setStyleSheet(PRO_STYLE)

        main_widget = QWidget()

        self.setCentralWidget(
            main_widget
        )

        main_layout = QHBoxLayout(
            main_widget
        )

        main_layout.setContentsMargins(
            0, 0, 0, 0
        )

        main_layout.setSpacing(0)

        # ----------------------------------------------------
        # SIDEBAR
        # ----------------------------------------------------

        sidebar = QFrame()

        sidebar.setFixedWidth(220)

        sidebar.setObjectName(
            "Sidebar"
        )

        sb_layout = QVBoxLayout(
            sidebar
        )

        sb_layout.setContentsMargins(
            15, 20, 15, 20
        )

        sb_layout.setSpacing(10)

        logo = QLabel(
            "⚡ CodeChat"
        )

        logo.setStyleSheet(
            "color: white; "
            "font-size: 20px; "
            "font-weight: bold; "
            "margin-bottom: 10px;"
        )

        sb_layout.addWidget(logo)

        sb_layout.addWidget(
            QLabel("PROJECT")
        )

        self.btn_new = QPushButton(
            "📂 New Project"
        )

        self.btn_new.clicked.connect(
            self.do_ingest
        )

        sb_layout.addWidget(
            self.btn_new
        )

        self.btn_mode = QPushButton(
            "⚡ Single Mode"
        )

        self.btn_mode.setCheckable(True)

        self.btn_mode.clicked.connect(
            self.toggle_mode
        )

        self.btn_mode.setStyleSheet(
            "color: #aaa; "
            "font-style: italic; "
            "border: 1px dashed #444;"
        )

        sb_layout.addWidget(
            self.btn_mode
        )

        self.btn_load_brain = QPushButton(
            "🧠 Load Brain"
        )

        self.btn_load_brain.clicked.connect(
            self.do_load_brain
        )

        sb_layout.addWidget(
            self.btn_load_brain
        )

        self.btn_save_brain = QPushButton(
            "💾 Save Brain"
        )

        self.btn_save_brain.clicked.connect(
            self.do_save_brain
        )

        self.btn_save_brain.setEnabled(
            False
        )

        sb_layout.addWidget(
            self.btn_save_brain
        )

        sb_layout.addSpacing(10)

        sb_layout.addWidget(
            QLabel("CHAT + CONTEXT")
        )

        self.btn_save_chat = QPushButton(
            "💾 Save Session"
        )

        self.btn_save_chat.clicked.connect(
            self.do_save_chat
        )

        sb_layout.addWidget(
            self.btn_save_chat
        )

        self.btn_load_chat = QPushButton(
            "📂 Load Session"
        )

        self.btn_load_chat.clicked.connect(
            self.do_load_chat
        )

        sb_layout.addWidget(
            self.btn_load_chat
        )

        sb_layout.addSpacing(10)

        sb_layout.addWidget(
            QLabel("TEAM")
        )

        self.btn_sync = QPushButton(
            "🔄 Sync Server"
        )

        self.btn_sync.clicked.connect(
            self.do_sync
        )

        sb_layout.addWidget(
            self.btn_sync
        )

        self.btn_invite = QPushButton(
            "📧 Invite"
        )

        self.btn_invite.clicked.connect(
            self.send_invite
        )

        sb_layout.addWidget(
            self.btn_invite
        )

        self.btn_join = QPushButton(
            "🌐 Join Team"
        )

        self.btn_join.setCheckable(True)

        self.btn_join.clicked.connect(
            self.toggle_join
        )

        sb_layout.addWidget(
            self.btn_join
        )

        # ----------------------------------------------------
        # TEAM MANAGEMENT / HUMAN CHAT
        # ----------------------------------------------------

        self.btn_manage_members = QPushButton(
            "👥 Manage Members"
        )
        self.btn_manage_members.clicked.connect(
            self.manage_members
        )
        sb_layout.addWidget(
            self.btn_manage_members
        )

        self.btn_chat_toggle = QPushButton(
            "💬 Team Chat: ON"
        )
        self.btn_chat_toggle.setCheckable(True)
        self.btn_chat_toggle.setChecked(True)
        self.btn_chat_toggle.clicked.connect(
            self.toggle_team_chat
        )
        sb_layout.addWidget(
            self.btn_chat_toggle
        )

        self.btn_new_group = QPushButton(
            "➕ New Chat Group"
        )
        self.btn_new_group.clicked.connect(
            self.create_team_group
        )
        sb_layout.addWidget(
            self.btn_new_group
        )

        sb_layout.addSpacing(10)

        sb_layout.addWidget(
            QLabel("TOOLS")
        )

        self.btn_voice = QPushButton(
            "📞 Voice Mode"
        )

        self.btn_voice.setCheckable(True)

        self.btn_voice.clicked.connect(
            self.toggle_voice
        )

        sb_layout.addWidget(
            self.btn_voice
        )

        sb_layout.addStretch()

        self.lbl_activity = QLabel(
            "Ready"
        )

        self.lbl_activity.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.lbl_activity.setStyleSheet(
            "color: #5865F2; "
            "font-size: 13px; "
            "font-weight: bold; "
            "margin-bottom: 5px;"
        )

        sb_layout.addWidget(
            self.lbl_activity
        )

        self.user_badge = QLabel(
            " Local Host "
        )

        self.user_badge.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.user_badge.setStyleSheet(
            f"background-color: {STATUS_LOCAL}; "
            "color: white; "
            "border-radius: 4px; "
            "padding: 5px; "
            "font-weight: bold;"
        )

        sb_layout.addWidget(
            self.user_badge
        )

        main_layout.addWidget(
            sidebar
        )

        # ----------------------------------------------------
        # CENTER
        # ----------------------------------------------------

        center_frame = QFrame()

        center_layout = QVBoxLayout(
            center_frame
        )

        center_layout.setContentsMargins(
            0, 0, 0, 0
        )

        self.tabs = QTabWidget()

        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)

        self.team_view = QTextBrowser()
        self.team_view.setOpenExternalLinks(True)
        # Host public answers are rendered immediately while /host_log is
        # acknowledged; pending entries are reconciled by the next poll.
        self._pending_host_stream = []

        self.tabs.addTab(
            self.chat,
            "💬 My Session (Private)"
        )

        self.tabs.addTab(
            self.team_view,
            "👥 Team Stream (Public)"
        )

        # Human-only Team Chat. This tab never calls Ollama/RAG.
        self.team_chat_widget = QWidget()
        chat_layout = QHBoxLayout(self.team_chat_widget)
        chat_layout.setContentsMargins(8, 8, 8, 8)
        chat_sidebar = QVBoxLayout()
        self.chat_conversation_list = QListWidget()
        self.chat_conversation_list.setFixedWidth(190)
        self.chat_conversation_list.currentRowChanged.connect(
            self.select_chat_conversation
        )
        self.btn_delete_chat = QPushButton("🗑 Delete Chat for Me")
        self.btn_delete_chat.setToolTip(
            "Clear this conversation from your view. Other participants keep their messages."
        )
        self.btn_delete_chat.clicked.connect(self.delete_current_chat)
        self.btn_leave_group = QPushButton("🚪 Leave Group")
        self.btn_leave_group.setToolTip("Leave the selected group. Other members keep the group.")
        self.btn_leave_group.clicked.connect(self.leave_current_group)
        self.btn_delete_group = QPushButton("🗑 Delete Group")
        self.btn_delete_group.setToolTip("Delete the selected group for everyone. Only the Host or group creator can do this.")
        self.btn_delete_group.clicked.connect(self.delete_current_group)
        chat_sidebar.addWidget(self.chat_conversation_list, 1)
        chat_sidebar.addWidget(self.btn_delete_chat)
        chat_sidebar.addWidget(self.btn_leave_group)
        chat_sidebar.addWidget(self.btn_delete_group)
        self.team_chat_view = QTextBrowser()
        chat_layout.addLayout(chat_sidebar)
        chat_layout.addWidget(self.team_chat_view, 1)
        self.tabs.addTab(self.team_chat_widget, "💬 Team Chat")

        # Read-only session history. Current/live session is shared in real time;
        # older sessions are fetched server-side and filtered to the requesting user.
        self.history_widget = QWidget()
        history_layout = QHBoxLayout(self.history_widget)
        history_layout.setContentsMargins(8, 8, 8, 8)
        self.history_list = QListWidget()
        self.history_list.setFixedWidth(230)
        self.history_list.currentRowChanged.connect(self.select_history_session)
        self.history_view = QTextBrowser()
        self.history_view.setReadOnly(True)
        history_layout.addWidget(self.history_list)
        history_layout.addWidget(self.history_view, 1)
        self.tabs.addTab(self.history_widget, "🕘 History")
        self.history_sessions = []
        self.selected_history_id = None
        self.tabs.currentChanged.connect(self.on_tab_changed)

        center_layout.addWidget(
            self.tabs
        )

        inp_frame = QFrame()

        inp_frame.setFixedHeight(70)

        inp_frame.setStyleSheet(
            "background-color: #161616; "
            "border-top: 1px solid #222;"
        )

        i_layout = QHBoxLayout(
            inp_frame
        )

        i_layout.setContentsMargins(
            15, 10, 15, 10
        )

        self.inp = QLineEdit()

        self.inp.setPlaceholderText(
            "Message @team, @person, @group or @ai..."
            if getattr(self, "tabs", None) is not None and self.tabs.currentIndex() == 2
            else "Ask a question..."
        )

        self.inp.returnPressed.connect(
            self.ask_text
        )

        self.btn_send = QPushButton("➤")

        self.btn_send.setFixedSize(
            40, 40
        )

        self.btn_send.clicked.connect(
            self.ask_text
        )

        self.btn_send.setStyleSheet(
            "border-radius: 20px; "
            "font-size: 18px;"
        )

        i_layout.addWidget(
            self.inp
        )

        i_layout.addWidget(
            self.btn_send
        )

        center_layout.addWidget(
            inp_frame
        )

        main_layout.addWidget(
            center_frame
        )

        # ----------------------------------------------------
        # ACTIVE USERS
        # ----------------------------------------------------

        self.user_panel = QFrame()

        self.user_panel.setFixedWidth(
            180
        )

        self.user_panel.setStyleSheet(
            "background-color: #111; "
            "border-left: 1px solid #222;"
        )

        u_layout = QVBoxLayout(
            self.user_panel
        )

        u_layout.setContentsMargins(
            10, 20, 10, 20
        )

        u_layout.addWidget(
            QLabel("🟢 ACTIVE USERS")
        )

        self.user_list = QListWidget()

        self.user_list.setStyleSheet(
            "background: transparent; "
            "border: none; "
            "font-size: 12px; "
            "color: #aaa;"
        )

        u_layout.addWidget(
            self.user_list
        )
        self.user_list.itemDoubleClicked.connect(self.show_user_details)

        main_layout.addWidget(
            self.user_panel
        )

        self.lock_ui()
        self.update_chat_controls()
        self.refresh_chat_conversations()

    # --------------------------------------------------------
    # NOTIFICATIONS
    # --------------------------------------------------------

    def _setup_notifications(self):
        self._tray = None
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                self._tray = QSystemTrayIcon(self)
                self._tray.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
                self._tray.setToolTip("CodeChat Pro")
                self._tray.show()
        except Exception:
            self._tray = None

    def _notification_data(self):
        try:
            if isinstance(self.brain, RemoteBrain):
                return self.brain.get_chat_notifications()
            r = requests.get("http://127.0.0.1:8000/chat/notifications", headers={"x-access-token": host_token or ""}, timeout=2)
            return r.json() if r.status_code == 200 else {"unread": {}, "notifications": []}
        except Exception:
            return {"unread": {}, "notifications": []}

    def _mark_conversation_read(self, conversation_id, message_id=None):
        if not conversation_id:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.mark_chat_read(conversation_id, message_id)
            else:
                r = requests.post("http://127.0.0.1:8000/chat/read", json={"conversation_id": conversation_id, "message_id": message_id}, headers={"x-access-token": host_token or ""}, timeout=2)
                result = r.json() if r.status_code == 200 else {}
            self._unread_counts.pop(conversation_id, None)
        except Exception:
            pass

    def _poll_notifications(self):
        if self._closing or not self.chat_enabled:
            return
        data = self._notification_data()
        unread = data.get("unread", {}) or {}
        self._unread_counts = {str(k): int(v or 0) for k, v in unread.items() if int(v or 0) > 0}

        # The currently open conversation is considered read, just like WhatsApp.
        if self.tabs.currentIndex() == 2 and self.current_conversation_id in self._unread_counts:
            self._mark_conversation_read(self.current_conversation_id)
            self._unread_counts.pop(self.current_conversation_id, None)

        self._render_chat_conversations()

        for n in data.get("notifications", []) or []:
            mid = n.get("id")
            if not mid or mid in self._notified_message_ids:
                continue
            self._notified_message_ids.add(mid)
            # Don't notify for the conversation currently being viewed.
            if self.tabs.currentIndex() == 2 and n.get("conversation_id") == self.current_conversation_id:
                continue
            conv = next((c for c in self.chat_conversations if c.get("id") == n.get("conversation_id")), None) or {}
            title = ("📣 Mention from " if n.get("mentioned") else "💬 New message from ") + str(n.get("sender", "Team member"))
            body = str(n.get("message", ""))
            if len(body) > 180:
                body = body[:177] + "..."
            if self._tray is not None:
                try:
                    self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 5000)
                except Exception:
                    QApplication.beep()
            else:
                QApplication.beep()

        # Keep the set bounded across long-running sessions.
        if len(self._notified_message_ids) > 2000:
            self._notified_message_ids = set(list(self._notified_message_ids)[-1000:])

    # --------------------------------------------------------
    # BASIC UI
    # --------------------------------------------------------

    def set_status(self, text):
        self.lbl_activity.setText(text)

    def lock_ui(self):
        # AI input only. Team Chat has its own independent controls and must
        # never be disabled by Brain sync/polling state.
        if getattr(self, "tabs", None) is not None and self.tabs.currentIndex() == 2:
            self.update_chat_controls()
            return
        self.inp.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.inp.setPlaceholderText("Waiting for Team Brain..." if isinstance(self.brain, RemoteBrain) else "Load project to start...")

    def unlock_ui(self):
        # AI input only; never touch Team Chat state here.
        if getattr(self, "tabs", None) is not None and self.tabs.currentIndex() == 2:
            self.update_chat_controls()
            return
        self.inp.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.inp.setPlaceholderText("Ask a question...")

    def handle_team_revoked(self):
        if not isinstance(self.brain, RemoteBrain):
            return
        name = self.user_name or "Team Member"
        self.add_msg(
            f"⚠️ Team access for {name} is no longer valid. "
            "You may have been removed by the Host or your membership was revoked.",
            "ai"
        )
        self.set_status("Team Access Revoked")
        self.btn_join.blockSignals(True)
        self.btn_join.setChecked(False)
        self.btn_join.blockSignals(False)

        self.brain = CoreBrain()
        self.is_team_client = False
        self.user_role = "local"
        self.user_name = "Local User"
        self.user_handle = "local"
        self.member_id = "local"
        self.chat_enabled = True

        self.user_badge.setText(" Local / Offline ")
        self.user_badge.setStyleSheet(
            f"background-color: {STATUS_LOCAL}; color:white; "
            "border-radius:4px; padding:5px; font-weight:bold;"
        )
        self.btn_mode.setEnabled(True)
        self.btn_load_brain.setEnabled(True)
        self.btn_sync.setEnabled(True)
        self.btn_invite.setEnabled(True)
        self.btn_manage_members.setEnabled(True)
        self.btn_chat_toggle.setEnabled(True)
        self.btn_new_group.setEnabled(True)

    # --------------------------------------------------------
    # HUMAN TEAM CHAT
    # --------------------------------------------------------

    def on_tab_changed(self, index):
        if index == 2:
            self.inp.setPlaceholderText("Message @team, @person, @group or @ai..." if self.chat_enabled else "Team Chat is disabled by Host")
            self.update_chat_controls()
            self.poll_updates()
        elif index == 3:
            self.inp.setPlaceholderText("History is read-only")
            self.inp.setEnabled(False)
            self.btn_send.setEnabled(False)
            self.poll_updates()
            self.refresh_selected_history()
        else:
            self.inp.setPlaceholderText("Ask a question...")
            if index == 1:
                self.poll_updates()
            if index != 3:
                self.update_chat_controls()

    def update_chat_controls(self):
        # This method can be reached during init_ui before the Team Chat
        # widgets have been constructed.  Never assume optional widgets
        # already exist; simply apply the controls that are available.
        enabled = bool(getattr(self, "chat_enabled", True))
        is_host = not isinstance(getattr(self, "brain", None), RemoteBrain)

        btn_chat = getattr(self, "btn_chat_toggle", None)
        if btn_chat is not None:
            btn_chat.blockSignals(True)
            btn_chat.setChecked(enabled)
            btn_chat.setText(
                "💬 Team Chat: ON" if enabled else "💬 Team Chat: OFF"
            )
            btn_chat.blockSignals(False)
            btn_chat.setEnabled(is_host)

        btn_members = getattr(self, "btn_manage_members", None)
        if btn_members is not None:
            btn_members.setEnabled(is_host)

        btn_group = getattr(self, "btn_new_group", None)
        if btn_group is not None:
            btn_group.setEnabled(True)

        conversation_list = getattr(self, "chat_conversation_list", None)
        if conversation_list is not None:
            conversation_list.setEnabled(enabled)

        tabs = getattr(self, "tabs", None)
        inp = getattr(self, "inp", None)
        btn_send = getattr(self, "btn_send", None)
        if tabs is not None and tabs.currentIndex() == 2:
            # Human Team Chat is independent of the Team Brain. A member must
            # be able to chat even while the Host is still loading/indexing a Brain.
            if inp is not None:
                inp.setEnabled(enabled and not getattr(self, "_chat_busy", False))
                inp.setPlaceholderText("Message @team, @person, @group or @ai..." if enabled else "Team Chat is disabled by Host")
            if btn_send is not None:
                btn_send.setEnabled(enabled and not getattr(self, "_chat_busy", False))
        elif tabs is not None and tabs.currentIndex() == 3:
            if inp is not None:
                inp.setEnabled(False)
                inp.setPlaceholderText("History is read-only")
            if btn_send is not None:
                btn_send.setEnabled(False)
            if btn_send is not None:
                btn_send.setEnabled(False)
            return
        if tabs is not None and tabs.currentIndex() == 2:
            if inp is not None:
                inp.setEnabled(enabled)
                inp.setPlaceholderText(
                    "Message @team, @person or @group..."
                    if enabled else "Team Chat is disabled by Host"
                )
            if btn_send is not None:
                btn_send.setEnabled(enabled)

    def refresh_chat_conversations(self):
        try:
            if isinstance(self.brain, RemoteBrain):
                data = self.brain.get_chat_conversations()
            else:
                r = requests.get(
                    "http://127.0.0.1:8000/chat/conversations",
                    headers={"x-access-token": host_token or ""},
                    timeout=2
                )
                data = r.json() if r.status_code == 200 else {}

            self.chat_enabled = bool(data.get("enabled", self.chat_enabled))
            self.chat_conversations = data.get("conversations", [])
            self.update_chat_controls()

            current = self.current_conversation_id
            self.chat_conversation_list.blockSignals(True)
            self.chat_conversation_list.clear()
            selected = 0
            for i, conv in enumerate(self.chat_conversations):
                prefix = {"team": "# ", "dm": "👤 ", "group": "👥 "}.get(
                    conv.get("type"), ""
                )
                self.chat_conversation_list.addItem(
                    prefix + conv.get("name", conv.get("id", "Chat"))
                )
                if conv.get("id") == current:
                    selected = i
            if self.chat_conversation_list.count():
                self.chat_conversation_list.setCurrentRow(selected)
            self.chat_conversation_list.blockSignals(False)
            self.update_group_controls()
        except Exception as e:
            print(f"Chat conversation error: {e}")

    def select_chat_conversation(self, row):
        if 0 <= row < len(self.chat_conversations):
            self.current_conversation_id = self.chat_conversations[row].get("id", "team")
            self._mark_conversation_read(self.current_conversation_id)
            self.update_group_controls()
            self.refresh_chat_messages()

    def update_group_controls(self):
        conv = next((c for c in self.chat_conversations if c.get("id") == self.current_conversation_id), None) or {}
        is_group = conv.get("type") == "group"
        self.btn_leave_group.setEnabled(is_group)
        self.btn_delete_group.setEnabled(
            is_group and (self.member_id == "host" or conv.get("created_by") == self.member_id)
        )
        self.btn_delete_chat.setEnabled(bool(conv) and not is_group)

    def leave_current_group(self):
        if self._closing:
            return
        conv = next((c for c in self.chat_conversations if c.get("id") == self.current_conversation_id), None) or {}
        if conv.get("type") != "group":
            return
        group_id = conv.get("group_id") or self.current_conversation_id.split(":", 1)[-1]
        reply = QMessageBox.question(
            self, "Leave Group",
            f"Leave '{conv.get('name', 'this group')}'?\n\nYou will no longer receive messages from this group until you are added again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.leave_group(group_id)
            else:
                r = requests.post(
                    f"http://127.0.0.1:8000/chat/groups/{group_id}/leave",
                    headers={"x-access-token": host_token or ""}, timeout=5
                )
                result = r.json() if r.status_code == 200 else {"error": r.text}
            if result.get("error"):
                raise RuntimeError(result["error"])
            self.current_conversation_id = "team"
            self.refresh_chat_conversations()
            self.set_status("Left group")
        except Exception as e:
            QMessageBox.warning(self, "Leave Group", str(e))

    def delete_current_group(self):
        if self._closing:
            return
        conv = next((c for c in self.chat_conversations if c.get("id") == self.current_conversation_id), None) or {}
        if conv.get("type") != "group":
            return
        group_id = conv.get("group_id") or self.current_conversation_id.split(":", 1)[-1]
        reply = QMessageBox.question(
            self, "Delete Group",
            f"Permanently delete '{conv.get('name', 'this group')}' for everyone?\n\nAll members will lose access to this group conversation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.delete_group(group_id)
            else:
                r = requests.delete(
                    f"http://127.0.0.1:8000/chat/groups/{group_id}",
                    headers={"x-access-token": host_token or ""}, timeout=5
                )
                result = r.json() if r.status_code == 200 else {"error": r.text}
            if result.get("error"):
                raise RuntimeError(result["error"])
            self.current_conversation_id = "team"
            self.refresh_chat_conversations()
            self.set_status("Group deleted")
        except Exception as e:
            QMessageBox.warning(self, "Delete Group", str(e))

    def delete_current_chat(self):
        if self._closing or not self.chat_enabled:
            return
        conversation_id = self.current_conversation_id or "team"
        conv = next((c for c in self.chat_conversations if c.get("id") == conversation_id), None)
        name = (conv or {}).get("name", "this conversation")
        reply = QMessageBox.question(
            self,
            "Delete Chat for Me",
            f"Clear {name} from your chat history?\n\n"
            "This only deletes your view. Other participants will keep the conversation and messages.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.delete_chat_conversation(conversation_id)
            else:
                r = requests.delete(
                    f"http://127.0.0.1:8000/chat/conversations/{conversation_id}",
                    headers={"x-access-token": host_token or ""},
                    timeout=5
                )
                try:
                    result = r.json()
                except Exception:
                    result = {"error": r.text or f"HTTP {r.status_code}"}
            if result.get("error"):
                raise RuntimeError(result["error"])
            self.set_status("Chat cleared for you")
            self.refresh_chat_messages()
        except Exception as e:
            QMessageBox.warning(self, "Delete Chat", str(e))

    def refresh_chat_messages(self):
        if not self.chat_enabled:
            self.team_chat_view.setHtml(
                "<h2 style='color:#888'>Team Chat Disabled</h2>"
                "<p style='color:#666'>The Host has disabled human chat.</p>"
            )
            return

        try:
            if isinstance(self.brain, RemoteBrain):
                data = self.brain.get_chat_messages(self.current_conversation_id)
            else:
                r = requests.get(
                    "http://127.0.0.1:8000/chat/messages",
                    params={"conversation_id": self.current_conversation_id},
                    headers={"x-access-token": host_token or ""},
                    timeout=2
                )
                data = r.json() if r.status_code == 200 else {}
            messages = data.get("messages", [])
            html = ""
            for m in messages:
                mine = m.get("sender_id") == self.member_id
                align = "right" if mine else "left"
                bg = "#005c4b" if mine else "#1f1f1f"
                html += f"""
                <div style="text-align:{align};margin:8px 0;">
                  <div style="display:inline-block;background:{bg};padding:10px;
                       border-radius:10px;max-width:75%;">
                    <div style="font-size:10px;color:#888;font-weight:bold;">
                      {m.get('sender','Unknown')} @{m.get('handle','')}
                      · {m.get('timestamp','')}
                    </div>
                    <div style="color:#eee;margin-top:4px;white-space:pre-wrap;">
                      {m.get('message','')}
                    </div>
                  </div>
                </div>
                """
            self.team_chat_view.setHtml(html)
            self.team_chat_view.verticalScrollBar().setValue(
                self.team_chat_view.verticalScrollBar().maximum()
            )
        except Exception as e:
            print(f"Chat message error: {e}")

    def send_team_chat(self):
        if self._closing:
            return
        text = self.inp.text().strip()
        if not text or not self.chat_enabled:
            return
        conversation_id = self.current_conversation_id or "team"
        self.inp.clear()
        self.btn_send.setEnabled(False)
        self.set_status("Sending message...")

        job = ChatJob(self.brain, text, conversation_id, host_token or "")
        self._chat_jobs.add(job)
        job.signals.result.connect(self._chat_send_success)
        job.signals.error.connect(self._chat_send_error)
        job.signals.finished.connect(self._chat_job_finished)
        self._chat_pool.start(job)

    def _chat_send_success(self, result):
        if self._closing:
            return
        self.set_status("Message sent")
        self.refresh_chat_messages()
        self.refresh_chat_conversations()

    def _chat_send_error(self, error):
        if self._closing:
            return
        self.set_status("Chat error")
        QMessageBox.warning(self, "Team Chat", str(error))
        self.inp.setFocus()

    def _chat_job_finished(self, job):
        self._chat_jobs.discard(job)
        if not self._closing:
            self.update_chat_controls()

    def toggle_team_chat(self):
        if isinstance(self.brain, RemoteBrain):
            return
        desired = self.btn_chat_toggle.isChecked()
        if not self.confirm_change(
            "Confirm Team Chat Change",
            "Turn Team Chat " + ("ON" if desired else "OFF") + " for everyone?"
        ):
            self.btn_chat_toggle.blockSignals(True)
            self.btn_chat_toggle.setChecked(self.chat_enabled)
            self.btn_chat_toggle.blockSignals(False)
            return
        try:
            r = requests.post(
                "http://127.0.0.1:8000/chat/settings",
                json={"enabled": desired},
                headers={"x-access-token": host_token or ""},
                timeout=5
            )
            if r.status_code != 200:
                raise Exception(r.text)
            self.chat_enabled = bool(r.json().get("enabled", desired))
            self.update_chat_controls()
            self.refresh_chat_messages()
        except Exception as e:
            self.btn_chat_toggle.blockSignals(True)
            self.btn_chat_toggle.setChecked(self.chat_enabled)
            self.btn_chat_toggle.blockSignals(False)
            QMessageBox.warning(self, "Team Chat", str(e))

    def create_team_group(self):
        name, ok = QInputDialog.getText(self, "Create Group", "Group name:")
        if not ok or not name.strip():
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                data = self.brain.get_chat_conversations()
            else:
                r = requests.get("http://127.0.0.1:8000/chat/conversations", headers={"x-access-token": host_token or ""}, timeout=3)
                data = r.json() if r.status_code == 200 else {}
            members = [m for m in data.get("members", []) if m.get("member_id") != self.member_id]
            if not members:
                QMessageBox.information(self, "Create Group", "There are no other active team members to add yet.")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle("Select Group Members")
            dialog.resize(420, 420)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("Select everyone you want in this group:"))
            checks = []
            for m in members:
                cb = QCheckBox(f"{m.get('name','Unknown')}  @{m.get('handle','')}  ·  {m.get('role','guest').title()}")
                cb.setProperty("member_id", m.get("member_id"))
                layout.addWidget(cb)
                checks.append(cb)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected_ids = [cb.property("member_id") for cb in checks if cb.isChecked()]

            if not self.confirm_change("Confirm Group Creation", f"Create '{name.strip()}' with {len(selected_ids)} selected teammate{'s' if len(selected_ids) != 1 else ''}?"):
                return
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.create_group(name, selected_ids)
            else:
                r = requests.post("http://127.0.0.1:8000/chat/groups", json={"name": name, "member_ids": selected_ids}, headers={"x-access-token": host_token or ""}, timeout=5)
                result = r.json() if r.status_code == 200 else {"error": r.text}
            if result.get("error"):
                QMessageBox.warning(self, "Create Group", result["error"]); return
            group = result.get("group", {})
            self.current_conversation_id = f"group:{group.get('group_id')}"
            self.refresh_chat_conversations()
            QMessageBox.information(self, "Group Created", f"Created {group.get('name')}.\nUse @{group.get('handle')} to route messages.")
        except Exception as e:
            QMessageBox.warning(self, "Create Group", str(e))

    def manage_members(self):
        if isinstance(self.brain, RemoteBrain):
            return
        try:
            r = requests.get(
                "http://127.0.0.1:8000/team_state",
                headers={"x-access-token": host_token or ""},
                timeout=3
            )
            if r.status_code != 200:
                raise Exception(r.text)
            members = [
                m for m in r.json().get("members", [])
                if m.get("member_id") != "host"
            ]
        except Exception as e:
            QMessageBox.warning(self, "Members", str(e))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Manage Team Members")
        dlg.resize(430, 360)
        dlg.setStyleSheet(PRO_STYLE)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Host has ultimate authority. Select a member to remove or change role:"))
        lst = QListWidget()
        for m in members:
            status = "🟢" if m.get("online") else "⚪"
            lst.addItem(
                f"{status} {m.get('name')} @{m.get('handle')} [{m.get('role')}]"
            )
        layout.addWidget(lst)
        change_btn = QPushButton("🔄 Change Member Role")
        remove_btn = QPushButton("❌ Remove Selected")
        close_btn = QPushButton("Close")
        layout.addWidget(change_btn)
        layout.addWidget(remove_btn)
        layout.addWidget(close_btn)

        def change_selected():
            row = lst.currentRow()
            if row < 0:
                return
            target = members[row]
            current_role = target.get("role", "guest")
            new_role, ok = QInputDialog.getItem(dlg, "Change Member Role", f"Role for {target.get('name') }:", ["guest", "collaborator"], ["guest", "collaborator"].index(current_role) if current_role in ("guest", "collaborator") else 0, False)
            if not ok or new_role == current_role:
                return
            try:
                r = requests.post(
                    "http://127.0.0.1:8000/members/change_role",
                    json={"member_id": target.get("member_id"), "role": new_role},
                    headers={"x-access-token": host_token or ""}, timeout=5
                )
                if r.status_code != 200:
                    raise Exception(r.text)
                target["role"] = new_role
                lst.item(row).setText(f"{'🟢' if target.get('online') else '⚪'} {target.get('name')} @{target.get('handle')} [{new_role}]")
                self.set_status(f"{target.get('name')} is now {new_role}")
            except Exception as e:
                QMessageBox.warning(dlg, "Role Change Failed", str(e))

        def remove_selected():
            row = lst.currentRow()
            if row < 0:
                return
            target = members[row]
            if QMessageBox.question(
                dlg, "Remove Member",
                f"Remove {target.get('name')} from the team?"
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                r = requests.post(
                    "http://127.0.0.1:8000/members/remove",
                    json={"member_id": target.get("member_id")},
                    headers={"x-access-token": host_token or ""},
                    timeout=5
                )
                if r.status_code != 200:
                    raise Exception(r.text)
                lst.takeItem(row)
                self.set_status(f"Removed {target.get('name')}")
            except Exception as e:
                QMessageBox.warning(dlg, "Remove Failed", str(e))

        change_btn.clicked.connect(change_selected)
        remove_btn.clicked.connect(remove_selected)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

    def _register_task_worker(self, worker):
        """Keep every QThread wrapper alive until its native thread exits.

        QThread emits result_signal before run() returns.  The old code stored
        the worker in one shared self.worker attribute, so a new operation
        (such as a mode switch) could garbage-collect the old wrapper while
        its native thread was still finishing, producing:
        "QThread: Destroyed while thread is still running".
        """
        if not hasattr(self, "_task_workers"):
            self._task_workers = set()
        self._task_workers.add(worker)
        worker.finished.connect(lambda w=worker: self._task_worker_finished(w))
        return worker

    def _task_worker_finished(self, worker):
        self._task_workers.discard(worker)
        # Do not delete a QThread wrapper from inside its own finished signal
        # until Qt has completed delivery. deleteLater() is the safe path.
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _task_worker_running(self):
        return any(w is not None and w.isRunning() for w in getattr(self, "_task_workers", set()))

    def toggle_mode(self):
        if (isinstance(self.brain, RemoteBrain) or getattr(self, "_busy", False)
                or self._task_worker_running()):
            # A Brain operation must finish completely before changing mode.
            if self._task_worker_running() and not getattr(self, "_busy", False):
                self.set_status("Please wait for the current operation to finish...")
            return

        desired = "append" if self.btn_mode.isChecked() else "single"
        current = self.team_mode
        if desired == current:
            return

        warning = (
            "Switching to Single Mode will permanently reduce the current Team Brain "
            "to ONLY the latest uploaded file. Older files will be forgotten.\n\nContinue?"
            if desired == "single"
            else
            "Switch to Append Mode? Future uploads will be added to the existing Team Brain."
        )
        if not self.confirm_change("Confirm Mode Change", warning):
            self.btn_mode.blockSignals(True)
            self.btn_mode.setChecked(current == "append")
            self.btn_mode.blockSignals(False)
            return

        self._busy = True
        self.btn_mode.setEnabled(False)
        mode = desired
        try:
            response = requests.post(
                "http://127.0.0.1:8000/set_mode",
                json={"mode": mode},
                headers={"x-access-token": host_token or ""},
                timeout=5
            )

            if response.status_code != 200:
                # Revert the visual state if the server rejected it.
                self.btn_mode.blockSignals(True)
                self.btn_mode.setChecked(mode != "append")
                self.btn_mode.blockSignals(False)
                raise Exception(response.text)

            if mode == "append":
                self.btn_mode.setText("🔗 Append Mode")
                self.btn_mode.setStyleSheet(
                    "color: #F59E0B; "
                    "font-weight: bold; "
                    "border: 1px solid #F59E0B;"
                )
            else:
                self.btn_mode.setText("⚡ Single Mode")
                self.btn_mode.setStyleSheet(
                    "color: #aaa; "
                    "font-style: italic; "
                    "border: 1px dashed #444;"
                )

            self.team_mode = mode
            # IMPORTANT: mode switching is NOT a new session. Preserve the current
            # My AI conversation exactly as-is. The server remains authoritative
            # about which files are searchable, while the current Ollama/session
            # context stays alive until the application is closed/restarted.
            self._busy = False
            self.btn_mode.setEnabled(True)
            self.poll_updates()
            self.set_status(f"Team Mode: {mode.title()}")

        except Exception as e:
            self._busy = False
            self.btn_mode.setEnabled(True)
            self.btn_mode.blockSignals(True)
            self.btn_mode.setChecked(current == "append")
            self.btn_mode.blockSignals(False)
            self.set_status("Mode Error")
            QMessageBox.warning(
                self,
                "Mode Change Failed",
                f"Could not update Team Mode on the server.\n\n{e}"
            )

    def apply_team_state(self, state):
        if not isinstance(state, dict):
            return

        mode = state.get("mode", "single")
        ready = bool(state.get("brain_ready", False))
        version = state.get("brain_version", 0)
        chunks = state.get("chunks", 0)
        self.chat_enabled = bool(state.get("chat_enabled", self.chat_enabled))
        self.user_role = state.get("role", self.user_role)
        self.user_name = state.get("name", self.user_name)
        self.user_handle = state.get("handle", self.user_handle)
        self.member_id = state.get("member_id", self.member_id)

        # Server-authoritative live role update. A Host can promote/demote a
        # member without requiring logout or restart; the next poll applies it.
        if isinstance(self.brain, RemoteBrain):
            role_now = self.user_role
            self.btn_invite.setEnabled(role_now == "collaborator")
            self.btn_new.setEnabled(role_now == "collaborator" and not getattr(self, "_busy", False))
            self.user_badge.setText(" Remote (Collab) " if role_now == "collaborator" else " Remote (Guest) ")

        self.team_mode = mode
        self.team_brain_ready = ready
        self.team_brain_version = version
        self.team_brain_chunks = chunks

        if hasattr(self.brain, "workspace_context"):
            try:
                self.brain.team_mode = mode
                self.brain.workspace_context.update({
                    "identity": "CodeChat AI inside CodeChat Pro Team Edition",
                    "mode": mode,
                    "admin": state.get("admin", "Host"),
                    "file_count": state.get("file_count", 0),
                    "files": state.get("files", []),
                    "brain_version": version,
                })
            except Exception:
                pass

        if isinstance(self.brain, RemoteBrain):
            # Keep the RemoteBrain's local view aligned with the server.
            try:
                self.brain.team_mode = mode
                self.brain.brain_ready = ready
                self.brain.brain_version = version
                self.brain.chunks = list(range(chunks))
            except Exception:
                pass

        # Always mirror the server's mode in the Host UI too.
        self.btn_mode.blockSignals(True)
        self.btn_mode.setChecked(mode == "append")
        self.btn_mode.blockSignals(False)

        if mode == "append":
            self.btn_mode.setText("🔗 Append Mode")
            self.btn_mode.setStyleSheet(
                "color: #F59E0B; "
                "font-weight: bold; "
                "border: 1px solid #F59E0B;"
            )
        else:
            self.btn_mode.setText("⚡ Single Mode")
            self.btn_mode.setStyleSheet(
                "color: #aaa; "
                "font-style: italic; "
                "border: 1px dashed #444;"
            )

        self.update_chat_controls()

        if isinstance(self.brain, RemoteBrain):
            if ready and chunks > 0:
                self.unlock_ui()
            else:
                self.lock_ui()
                self.set_status("Waiting for Host Brain...")

    def get_team_state(self):
        try:
            if not isinstance(self.brain, RemoteBrain):
                # Host already owns the authoritative local CoreBrain.
                # Avoid authenticating the Host token as a normal user.
                return {
                    "mode": self.team_mode,
                    "brain_ready": bool(self.brain.chunks and len(self.brain.embeddings) > 0),
                    "brain_version": self.team_brain_version,
                    "chunks": len(self.brain.chunks)
                }

            response = requests.get(
                f"{self.brain.url}/team_state",
                headers={"x-access-token": self.brain.token},
                timeout=2
            )

            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Team state error: {e}")
        return None

    # --------------------------------------------------------
    # TEAM POLLING
    # --------------------------------------------------------

    def poll_updates(self):
        """Start exactly one polling QThread.

        During join/leave the poll is stopped first.  A generation number makes
        late results from an old TeamPollWorker harmless even if Qt delivers a
        queued signal after the worker has technically finished.
        """
        if self._closing or self._join_in_progress:
            return
        worker = getattr(self, "_poll_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    return
            except RuntimeError:
                self._poll_worker = None
                worker = None

        brain_ref = self.brain
        generation = self._poll_generation
        worker = TeamPollWorker(
            brain_ref,
            self.current_conversation_id,
            self.team_brain_version
        )
        self._poll_worker = worker
        worker._codechat_generation = generation
        worker.result_signal.connect(
            lambda data, w=worker, g=generation: self._apply_poll_result_guarded(data, w, g)
        )
        worker.error_signal.connect(
            lambda e, b=brain_ref, w=worker, g=generation:
                self._set_poll_error_guarded(e, b, w, g)
        )
        worker.finished.connect(
            lambda w=worker, g=generation: self._poll_finished(w, g)
        )
        worker.start()

    def _apply_poll_result_guarded(self, data, worker, generation):
        if self._closing or generation != self._poll_generation:
            return
        if worker is not getattr(self, "_poll_worker", None):
            return
        self._apply_poll_result(data)

    def _set_poll_error_guarded(self, error, brain_ref, worker, generation):
        if self._closing or generation != self._poll_generation:
            return
        self._set_poll_error(error, brain_ref)

    def _poll_finished(self, worker=None, generation=None):
        current = getattr(self, "_poll_worker", None)
        if worker is not None and current is not worker:
            try:
                worker.deleteLater()
            except Exception:
                pass
            return
        if current is worker or worker is None:
            self._poll_worker = None
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass

    def _stop_poll_worker(self, wait_ms=5000):
        """Synchronously retire the current poll before changing self.brain."""
        self._poll_generation += 1
        poll = getattr(self, "_poll_worker", None)
        if poll is None:
            return True
        try:
            if poll.isRunning():
                poll.requestInterruption()
                poll.quit()
                if not poll.wait(wait_ms):
                    # requests inside TeamPollWorker have short connect/read
                    # timeouts, so this should only be a last-resort diagnostic.
                    print("⚠️ Team poll did not stop within timeout.")
                    return False
        except RuntimeError:
            pass
        finally:
            if getattr(self, "_poll_worker", None) is poll:
                self._poll_worker = None
        return True

    def _set_poll_error(self, error, brain_ref):
        if brain_ref is self.brain:
            print(f"Team polling error: {error}")

    def _apply_poll_result(self, data):
        brain_ref = data.get("brain")
        if brain_ref is not self.brain:
            return
        if data.get("auth_error"):
            self.handle_team_revoked()
            return
        if data.get("offline"):
            self.set_status("Team server unavailable — retrying...")
            return

        # If another team member changed the authoritative Brain, refresh the
        # Host's local copy before allowing another upload/query against it.
        if data.get("brain_bytes") and isinstance(self.brain, CoreBrain):
            try:
                fd, temp_path = tempfile.mkstemp(prefix="codechat_remote_", suffix=".brain")
                with os.fdopen(fd, "wb") as f:
                    f.write(data["brain_bytes"])
                load_res = self.brain.load_snapshot(temp_path)
                os.remove(temp_path)
                if "Success" not in load_res:
                    self.set_status("Team Brain refresh failed")
                    return
            except Exception as e:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
                self.set_status(f"Team Brain refresh failed: {e}")
                return

        state = data.get("state") or {}
        self.apply_team_state(state)
        history = list(data.get("history", []))
        # Do not let a poll containing the pre-log server state erase a freshly
        # answered Host Team Stream entry.
        if not isinstance(self.brain, RemoteBrain):
            pending = list(getattr(self, "_pending_host_stream", []))
            if pending:
                acknowledged = [
                    p for p in pending
                    if any(
                        str(h.get("query", "")) == p["query"]
                        and str(h.get("answer", "")) == p["answer"]
                        for h in history
                    )
                ]
                if acknowledged:
                    self._pending_host_stream = [
                        p for p in pending if p not in acknowledged
                    ]
                    pending = list(self._pending_host_stream)
                existing_pairs = {
                    (str(h.get("query", "")), str(h.get("answer", "")))
                    for h in history
                }
                for p in pending:
                    if (p["query"], p["answer"]) not in existing_pairs:
                        history.append({
                            "user": self.user_name or "Host",
                            "query": p["query"],
                            "answer": p["answer"]
                        })
        html_out = ""
        for h in history:
            user = html.escape(str(h.get("user", "Unknown")))
            query = html.escape(str(h.get("query", "")))
            answer = html.escape(str(h.get("answer", "")))
            color = "#5865F2" if "HOST" in user.upper() else "#F59E0B"
            html_out += f"""<div style='margin-bottom:15px;padding:10px;background:#0f0f0f;border-radius:8px;border:1px solid #222;'>
<div style='color:{color};font-size:10px;font-weight:bold;margin-bottom:5px;'>👤 {user}</div>
<div style='background:#1a1a1a;padding:8px;border-radius:6px;margin-bottom:5px;color:#ccc;'><b>Q:</b> {query}</div>
<div style='background:#111;padding:8px;border-radius:6px;color:#aaa;white-space:pre-wrap;'><b style='color:#5865F2;'>AI:</b> {answer}</div></div>"""
        if self.team_view.toHtml() != html_out:
            sb = self.team_view.verticalScrollBar(); at_bottom = sb.value() >= sb.maximum() - 20; old = sb.value()
            self.team_view.setHtml(html_out)
            sb.setValue(sb.maximum() if at_bottom else min(old, sb.maximum()))

        if "conversations" in data:
            conv_data = data["conversations"] or {}
            self.chat_enabled = bool(conv_data.get("enabled", self.chat_enabled))
            self.chat_conversations = conv_data.get("conversations", [])
            self.update_chat_controls()
            self._render_chat_conversations()
        # A poll can finish after the user has switched conversations. Only
        # render messages if they belong to the conversation currently visible.
        if (
            self.tabs.currentIndex() == 2
            and self.chat_enabled
            and "messages" in data
            and data.get("messages_conversation_id") == self.current_conversation_id
        ):
            self._render_chat_messages(data.get("messages", []))

        # Notifications are independent from Brain readiness.
        if self.tabs.currentIndex() == 2 or data.get("conversations") is not None:
            self._poll_notifications()

        users = data.get("users", [])
        self.user_list.clear()
        from PyQt6.QtWidgets import QListWidgetItem
        host_item = QListWidgetItem("👤 Host (Admin) · @host")
        host_item.setData(Qt.ItemDataRole.UserRole, "host")
        self.user_list.addItem(host_item)
        for u in users:
            if u.get("member_id") == "host":
                continue
            prefix = "🟢" if u.get("online") else "⚫"
            role = u.get("role", "guest").title()
            item = QListWidgetItem(f"{prefix} {u.get('name', 'Unknown')} · @{u.get('handle', '')} · {role}")
            item.setData(Qt.ItemDataRole.UserRole, u.get("member_id"))
            self.user_list.addItem(item)

        if "live_history" in data and self.selected_history_id:
            live_id = data.get("live_history", {}).get("session_id")
            if live_id == self.selected_history_id:
                self._render_history_document(data.get("live_history", {}))

        if "history_sessions" in data:
            self.history_sessions = data.get("history_sessions", [])
            if not self.selected_history_id or not any(x.get("session_id") == self.selected_history_id for x in self.history_sessions):
                live = next((x for x in self.history_sessions if x.get("live")), None)
                self.selected_history_id = live.get("session_id") if live else (self.history_sessions[0].get("session_id") if self.history_sessions else None)
            self._render_history_sessions()
        if self.tabs.currentIndex() == 3:
            self.refresh_selected_history()

    def show_user_details(self, item):
        member_id = item.data(Qt.ItemDataRole.UserRole)
        if not member_id:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                url = f"{self.brain.url}/members/{member_id}/details"; headers = self.brain.headers
            else:
                url = f"http://127.0.0.1:8000/members/{member_id}/details"; headers = {"x-access-token": host_token or ""}
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code != 200:
                # A member-list race should not make a valid user look missing.
                # Refresh the authoritative team state once before failing.
                try:
                    sr = requests.get(
                        (f"{self.brain.url}/team_state" if isinstance(self.brain, RemoteBrain) else "http://127.0.0.1:8000/team_state"),
                        headers=(self.brain.headers if isinstance(self.brain, RemoteBrain) else {"x-access-token": host_token or ""}),
                        timeout=3
                    )
                    if sr.status_code == 200:
                        member = next((m for m in sr.json().get("members", []) if m.get("member_id") == member_id), None)
                        if member:
                            d = member
                            d["status"] = member.get("status", "active")
                        else:
                            raise Exception(r.json().get("detail", r.text))
                    else:
                        raise Exception(r.json().get("detail", r.text))
                except Exception:
                    raise Exception(r.json().get("detail", r.text))
            else:
                d = r.json()
            groups = d.get("groups", [])
            group_text = ", ".join(f"{g.get('name')} (@{g.get('handle')})" for g in groups) or "None"
            online = "🟢 Online" if d.get("online") else "⚫ Offline"
            details = (
                f"<h2>{html.escape(str(d.get('name','Unknown')))}</h2>"
                f"<p><b>Handle:</b> @{html.escape(str(d.get('handle','')))}</p>"
                f"<p><b>Role:</b> {html.escape(str(d.get('role','')).title())}</p>"
                f"<p><b>Status:</b> {online}</p>"
                f"<p><b>Member ID:</b> {html.escape(str(d.get('member_id','')))}</p>"
                f"<p><b>Invited by:</b> {html.escape(str(d.get('invited_by','Not available')))}</p>"
                f"<p><b>Invited at:</b> {html.escape(str(d.get('invited_at','Not available')))}</p>"
                f"<p><b>Joined at:</b> {html.escape(str(d.get('joined_at','Not available')))}</p>"
                f"<p><b>Groups:</b> {html.escape(group_text)}</p>"
            )
            if d.get("removed_by"):
                details += f"<p><b>Removed by:</b> {html.escape(str(d['removed_by']))} · {html.escape(str(d.get('removed_at','')))}</p>"
            if d.get("left_at"):
                details += f"<p><b>Left at:</b> {html.escape(str(d['left_at']))}</p>"
            QMessageBox.information(self, "Member Details", details)
        except Exception as e:
            QMessageBox.warning(self, "Member Details", f"Could not load member details.\n\n{e}")

    def _render_history_sessions(self):
        if not hasattr(self, "history_list"): return
        self.history_list.blockSignals(True)
        self.history_list.clear()
        selected = 0
        for i, item in enumerate(self.history_sessions):
            label = ("🟢 LIVE · " if item.get("live") else "🕘 ") + item.get("name", item.get("session_id", "History"))
            if not item.get("live"):
                label += f"\n{item.get('started_at','')}"
            self.history_list.addItem(label)
            if item.get("session_id") == self.selected_history_id: selected = i
        if self.history_list.count(): self.history_list.setCurrentRow(selected)
        self.history_list.blockSignals(False)

    def select_history_session(self, row):
        if row < 0 or row >= len(self.history_sessions): return
        self.selected_history_id = self.history_sessions[row].get("session_id")
        self.refresh_selected_history()

    def refresh_selected_history(self):
        if not self.selected_history_id or not hasattr(self, "history_view"): return
        try:
            if isinstance(self.brain, RemoteBrain):
                url = f"{self.brain.url}/history/session/{self.selected_history_id}"; headers = self.brain.headers
            else:
                url = f"http://127.0.0.1:8000/history/session/{self.selected_history_id}"; headers = {"x-access-token": host_token or ""}
            r = requests.get(url, headers=headers, timeout=4)
            if r.status_code != 200: raise Exception(r.json().get("detail", r.text))
            d = r.json()
            self._render_history_document(d)
        except Exception as e:
            self.history_view.setHtml(f"<h3>History unavailable</h3><p>{html.escape(str(e))}</p>")

    def _render_history_document(self, d):
        started = str(d.get("started_at", ""))
        creator = str(d.get("created_by", "Host"))
        status = "🟢 LIVE SESSION" if d.get("live") else "🕘 ARCHIVED SESSION"
        out = f"<h2>{status}</h2>"
        out += f"<h3>Host/session started by: {html.escape(creator)}</h3>"
        out += f"<p style='color:#888'><b>Started:</b> {html.escape(started)} · <b>Updated:</b> {html.escape(str(d.get('updated_at','')))}</p>"
        out += f"<p style='color:#666'>Session ID: {html.escape(str(d.get('session_id','History')))}</p>"
        out += "<h3>👥 Team Activity</h3>"
        for ev in d.get("events", []):
            details = ev.get("details", {}) or {}
            compact = ", ".join(f"{html.escape(str(k))}: {html.escape(str(v))}" for k,v in details.items() if k != "invite_token_created")
            out += f"<div style='padding:7px;margin:5px 0;border-left:2px solid #5865F2;background:#101419'><b>{html.escape(str(ev.get('type','event')).replace('_',' ').title())}</b> · {html.escape(str(ev.get('actor','Unknown')))}"
            if ev.get("target"): out += f" → {html.escape(str(ev.get('target')))}"
            if compact: out += f" <span style='color:#888'>({compact})</span>"
            out += f" <span style='color:#555'>· {html.escape(str(ev.get('timestamp','')))}</span></div>"
        out += "<h3>🤖 Public AI / Team Conversations</h3>"
        for h in d.get("public_ai", []):
            out += f"<div style='margin:8px 0;padding:9px;background:#11151a'><b>{html.escape(str(h.get('user','Unknown')))}</b> · {html.escape(str(h.get('timestamp','')))}<br><b>Q:</b> {html.escape(str(h.get('query','')))}<br><b style='color:#5865F2'>AI:</b> {html.escape(str(h.get('answer','')))}</div>"
        out += "<h3>💬 Chat History</h3>"
        for m in d.get("chat_messages", []):
            cid = html.escape(str(m.get('conversation_id','')))
            out += f"<div style='padding:7px;margin:4px 0;background:#0f1318'><b>{html.escape(str(m.get('sender','Unknown')))}</b> <span style='color:#777'>[{cid}] · {html.escape(str(m.get('timestamp','')))}</span><br>{html.escape(str(m.get('message','')))}</div>"
        if not d.get("events") and not d.get("public_ai") and not d.get("chat_messages"):
            out += "<p style='color:#888'>No recorded activity in this session.</p>"
        self.history_view.setHtml(out)

    def _render_chat_conversations(self):
        if not hasattr(self, "chat_conversation_list"): return
        self.chat_conversation_list.blockSignals(True); self.chat_conversation_list.clear()
        selected = 0
        for i, conv in enumerate(self.chat_conversations):
            prefix = {"team":"# ","dm":"👤 ","group":"👥 "}.get(conv.get("type"), "")
            cid = conv.get("id", "")
            unread = int(getattr(self, "_unread_counts", {}).get(cid, 0) or 0)
            label = prefix + conv.get("name", cid or "Chat")
            if unread:
                label += f"  🔴 {unread}"
            self.chat_conversation_list.addItem(label)
            if conv.get("id") == self.current_conversation_id: selected = i
        if self.chat_conversation_list.count(): self.chat_conversation_list.setCurrentRow(selected)
        self.chat_conversation_list.blockSignals(False)

    def _render_chat_messages(self, messages):
        html_out = ""
        for m in messages:
            mine = m.get("sender_id") == self.member_id
            align = "right" if mine else "left"; bg = "#005c4b" if mine else "#1f1f1f"
            sender = html.escape(str(m.get("sender", "Unknown"))); handle = html.escape(str(m.get("handle", "")))
            stamp = html.escape(str(m.get("timestamp", ""))); body = html.escape(str(m.get("message", "")))
            html_out += f"<div style='text-align:{align};margin:8px 0;'><div style='display:inline-block;background:{bg};padding:10px;border-radius:10px;max-width:75%;'><div style='font-size:10px;color:#888;font-weight:bold;'>{sender} @{handle} · {stamp}</div><div style='color:#eee;margin-top:4px;white-space:pre-wrap;'>{body}</div></div></div>"
        self.team_chat_view.setHtml(html_out)
        self.team_chat_view.verticalScrollBar().setValue(self.team_chat_view.verticalScrollBar().maximum())

    # --------------------------------------------------------
    # QUESTIONS
    # --------------------------------------------------------

    def ask_text(self):

        t = self.inp.text().strip()

        if not t:
            return

        if self.tabs.currentIndex() == 2:
            self.send_team_chat()
        else:
            self.handle_question(t)

    def handle_question(self, text):
        if getattr(self, "_busy", False):
            return

        is_public = (
            self.tabs.currentIndex() == 1
        )

        self.inp.clear()

        self.set_status(
            "🤔 Thinking..."
        )

        if (
            self.voice_thread
            and self.voice_thread.isRunning()
        ):

            self.voice_thread.pause()

        if not is_public:

            self.add_msg(
                text,
                "user"
            )

        formatted_history = []

        for msg in self.chat_history_log[-6:]:

            role = (
                'user'
                if msg['type'] == 'user'
                else 'assistant'
            )

            formatted_history.append({
                'role': role,
                'content': msg['content']
            })

        self.worker = self._register_task_worker(TaskWorker(
            self.brain,
            "query",
            text,
            public_flag=is_public,
            history=formatted_history
        ))

        self.worker.result_signal.connect(
            lambda r, q=text: self.finish_query(
                r,
                is_public,
                q
            )
        )

        self.worker.start()

    def finish_query(
        self,
        result,
        is_public,
        query_text=""
    ):

        if isinstance(
            result,
            str
        ):

            ans = result
            srcs = []

        elif (
            isinstance(result, tuple)
            and len(result) == 2
        ):

            ans, srcs = result

        else:

            ans = (
                "Error: Invalid response format"
            )

            srcs = []

        self._busy = False
        self.set_status("Ready")
        if self.tabs.currentIndex() != 2 and (not isinstance(self.brain, RemoteBrain) or self.team_brain_ready):
            self.btn_send.setEnabled(True)

        if not isinstance(ans, str):
            ans = str(ans)

        if not is_public:

            self.add_msg(
                ans,
                "ai",
                srcs
            )
        else:
            # Team Stream is a shared/public AI view.  Host queries are served
            # locally by CoreBrain, so do not wait for the next 5-second poll
            # before showing the Host's own answer. The server also records the
            # same entry via /host_log, and the next poll reconciles the view.
            try:
                user_name = html.escape(str(self.user_name or "Host"))
                safe_query = html.escape(str(query_text))
                safe_answer = html.escape(str(ans))
                existing = self.team_view.toHtml()
                entry = (
                    f"<div style='margin-bottom:15px;padding:10px;background:#0f0f0f;border-radius:8px;border:1px solid #222;'>"
                    f"<div style='color:#5865F2;font-size:10px;font-weight:bold;margin-bottom:5px;'>👤 {user_name}</div>"
                    f"<div style='background:#1a1a1a;padding:8px;border-radius:6px;margin-bottom:5px;color:#ccc;'><b>Q:</b> {safe_query}</div>"
                    f"<div style='background:#111;padding:8px;border-radius:6px;color:#aaa;white-space:pre-wrap;'><b style='color:#5865F2;'>AI:</b> {safe_answer}</div></div>"
                )
                self.team_view.setHtml(existing + entry)
                sb = self.team_view.verticalScrollBar()
                sb.setValue(sb.maximum())
                if not isinstance(self.brain, RemoteBrain):
                    self._pending_host_stream.append({
                        "query": str(query_text),
                        "answer": str(ans)
                    })
                    self._pending_host_stream = self._pending_host_stream[-20:]
            except Exception as exc:
                print(f"Team Stream immediate-render warning: {exc}")

        if (
            self.voice_thread
            and self.voice_thread.isRunning()
        ):

            self.voice_thread.resume()

    # --------------------------------------------------------
    # VOICE
    # --------------------------------------------------------

    def toggle_voice(self):

        if self.btn_voice.isChecked():

            self.voice_thread = VoiceLoop()

            self.voice_thread.update_status.connect(
                lambda s: self.set_status(s)
            )

            self.voice_thread.speech_recognized.connect(
                self.handle_question
            )

            self.voice_thread.start()

        else:

            self.voice_thread.stop()

            self.set_status(
                "Ready"
            )

    def confirm_change(self, title, message):
        return QMessageBox.question(
            self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    # --------------------------------------------------------
    # INGEST
    # --------------------------------------------------------

    def do_ingest(self):
        if getattr(self, "_busy", False):
            return

        is_append = self.team_mode == "append" if isinstance(self.brain, RemoteBrain) else self.btn_mode.isChecked()

        if is_append:
            files, _ = QFileDialog.getOpenFileNames(
                self, "Add Files to Project", "", 
                "All Supported Files (*);;All Files (*)"
            )
        else:
            file, _ = QFileDialog.getOpenFileName(
                self, "Select Project File", "",
                "All Supported Files (*);;All Files (*)"
            )
            files = [file] if file else []

        if not files:
            return

        # Single Mode is intentionally destructive: the selected file becomes
        # the entire Brain. Append Mode adds all selected files.
        if is_append:
            action = f"Add {len(files)} file{'s' if len(files) != 1 else ''} to the shared project?"
        else:
            action = f"Replace the current Brain with:\n\n{os.path.basename(files[0])}\n\nAll previous project files will be forgotten."

        if not self.confirm_change("Confirm Project Change", action):
            return

        self._busy = True
        self.btn_new.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.btn_mode.setEnabled(False)
        self.set_status("Indexing...")

        self.worker = self._register_task_worker(TaskWorker(
            self.brain,
            "ingest",
            files,
            is_append,
            extra="publish_team" if isinstance(self.brain, CoreBrain) else None
        ))
        self.worker.msg_signal.connect(self.set_status)
        self.worker.result_signal.connect(self.finish_ingest)
        self.worker.start()

    def finish_ingest(self, res):
        if "Success" not in str(res) and "Indexed" not in str(res) and "uploaded successfully" not in str(res).lower():
            self._busy = False
            self.btn_new.setEnabled(True)
            self.btn_mode.setEnabled(not isinstance(self.brain, RemoteBrain))
            self.set_status("Upload Failed")
            QMessageBox.warning(self, "Project Update Failed", str(res))
            return

        self._busy = False
        # Uploads are not session boundaries. Keep the current AI conversation alive.
        # Only a fresh application launch starts a new AI session.
        self.btn_new.setEnabled(True)
        self.btn_mode.setEnabled(not isinstance(self.brain, RemoteBrain))
        self.set_status("Ready")
        self.unlock_ui()
        self.add_msg(f"✅ {res}", "ai")
        self.poll_updates()

    def finish_ingest_sync(self, res):
        self._busy = False
        self.btn_mode.setEnabled(not isinstance(self.brain, RemoteBrain))
        if "SUCCESS" in res:
            self.set_status("Ready")
            self.poll_updates()
        else:
            self.set_status("Sync Error")
            QMessageBox.warning(
                self,
                "Team Brain Sync Error",
                str(res)
            )

    def refresh_team_state_once(self):
        self.poll_updates()

    # --------------------------------------------------------
    # SYNC
    # --------------------------------------------------------

    def do_sync(self):
        if getattr(self, "_busy", False):
            return
        if not self.confirm_change(
            "Confirm Team Sync",
            "Publish the current local Brain to the Team Server and replace the shared Team Brain?"
        ):
            return

        self._busy = True
        self.set_status("Syncing...")

        self.worker = self._register_task_worker(TaskWorker(
            self.brain,
            "sync_server"
        ))

        self.worker.result_signal.connect(
            self.finish_sync
        )

        self.worker.start()

    def finish_sync(self, res):
        self._busy = False

        if "SUCCESS" in res:

            self.set_status(
                "Synced"
            )

            QMessageBox.information(
                self,
                "Sync",
                "✅ Server Updated."
            )

        else:

            self.set_status(
                "Error"
            )

            QMessageBox.warning(
                self,
                "Sync Error",
                res
            )

    # --------------------------------------------------------
    # INVITE
    # --------------------------------------------------------

    def send_invite(self):
        dlg = InviteDialog()
        if not dlg.exec():
            return

        name, role = dlg.get_data()
        if not name.strip():
            return
        if not self.confirm_change(
            "Confirm Invitation",
            f"Invite {name.strip()} as {role.title()}?"
        ):
            return

        self.set_status("Inviting...")
        self.worker = self._register_task_worker(TaskWorker(
            self.brain,
            "invite",
            name,
            extra=role
        ))
        self.worker.result_signal.connect(self.show_invite_popup)
        self.worker.start()

    def show_invite_popup(self, result_str):
        self.set_status("Ready")
        if result_str.startswith("SUCCESS|"):
            TokenPopup(
                result_str.split("|", 1)[1],
                public_url if not isinstance(self.brain, RemoteBrain) else self.brain.url
            ).exec()
            return
        QMessageBox.critical(self, "Invite Error", result_str)

    # --------------------------------------------------------
    # JOIN TEAM
    # --------------------------------------------------------

    def toggle_join(self):
        """Join/leave without blocking the GUI or racing Team polling."""
        if self.btn_join.isChecked():
            if self._join_in_progress or isinstance(self.brain, RemoteBrain):
                return
            if not self.confirm_change(
                "Join Team",
                "Join this Team? Your account will become an active team member."
            ):
                self.btn_join.blockSignals(True)
                self.btn_join.setChecked(False)
                self.btn_join.blockSignals(False)
                return

            saved = load_saved_teams()
            url = token = ""
            if saved:
                # Give users explicit control over remembered credentials.
                # Deleting here only removes the local saved login; it does NOT
                # remove the member from the Team server.
                while True:
                    labels = [f"{t.get('name','Team')}  ·  {t.get('role','guest').title()}  ·  {t.get('url','')}" for t in saved]
                    labels.append("➕ Join a New Team (Invite)")
                    labels.append("🗑 Delete a Saved Team Login")
                    choice, ok = QInputDialog.getItem(self, "Team Login", "Choose a remembered team, join a new server, or delete a saved login:", labels, 0, False)
                    if not ok:
                        self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                    idx = labels.index(choice)
                    if idx < len(saved):
                        url = saved[idx].get("url", "").strip()
                        token = saved[idx].get("token", "").strip()
                        break
                    if idx == len(saved):
                        url, ok1 = QInputDialog.getText(self, "New Team", "Host URL:")
                        if not ok1 or not url.strip():
                            self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                        token, ok2 = QInputDialog.getText(self, "Invite Login", "Invite token:")
                        if not ok2 or not token.strip():
                            self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                        break
                    # Delete selected saved login.
                    del_idx, ok_del = QInputDialog.getItem(self, "Delete Saved Login", "Select the saved team login to delete:", [f"{t.get('name','Team')}  ·  {t.get('url','')}" for t in saved], 0, False)
                    if ok_del:
                        d_idx = [f"{t.get('name','Team')}  ·  {t.get('url','')}" for t in saved].index(del_idx)
                        removed = saved.pop(d_idx)
                        save_saved_teams(saved)
                        QMessageBox.information(self, "Saved Login Deleted", f"Removed the saved login for {removed.get('name','Team')}.\n\nThis only deletes the credential from this computer; it does not remove you from the Team.")
                        if not saved:
                            url, ok1 = QInputDialog.getText(self, "New Team", "Host URL:")
                            if not ok1 or not url.strip():
                                self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                            token, ok2 = QInputDialog.getText(self, "Invite Login", "Invite token:")
                            if not ok2 or not token.strip():
                                self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                            break
            else:
                url, ok1 = QInputDialog.getText(self, "New Team", "Host URL:")
                if not ok1 or not url.strip():
                    self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return
                token, ok2 = QInputDialog.getText(self, "Invite Login", "Invite token:")
                if not ok2 or not token.strip():
                    self.btn_join.blockSignals(True); self.btn_join.setChecked(False); self.btn_join.blockSignals(False); return

            self._join_in_progress = True
            self._stop_poll_worker()
            self.btn_join.setEnabled(False)
            self.set_status("Connecting to Team…")
            job = JoinJob(url.strip(), token.strip())
            self._join_job = job
            job.signals.result.connect(self._finish_join)
            job.signals.error.connect(self._join_failed)
            job.signals.finished.connect(lambda j=job: self._join_job_finished(j))
            self._chat_pool.start(job)
            return

        # Leave: stop polling before replacing RemoteBrain with CoreBrain.
        if self._join_in_progress:
            return
        old_brain = self.brain
        self._stop_poll_worker()
        if isinstance(old_brain, RemoteBrain):
            try:
                requests.post(
                    f"{old_brain.url.rstrip('/')}/leave",
                    headers={"x-access-token": old_brain.token},
                    timeout=(1, 3)
                )
            except Exception as exc:
                print(f"Leave notification warning: {exc}")

        self.brain = CoreBrain()
        self.is_team_client = False
        self.user_role = "host"
        self.user_name = "Host"
        self.user_handle = "host"
        self.member_id = "host"
        self.chat_enabled = True
        self.team_mode = "single"
        self.team_brain_ready = False
        self.team_brain_version = 0
        self.team_brain_chunks = 0
        self.current_conversation_id = "team"
        self.chat_conversations = []

        self.user_badge.setText(" Local Host ")
        self.user_badge.setStyleSheet(
            f"background-color: {STATUS_LOCAL}; color: white; "
            "border-radius: 4px; padding: 5px;"
        )
        self.btn_mode.setEnabled(True)
        self.btn_save_chat.setEnabled(True)
        self.btn_load_chat.setEnabled(True)
        self.btn_save_brain.setEnabled(True)
        self.btn_load_brain.setEnabled(True)
        self.btn_join.setText("🌐 Join Team")
        self.btn_invite.setEnabled(True)
        self.btn_manage_members.setEnabled(True)
        self.btn_chat_toggle.setEnabled(True)
        self.btn_new_group.setEnabled(True)
        self.btn_sync.setEnabled(True)
        self.btn_new.setEnabled(True)
        self.lock_ui()
        self.set_status("Local Host")

    def _join_job_finished(self, job):
        if self._join_job is job:
            self._join_job = None
        try:
            job.deleteLater()
        except Exception:
            pass

    def _join_failed(self, error):
        self._join_in_progress = False
        self.btn_join.setEnabled(True)
        self.btn_join.blockSignals(True)
        self.btn_join.setChecked(False)
        self.btn_join.blockSignals(False)
        self.set_status("Join failed")
        self.add_msg(f"❌ Connection Failed: {error}", "ai")

    def _finish_join(self, payload):
        if self._closing or not self._join_in_progress:
            return
        try:
            url = payload["url"]
            token = payload["token"]
            role_data = payload["role"]
            state = payload["state"]

            # The old poll is already stopped. Only now is it safe to swap the
            # Brain reference used by every future operation.
            self.brain = RemoteBrain(url, token)
            self.is_team_client = True
            self.user_role = role_data.get("role", "guest")
            self.user_name = role_data.get("name", "Team Member")
            self.user_handle = role_data.get("handle", "")
            self.member_id = role_data.get("member_id")
            remember_team(url, token, self.user_name, self.user_role, self.member_id)

            self.btn_mode.setEnabled(False)
            self.btn_save_chat.setEnabled(True)
            self.btn_load_chat.setEnabled(False)
            self.btn_save_brain.setEnabled(True)
            self.btn_load_brain.setEnabled(False)

            role = self.user_role
            if role == "collaborator":
                self.user_badge.setText(" Remote (Collab) ")
                self.user_badge.setStyleSheet(
                    f"background-color: {STATUS_COLLAB}; color: black; "
                    "border-radius: 4px; padding: 5px; font-weight:bold;"
                )
                self.btn_invite.setEnabled(True)
                self.btn_new.setEnabled(not getattr(self, "_busy", False))
            else:
                self.user_badge.setText(" Remote (Guest) ")
                self.user_badge.setStyleSheet(
                    f"background-color: {STATUS_GUEST}; color: white; "
                    "border-radius: 4px; padding: 5px; font-weight:bold;"
                )
                self.btn_invite.setEnabled(False)
                self.btn_new.setEnabled(False)

            self.btn_manage_members.setEnabled(False)
            self.btn_chat_toggle.setEnabled(False)
            self.btn_new_group.setEnabled(True)
            self.btn_sync.setEnabled(False)
            self.btn_join.setText("❌ Leave Team")

            self.apply_team_state(state)
            self._join_in_progress = False
            self.btn_join.setEnabled(True)

            if self.team_brain_ready and self.team_brain_chunks > 0:
                self.unlock_ui()
                self.set_status(f"Connected as {role.title()}")
            else:
                self.lock_ui()
                self.set_status("Waiting for Host Brain…")

            self.add_msg(f"Connected as {role.title()}.", "ai")
            self.poll_updates()
        except Exception as exc:
            self._join_failed(f"Join setup failed: {exc}")

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    def closeEvent(self, event):
        self._closing = True
        try:
            self.team_timer.stop()
        except Exception:
            pass

        # Invalidate every in-flight poll before closing widgets.
        self._stop_poll_worker(wait_ms=5000)

        # QRunnable chat/join jobs do not own QThread wrappers. Waiting here
        # prevents late signal delivery into a destroyed window.
        try:
            self._chat_pool.waitForDone(5000)
        except Exception:
            pass

        if (
            hasattr(self, 'brain')
            and isinstance(
                self.brain,
                RemoteBrain
            )
        ):

            try:

                requests.post(
                    f"{self.brain.url}/logout",
                    headers={
                        "x-access-token":
                        self.brain.token
                    },
                    timeout=1
                )

            except:
                pass

        if self.voice_thread:
            try:
                self.voice_thread.stop()
                self.voice_thread.wait(5000)
            except Exception:
                pass

        # TaskWorkers are also QThreads. Keep every wrapper alive and wait
        # for all native threads before the window/application is destroyed.
        for w in list(getattr(self, "_task_workers", set())):
            try:
                if w is not None and w.isRunning():
                    w.requestInterruption()
                    w.quit()
                    w.wait(5000)
            except Exception:
                pass

        stop_server()
        stop_ngrok()

        event.accept()

    # --------------------------------------------------------
    # CHAT
    # --------------------------------------------------------

    def add_msg(
        self,
        text,
        type="user",
        srcs=None
    ):

        self.chat_history_log.append({
            "type": type,
            "content": text,
            "srcs": srcs
        })

        align = (
            "right"
            if type == "user"
            else "left"
        )

        bg = (
            "#005c4b"
            if type == "user"
            else "#1f1f1f"
        )

        content = text

        if type == "ai":

            content = markdown.markdown(
                text,
                extensions=[
                    'fenced_code',
                    'codehilite'
                ],
                extension_configs={
                    'codehilite': {
                        'noclasses': True,
                        'pygments_style': 'monokai'
                    }
                }
            )

        if srcs:

            content += (
                f"<br><hr style="
                f"'border:0; "
                f"border-top:1px solid #444'>"
                f"<small style="
                f"'color:{TEXT_GRAY}'>"
                f"Ref: {len(srcs)} sources"
                f"</small>"
            )

        html = f"""
        <table width="100%" cellpadding="5">
            <tr>
                <td align="{align}">
                    <div style="
                    background-color:{bg};
                    color:#e9edef;
                    padding:15px;
                    border-radius:15px;
                    max-width:600px;">
                        {content}
                    </div>
                </td>
            </tr>
        </table>
        """

        self.chat.append(
            html
        )

        self.chat.verticalScrollBar().setValue(
            self.chat.verticalScrollBar().maximum()
        )

    # --------------------------------------------------------
    # SAVE SESSION
    # --------------------------------------------------------

    def do_save_chat(self):

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Session",
            f"session_"
            f"{datetime.now().strftime('%Y%m%d_%H%M')}"
            f".ccsession",
            "CodeChat Session (*.ccsession)"
        )

        if path:

            self.set_status(
                "Saving Session..."
            )

            self.worker = self._register_task_worker(TaskWorker(
                self.brain,
                "save_session",
                path,
                extra=self.chat_history_log
            ))

            self.worker.result_signal.connect(
                lambda res:
                self.set_status(
                    "Session Saved"
                    if res == "Success"
                    else f"Error: {res}"
                )
            )

            self.worker.start()

    # --------------------------------------------------------
    # LOAD SESSION
    # --------------------------------------------------------

    def do_load_chat(self):

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Session",
            "",
            "CodeChat Session (*.ccsession)"
        )

        if path:
            if not self.confirm_change(
                "Confirm Session Load",
                "Loading this session will replace the current project Brain and chat history. Continue?"
            ):
                return

            self.set_status(
                "Loading Session..."
            )

            self.worker = self._register_task_worker(TaskWorker(
                self.brain,
                "load_session",
                path
            ))

            self.worker.result_signal.connect(
                self.finish_load_session
            )

            self.worker.start()

    def finish_load_session(
        self,
        res
    ):

        if isinstance(
            res,
            list
        ):

            self.chat.clear()

            self.chat_history_log = []

            for msg in res:

                self.add_msg(
                    msg['content'],
                    msg['type'],
                    msg.get('srcs')
                )

            # An explicitly loaded session is allowed to restore its Brain.
            # Publish it so the server and every collaborator use exactly the
            # same restored evidence set; this is the ONLY normal path by which
            # an older session's AI context becomes active again.
            if isinstance(self.brain, CoreBrain):
                self.set_status("Session Loaded — Publishing Brain...")
                self.sync_worker = self._register_task_worker(TaskWorker(self.brain, "sync_server"))
                self.sync_worker.result_signal.connect(
                    lambda sync_res: self._finish_loaded_session_sync(sync_res)
                )
                self.sync_worker.start()
            else:
                self.set_status("Session Loaded")
                self.unlock_ui()

            self.btn_save_brain.setEnabled(True)

        else:

            self.set_status(
                "Error"
            )

            QMessageBox.critical(
                self,
                "Load Failed",
                str(res)
            )

    def _finish_loaded_session_sync(self, res):
        if "SUCCESS" in str(res):
            self.chat_history_log = list(self.chat_history_log)
            self.set_status("Session Loaded & Team Brain Restored")
            self.unlock_ui()
            self.refresh_team_state_once()
        else:
            self.set_status("Session Brain Sync Failed")
            QMessageBox.warning(self, "Session Loaded Locally", str(res))
            self.unlock_ui()

    # --------------------------------------------------------
    # SAVE BRAIN
    # --------------------------------------------------------

    def do_save_brain(self):

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Brain",
            "",
            "Brain (*.brain)"
        )

        if path:

            self.set_status(
                "Saving Brain..."
            )

            self.worker = self._register_task_worker(TaskWorker(
                self.brain,
                "save_brain",
                path
            ))

            self.worker.result_signal.connect(
                lambda res:
                self.set_status(
                    "Brain Saved"
                    if res == "Success"
                    else f"Error: {res}"
                )
            )

            self.worker.start()

    # --------------------------------------------------------
    # LOAD BRAIN
    # --------------------------------------------------------

    def do_load_brain(self):
        if isinstance(self.brain, RemoteBrain):
            QMessageBox.information(
                self,
                "Team Brain",
                "Only the Host can load a .brain file.\n"
                "Once the Host loads it, the Team Brain is published automatically."
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Brain",
            "",
            "Brain (*.brain)"
        )

        if path:
            if not self.confirm_change(
                "Confirm Brain Load",
                "Loading this Brain will replace the current local project Brain. Continue?"
            ):
                return
            if getattr(self, "_busy", False):
                return
            self._busy = True
            self.set_status("Loading Brain...")
            self.worker = self._register_task_worker(TaskWorker(
                self.brain,
                "load_brain",
                path
            ))
            self.worker.result_signal.connect(
                self.finish_load_brain
            )
            self.worker.start()

    def finish_load_brain(self, res):
        if res == "Success":
            self.chat_history_log = []
            self.chat.clear()
            self.set_status("Publishing Team Brain...")
            self.btn_save_brain.setEnabled(True)

            # Loading a .brain is immediately synchronized to the server.
            self.sync_worker = self._register_task_worker(TaskWorker(
                self.brain,
                "sync_server"
            ))
            self.sync_worker.result_signal.connect(
                self.finish_load_brain_sync
            )
            self.sync_worker.start()
        else:
            self._busy = False
            self.set_status("Load Failed")
            QMessageBox.critical(
                self,
                "Load Failed",
                str(res)
            )

    def finish_load_brain_sync(self, res):
        if "SUCCESS" in res:
            self._busy = False
            self.set_status("Team Brain Ready")
            self.unlock_ui()
            self.add_msg(
                "✅ Brain loaded and shared with the entire team.",
                "ai"
            )
            self.refresh_team_state_once()
        else:
            self._busy = False
            self.set_status("Sync Failed")
            QMessageBox.critical(
                self,
                "Team Brain Sync Failed",
                str(res)
            )


# ============================================================
# APPLICATION ENTRY POINT
# ============================================================

if __name__ == "__main__":

    # ========================================================
    # SERVER MODE
    # ========================================================
    #
    # This is extremely important.
    #
    # When the GUI launches:
    #
    #     CodeChat.exe
    #
    # it starts:
    #
    #     CodeChat.exe --server
    #
    # This same executable then runs FastAPI.
    #
    # ========================================================

    if "--server" in sys.argv:

        from server import app, initialize_server
        import uvicorn

        print("🚀 Starting CodeChat Team Server...")
        initialize_server()

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000
        )

        sys.exit(0)

    # ========================================================
    # NORMAL GUI MODE
    # ========================================================

    print("=" * 55)
    print("          CODECHAT STARTING — FRESH AI SESSION")
    print("=" * 55)

    # --------------------------------------------------------
    # 1. Setup Ollama
    # --------------------------------------------------------

    print("🔍 Checking Ollama...")

    if not setup_ollama():

        print("❌ Ollama setup failed.")
        sys.exit(1)

    print("✅ Ollama ready.")

    # --------------------------------------------------------
    # 2. Start Team Server
    # --------------------------------------------------------

    if not start_server():
        print("❌ CodeChat server could not start.")
        sys.exit(1)

    # --------------------------------------------------------
    # 3. Start ngrok
    # --------------------------------------------------------

    if not start_ngrok():
        print("⚠️ ngrok could not start.")
        print("   Team mode will not be available remotely.")

    print("🖥️ Starting CodeChat GUI...")

    # --------------------------------------------------------
    # 3. Start GUI
    # --------------------------------------------------------

    app = QApplication(
        sys.argv
    )

    window = CoreApp()

    window.show()

    exit_code = app.exec()

    if getattr(window, "_poll_worker", None) is not None and window._poll_worker.isRunning():
        window._poll_worker.quit(); window._poll_worker.wait(2000)

    # --------------------------------------------------------
    # 4. Cleanup
    # --------------------------------------------------------

    stop_server()
    stop_ngrok()
    stop_ollama()

    sys.exit(
        exit_code
    )