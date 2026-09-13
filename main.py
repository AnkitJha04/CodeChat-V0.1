import sys
import os
import subprocess
import time
import markdown
import speech_recognition as sr
import requests
import json
import base64
import secrets
import zipfile
import shutil
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextBrowser, QLineEdit, QPushButton,
    QFileDialog, QLabel, QFrame, QInputDialog, QMessageBox,
    QDialog, QRadioButton, QTextEdit, QTabWidget,
    QListWidget
)

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer

from backend import CoreBrain, RemoteBrain
from styles import (
    PRO_STYLE,
    STATUS_LOCAL,
    STATUS_REMOTE,
    STATUS_GUEST,
    STATUS_COLLAB
)
from ollama_setup import setup_ollama


TEXT_GRAY = "#888888"

# ============================================================
# SERVER PROCESS
# ============================================================

server_process = None
ngrok_process = None
public_url = None
host_token = None

def start_server():
    global server_process, host_token

    # Check if server is already running
    try:
        response = requests.get(
            "http://127.0.0.1:8000/health",
            timeout=2
        )

        if response.status_code == 200:
            print("✅ CodeChat server already running.")
            return True

    except requests.RequestException:
        pass

    print("🚀 Starting CodeChat server...")

    # ---------------------------------------------
    # Build correct command
    # ---------------------------------------------
    # When running from source:
    #     python main.py
    # becomes:
    #     python main.py --server
    #
    # When running as a PyInstaller executable:
    #     CodeChat.exe
    # becomes:
    #     CodeChat.exe --server
    #
    # IMPORTANT:
    # In source mode we MUST include the path to main.py.
    # Otherwise Python interprets "--server" as a Python
    # interpreter option and exits with:
    #     unknown option --server
    # ---------------------------------------------
    if getattr(sys, "frozen", False):
        server_command = [
            sys.executable,
            "--server"
        ]
    else:
        server_command = [
            sys.executable,
            os.path.abspath(__file__),
            "--server"
        ]

    # ---------------------------------------------
    # Start server
    # ---------------------------------------------
    creation_flags = 0

    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NO_WINDOW

    server_env = os.environ.copy()
    if host_token:
        server_env["CODECHAT_HOST_TOKEN"] = host_token

    server_process = subprocess.Popen(
        server_command,
        creationflags=creation_flags,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=server_env
    )

    # ---------------------------------------------
    # Wait for server
    # ---------------------------------------------
    print("⏳ Waiting for CodeChat server...")

    for _ in range(30):
        time.sleep(1)

        # Check if process died
        if server_process.poll() is not None:
            print("❌ Server process stopped unexpectedly.")
            server_process = None
            return False

        try:
            response = requests.get(
                "http://127.0.0.1:8000/health",
                timeout=1
            )

            if response.status_code == 200:
                print("✅ CodeChat server started successfully.")
                return True

        except requests.RequestException:
            pass

    print("❌ CodeChat server failed to start.")
    return False


def start_ngrok():
    global ngrok_process, public_url

    print("🌐 Starting ngrok...")

    try:
        # Start ngrok
        creation_flags = 0

        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        ngrok_process = subprocess.Popen(
            ["ngrok", "http", "8000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags
        )

        # Wait for ngrok API
        for _ in range(15):
            time.sleep(1)

            try:
                response = requests.get(
                    "http://127.0.0.1:4040/api/tunnels",
                    timeout=2
                )

                if response.status_code == 200:
                    tunnels = response.json().get("tunnels", [])

                    for tunnel in tunnels:
                        if tunnel.get("proto") == "https":
                            public_url = tunnel["public_url"]

                            print("✅ ngrok started successfully.")
                            print(f"🌍 Public URL: {public_url}")

                            return True

            except requests.RequestException:
                pass

        print("❌ Could not obtain ngrok public URL.")
        return False

    except FileNotFoundError:
        print("❌ ngrok.exe not found.")
        print("   Install ngrok or place ngrok.exe in PATH.")
        return False

    except Exception as e:
        print(f"❌ ngrok failed: {e}")
        return False
    

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

                    temp_brain = "temp_session_brain.brain"
                    temp_chat = "temp_session_chat.json"

                    save_res = self.brain.save_snapshot(
                        temp_brain
                    )

                    if "Success" not in save_res:
                        raise Exception(save_res)

                    with open(
                        temp_chat,
                        "w",
                        encoding="utf-8"
                    ) as f:

                        json.dump(
                            self.extra,
                            f,
                            indent=2
                        )

                    with zipfile.ZipFile(
                        self.data,
                        "w",
                        zipfile.ZIP_DEFLATED
                    ) as zf:

                        zf.write(temp_brain)
                        zf.write(temp_chat)

                    res = "Success"

                except Exception as e:

                    res = f"Error saving session: {e}"

                finally:

                    if os.path.exists(
                        "temp_session_brain.brain"
                    ):
                        os.remove(
                            "temp_session_brain.brain"
                        )

                    if os.path.exists(
                        "temp_session_chat.json"
                    ):
                        os.remove(
                            "temp_session_chat.json"
                        )

            elif self.task == "load_session":

                try:

                    with zipfile.ZipFile(
                        self.data,
                        "r"
                    ) as zf:

                        zf.extractall(
                            "temp_session_extract"
                        )

                    if os.path.exists(
                        "temp_session_extract/temp_session_brain.brain"
                    ):

                        load_res = self.brain.load_snapshot(
                            "temp_session_extract/temp_session_brain.brain"
                        )

                        if "Success" not in load_res:
                            raise Exception(load_res)

                    chat_data = []

                    if os.path.exists(
                        "temp_session_extract/temp_session_chat.json"
                    ):

                        with open(
                            "temp_session_extract/temp_session_chat.json",
                            "r",
                            encoding="utf-8"
                        ) as f:

                            chat_data = json.load(f)

                    res = chat_data

                except Exception as e:

                    res = f"Error loading session: {e}"

                finally:

                    if os.path.exists(
                        "temp_session_extract"
                    ):

                        shutil.rmtree(
                            "temp_session_extract"
                        )

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

                    temp_file = "temp_sync.brain"

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

        self.init_ui()

        self.team_timer = QTimer()

        self.team_timer.timeout.connect(
            self.poll_updates
        )

        self.team_timer.start(2000)

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
        self.chat_conversation_list = QListWidget()
        self.chat_conversation_list.setFixedWidth(190)
        self.chat_conversation_list.currentRowChanged.connect(
            self.select_chat_conversation
        )
        self.team_chat_view = QTextBrowser()
        chat_layout.addWidget(self.chat_conversation_list)
        chat_layout.addWidget(self.team_chat_view, 1)
        self.tabs.addTab(self.team_chat_widget, "💬 Team Chat")
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
            "Ask a question..."
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

        main_layout.addWidget(
            self.user_panel
        )

        self.lock_ui()
        self.update_chat_controls()
        self.refresh_chat_conversations()

    # --------------------------------------------------------
    # BASIC UI
    # --------------------------------------------------------

    def set_status(self, text):
        self.lbl_activity.setText(text)

    def lock_ui(self):

        self.inp.setEnabled(False)
        self.btn_send.setEnabled(False)

        if isinstance(self.brain, RemoteBrain):
            self.inp.setPlaceholderText(
                "Waiting for Team Brain..."
            )
        else:
            self.inp.setPlaceholderText(
                "Load project to start..."
            )

    def unlock_ui(self):

        self.inp.setEnabled(True)
        self.btn_send.setEnabled(True)

        self.inp.setPlaceholderText(
            "Ask a question..."
        )

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
        self.user_role = "host"
        self.user_name = "Host"
        self.user_handle = "host"
        self.member_id = "host"

        self.user_badge.setText(" Local Host ")
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
            self.update_chat_controls()
            self.refresh_chat_conversations()
            self.refresh_chat_messages()

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
        except Exception as e:
            print(f"Chat conversation error: {e}")

    def select_chat_conversation(self, row):
        if 0 <= row < len(self.chat_conversations):
            self.current_conversation_id = self.chat_conversations[row].get("id", "team")
            self.refresh_chat_messages()

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
        text = self.inp.text().strip()
        if not text or not self.chat_enabled:
            return
        try:
            if isinstance(self.brain, RemoteBrain):
                result = self.brain.send_chat_message(
                    text, self.current_conversation_id
                )
            else:
                r = requests.post(
                    "http://127.0.0.1:8000/chat/messages",
                    json={
                        "text": text,
                        "conversation_id": self.current_conversation_id
                    },
                    headers={"x-access-token": host_token or ""},
                    timeout=5
                )
                result = r.json() if r.status_code == 200 else {"error": r.text}
            if result.get("error"):
                QMessageBox.warning(self, "Team Chat", result["error"])
                return
            self.inp.clear()
            self.refresh_chat_messages()
        except Exception as e:
            QMessageBox.warning(self, "Team Chat", str(e))

    def toggle_team_chat(self):
        if isinstance(self.brain, RemoteBrain):
            return
        desired = self.btn_chat_toggle.isChecked()
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
                r = requests.get(
                    "http://127.0.0.1:8000/chat/conversations",
                    headers={"x-access-token": host_token or ""},
                    timeout=2
                )
                data = r.json() if r.status_code == 200 else {}
            members = data.get("members", [])
            choices = [
                f"{m.get('name')} (@{m.get('handle')}) [{m.get('member_id')}]"
                for m in members if m.get("member_id") != self.member_id
            ]
            selected_ids = []
            while choices:
                choice, ok = QInputDialog.getItem(
                    self, "Group Members",
                    "Select a member (Cancel when finished):",
                    choices, 0, False
                )
                if not ok:
                    break
                mid = choice.split("[")[-1].rstrip("]")
                if mid not in selected_ids:
                    selected_ids.append(mid)
                choices = [c for c in choices if not c.endswith(f"[{mid}]")]

            if isinstance(self.brain, RemoteBrain):
                result = self.brain.create_group(name, selected_ids)
            else:
                r = requests.post(
                    "http://127.0.0.1:8000/chat/groups",
                    json={"name": name, "member_ids": selected_ids},
                    headers={"x-access-token": host_token or ""},
                    timeout=5
                )
                result = r.json() if r.status_code == 200 else {"error": r.text}

            if result.get("error"):
                QMessageBox.warning(self, "Create Group", result["error"])
                return
            group = result.get("group", {})
            self.current_conversation_id = f"group:{group.get('group_id')}"
            self.refresh_chat_conversations()
            QMessageBox.information(
                self, "Group Created",
                f"Created {group.get('name')}.\nUse @{group.get('handle')} to route messages."
            )
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
        layout.addWidget(QLabel("Host has ultimate authority. Select a member to remove:"))
        lst = QListWidget()
        for m in members:
            status = "🟢" if m.get("online") else "⚪"
            lst.addItem(
                f"{status} {m.get('name')} @{m.get('handle')} [{m.get('role')}]"
            )
        layout.addWidget(lst)
        remove_btn = QPushButton("❌ Remove Selected")
        close_btn = QPushButton("Close")
        layout.addWidget(remove_btn)
        layout.addWidget(close_btn)

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

        remove_btn.clicked.connect(remove_selected)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

    def toggle_mode(self):
        # Remote users never control the mode. The Host is the
        # single source of truth for Single/Append mode.
        if isinstance(self.brain, RemoteBrain):
            return

        mode = "append" if self.btn_mode.isChecked() else "single"

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

            self.set_status(f"Team Mode: {mode.title()}")

        except Exception as e:
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

        self.team_mode = mode
        self.team_brain_ready = ready
        self.team_brain_version = version
        self.team_brain_chunks = chunks

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

        state = self.get_team_state()
        if state and state.get("auth_error"):
            self.handle_team_revoked()
            return
        if state:
            self.apply_team_state(state)

        history = self.brain.get_team_chat()
        events = self.brain.get_team_events() if hasattr(self.brain, "get_team_events") else []

        if history or events:

            html = ""

            for h in history:

                color = (
                    "#5865F2"
                    if "HOST" in h['user']
                    else "#F59E0B"
                )

                html += f"""
                <div style="margin-bottom: 15px;
                padding: 10px;
                background-color: #0f0f0f;
                border-radius: 8px;
                border: 1px solid #222;">

                    <div style="
                    color:{color};
                    font-size: 10px;
                    font-weight: bold;
                    margin-bottom: 5px;">
                        👤 {h['user']}
                    </div>

                    <div style="
                    background-color: #1a1a1a;
                    padding: 8px;
                    border-radius: 6px;
                    margin-bottom: 5px;
                    border-left: 2px solid {color};">

                        <span style="
                        color: #ccc;
                        font-weight: bold;">
                            Q:
                        </span>

                        <span style="
                        color: #fff;
                        white-space: pre-wrap;">
                            {h['query']}
                        </span>

                    </div>

                    <div style="
                    background-color: #111;
                    padding: 8px;
                    border-radius: 6px;
                    color: #aaa;
                    font-size: 12px;
                    white-space: pre-wrap;">

                        <span style="
                        color: #5865F2;
                        font-weight: bold;">
                            AI:
                        </span>

                        {h['answer']}

                    </div>

                </div>
                """

            if events:
                html += """
                <h3 style="color:#888;margin-top:20px;">📋 Team Audit</h3>
                """
                for ev in events:
                    actor = ev.get("actor", "Unknown")
                    target = ev.get("target")
                    etype = ev.get("type", "event").replace("_", " ").title()
                    details = ev.get("details", {})
                    suffix = f" → {target}" if target else ""
                    if details:
                        compact = ", ".join(f"{k}: {v}" for k, v in details.items() if k != "invite_token_created")
                        if compact:
                            suffix += f" ({compact})"
                    html += f"""
                    <div style="margin:6px 0;padding:7px;background:#0b0b0b;
                                border-left:2px solid #444;color:#888;">
                        <b style="color:#aaa;">{etype}</b>
                        · {actor}{suffix}
                        <span style="color:#555;"> · {ev.get('timestamp','')}</span>
                    </div>
                    """

            if self.team_view.toHtml() != html:

                sb = self.team_view.verticalScrollBar()

                was_at_bottom = (
                    sb.value()
                    >= (sb.maximum() - 20)
                )

                old_val = sb.value()

                self.team_view.setHtml(html)

                if was_at_bottom:
                    sb.setValue(sb.maximum())

                else:
                    sb.setValue(old_val)

        if self.tabs.currentIndex() == 2 and self.chat_enabled:
            self.refresh_chat_messages()

        if hasattr(
            self.brain,
            'get_connected_users'
        ):

            users = self.brain.get_connected_users()

            self.user_list.clear()

            self.user_list.addItem(
                "👤 Host (Admin)"
            )

            for u in users:

                prefix = "🟠" if u.get('role') == 'collaborator' else "⚪"
                self.user_list.addItem(
                    f"{prefix} {u.get('name', u.get('email', 'Unknown'))} "
                    f"@{u.get('handle', '')}"
                )

    # --------------------------------------------------------
    # QUESTIONS
    # --------------------------------------------------------

    def ask_text(self):

        t = self.inp.text().strip()

        if not t:
            return

        self.handle_question(t)

    def handle_question(self, text):

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

        self.worker = TaskWorker(
            self.brain,
            "query",
            text,
            public_flag=is_public,
            history=formatted_history
        )

        self.worker.result_signal.connect(
            lambda r: self.finish_query(
                r,
                is_public
            )
        )

        self.worker.start()

    def finish_query(
        self,
        result,
        is_public
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

        self.set_status("Ready")

        if not isinstance(ans, str):
            ans = str(ans)

        if not is_public:

            self.add_msg(
                ans,
                "ai",
                srcs
            )

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

    # --------------------------------------------------------
    # INGEST
    # --------------------------------------------------------

    def do_ingest(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Code"
        )

        if folder:

            # The Host controls the mode. Remote clients merely send
            # the upload; the server decides whether it is Single or Append.
            is_append = self.btn_mode.isChecked()
            if isinstance(self.brain, RemoteBrain):
                is_append = getattr(
                    self.brain,
                    "team_mode",
                    self.team_mode
                ) == "append"

            if not is_append:

                self.chat.clear()
                self.chat_history_log = []

            self.set_status(
                "Indexing..."
            )

            self.worker = TaskWorker(
                self.brain,
                "ingest",
                folder,
                is_append
            )

            self.worker.msg_signal.connect(
                lambda s: self.set_status(s)
            )

            self.worker.result_signal.connect(
                self.finish_ingest
            )

            self.worker.start()

    def finish_ingest(self, res):
        if "Success" in res or "Indexed" in res or "Uploaded Successfully" in res:
            self.set_status("Ready")
            self.unlock_ui()
            self.add_msg(f"✅ {res}", "ai")

            if not isinstance(self.brain, RemoteBrain):
                self.btn_save_brain.setEnabled(True)

                # Host uploads are immediately published to the central
                # Team Brain. No collaborator needs to press Sync Server.
                self.set_status("Publishing Team Brain...")
                self.sync_worker = TaskWorker(
                    self.brain,
                    "sync_server"
                )
                self.sync_worker.result_signal.connect(
                    self.finish_ingest_sync
                )
                self.sync_worker.start()
            else:
                # Collaborator upload is already ingested by the central
                # server. Refresh state immediately.
                self.refresh_team_state_once()
        else:
            self.add_msg(f"❌ {res}", "ai")
            self.set_status("Error")

    def finish_ingest_sync(self, res):
        if "SUCCESS" in res:
            self.set_status("Ready")
            self.refresh_team_state_once()
        else:
            self.set_status("Sync Error")
            QMessageBox.warning(
                self,
                "Team Brain Sync Error",
                str(res)
            )

    def refresh_team_state_once(self):
        state = self.get_team_state()
        if state:
            self.apply_team_state(state)

    # --------------------------------------------------------
    # SYNC
    # --------------------------------------------------------

    def do_sync(self):

        self.set_status(
            "Syncing..."
        )

        self.worker = TaskWorker(
            self.brain,
            "sync_server"
        )

        self.worker.result_signal.connect(
            self.finish_sync
        )

        self.worker.start()

    def finish_sync(self, res):

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

        self.set_status("Inviting...")
        self.worker = TaskWorker(
            self.brain,
            "invite",
            name,
            extra=role
        )
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

        if self.btn_join.isChecked():

            url, ok1 = QInputDialog.getText(
                self,
                "Join Team",
                "Host URL:"
            )

            token, ok2 = QInputDialog.getText(
                self,
                "Auth",
                "Token:"
            )

            if ok1 and ok2:

                try:

                    response = requests.get(
                        f"{url.rstrip('/')}/check_role",
                        headers={
                            "x-access-token": token
                        },
                        timeout=5
                    )

                    print("JOIN STATUS:", response.status_code)
                    print("JOIN RESPONSE:", repr(response.text))

                    if response.status_code != 200:
                        raise Exception(
                            f"Server returned {response.status_code}: {response.text}"
                        )

                    role_data = response.json()

                    self.brain = RemoteBrain(
                        url,
                        token
                    )
                    self.is_team_client = True

                    role = role_data['role']
                    self.user_role = role
                    self.user_name = role_data.get("name", "Team Member")
                    self.user_handle = role_data.get("handle", "")
                    self.member_id = role_data.get("member_id")

                    # ----------------------------------------
                    # LOCK MODE
                    # ----------------------------------------
                    # The server decides the mode. Never assume Append.
                    self.btn_mode.setEnabled(False)
                    self.is_team_client = True

                    team_state_response = requests.get(
                        f"{url.rstrip('/')}/team_state",
                        headers={"x-access-token": token},
                        timeout=5
                    )
                    if team_state_response.status_code == 200:
                        self.apply_team_state(team_state_response.json())

                    # ----------------------------------------
                    # BUTTON PERMISSIONS
                    # ----------------------------------------

                    self.btn_load_session = getattr(
                        self,
                        'btn_load_chat',
                        None
                    )

                    self.btn_save_chat.setEnabled(
                        True
                    )

                    self.btn_load_chat.setEnabled(
                        False
                    )

                    self.btn_save_brain.setEnabled(
                        True
                    )

                    self.btn_load_brain.setEnabled(
                        False
                    )

                    if role == "collaborator":

                        self.user_badge.setText(
                            " Remote (Collab) "
                        )

                        self.user_badge.setStyleSheet(
                            f"background-color: "
                            f"{STATUS_COLLAB}; "
                            "color: black; "
                            "border-radius: 4px; "
                            "padding: 5px; "
                            "font-weight:bold;"
                        )

                        self.btn_new.setEnabled(
                            True
                        )

                    else:

                        self.user_badge.setText(
                            " Remote (Guest) "
                        )

                        self.user_badge.setStyleSheet(
                            f"background-color: "
                            f"{STATUS_GUEST}; "
                            "color: white; "
                            "border-radius: 4px; "
                            "padding: 5px; "
                            "font-weight:bold;"
                        )

                        self.btn_new.setEnabled(
                            False
                        )

                    self.btn_join.setText(
                        "❌ Leave Team"
                    )

                    self.btn_invite.setEnabled(
                        role == "collaborator"
                    )
                    self.btn_manage_members.setEnabled(False)
                    self.btn_chat_toggle.setEnabled(False)
                    self.btn_new_group.setEnabled(True)

                    self.btn_sync.setEnabled(
                        False
                    )

                    if self.team_brain_ready and self.team_brain_chunks > 0:
                        self.unlock_ui()
                    else:
                        self.lock_ui()
                        self.set_status("Waiting for Host Brain...")

                    self.add_msg(
                        f"Connected as {role.title()}.",
                        "ai"
                    )

                except Exception as e:

                    self.add_msg(
                        f"❌ Connection Failed: {e}",
                        "ai"
                    )

                    self.btn_join.setChecked(
                        False
                    )

            else:

                self.btn_join.setChecked(
                    False
                )

        else:

            try:

                requests.post(
                    f"{self.brain.url}/leave",
                    headers={
                        "x-access-token":
                        self.brain.token
                    },
                    timeout=2
                )

            except:
                pass

            # --------------------------------------------
            # RESTORE HOST
            # --------------------------------------------

            self.brain = CoreBrain()
            self.is_team_client = False
            self.user_role = "host"
            self.user_name = "Host"
            self.user_handle = "host"
            self.member_id = "host"
            self.chat_enabled = True
            self.team_brain_ready = False
            self.team_brain_version = 0
            self.team_brain_chunks = 0

            self.user_badge.setText(
                " Local Host "
            )

            self.user_badge.setStyleSheet(
                f"background-color: "
                f"{STATUS_LOCAL}; "
                "color: white; "
                "border-radius: 4px; "
                "padding: 5px;"
            )

            self.btn_mode.setEnabled(
                True
            )

            self.btn_mode.setStyleSheet(
                "color: #aaa; "
                "font-style: italic; "
                "border: 1px dashed #444;"
            )

            self.btn_save_chat.setEnabled(
                True
            )

            self.btn_load_chat.setEnabled(
                True
            )

            self.btn_save_brain.setEnabled(
                True
            )

            self.btn_load_brain.setEnabled(
                True
            )

            self.btn_join.setText(
                "🌐 Join Team"
            )

            self.btn_invite.setEnabled(
                True
            )
            self.btn_manage_members.setEnabled(True)
            self.btn_chat_toggle.setEnabled(True)
            self.btn_new_group.setEnabled(True)

            self.btn_sync.setEnabled(
                True
            )

            self.btn_new.setEnabled(
                True
            )

            self.lock_ui()

    # --------------------------------------------------------
    # CLOSE
    # --------------------------------------------------------

    def closeEvent(self, event):

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

            self.voice_thread.stop()

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

            self.worker = TaskWorker(
                self.brain,
                "save_session",
                path,
                extra=self.chat_history_log
            )

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

            self.set_status(
                "Loading Session..."
            )

            self.worker = TaskWorker(
                self.brain,
                "load_session",
                path
            )

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

            self.set_status(
                "Session Loaded"
            )

            self.unlock_ui()

            self.btn_save_brain.setEnabled(
                True
            )

        else:

            self.set_status(
                "Error"
            )

            QMessageBox.critical(
                self,
                "Load Failed",
                str(res)
            )

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

            self.worker = TaskWorker(
                self.brain,
                "save_brain",
                path
            )

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
            self.set_status("Loading Brain...")
            self.worker = TaskWorker(
                self.brain,
                "load_brain",
                path
            )
            self.worker.result_signal.connect(
                self.finish_load_brain
            )
            self.worker.start()

    def finish_load_brain(self, res):
        if res == "Success":
            self.set_status("Publishing Team Brain...")
            self.btn_save_brain.setEnabled(True)

            # Loading a .brain is immediately synchronized to the server.
            self.sync_worker = TaskWorker(
                self.brain,
                "sync_server"
            )
            self.sync_worker.result_signal.connect(
                self.finish_load_brain_sync
            )
            self.sync_worker.start()
        else:
            self.set_status("Load Failed")
            QMessageBox.critical(
                self,
                "Load Failed",
                str(res)
            )

    def finish_load_brain_sync(self, res):
        if "SUCCESS" in res:
            self.set_status("Team Brain Ready")
            self.unlock_ui()
            self.add_msg(
                "✅ Brain loaded and shared with the entire team.",
                "ai"
            )
            self.refresh_team_state_once()
        else:
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

        from server import app
        import uvicorn

        print("🚀 Starting CodeChat Team Server...")

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
    print("          CODECHAT STARTING")
    print("=" * 55)

    # --------------------------------------------------------
    # 0. Create this instance's Host authentication token
    # --------------------------------------------------------
    # The same token is passed to the child FastAPI process through
    # CODECHAT_HOST_TOKEN and is required for Host-only endpoints.
    host_token = secrets.token_hex(32)
    os.environ["CODECHAT_HOST_TOKEN"] = host_token

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

    print("🖥️ Starting CodeChat GUI...")

    app = QApplication(
        sys.argv
    )

    window = CoreApp()

    window.show()

    exit_code = app.exec()

    # --------------------------------------------------------
    # 4. Cleanup
    # --------------------------------------------------------

    stop_server()
    stop_ngrok()

    sys.exit(
        exit_code
    )