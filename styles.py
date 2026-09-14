BG_WINDOW = "#0b0d10"
BG_SIDEBAR = "#11151a"
BG_CHAT = "#0b0d10"
BG_PANEL = "#151a20"
ACCENT = "#5865F2"
TEXT = "#e8eaed"
MUTED = "#9aa0a6"
STATUS_LOCAL = "#23a559"
STATUS_REMOTE = "#5865F2"
STATUS_GUEST = "#757575"
STATUS_COLLAB = "#F59E0B"

PRO_STYLE = f"""
QMainWindow {{ background: {BG_WINDOW}; color: {TEXT}; }}
QFrame#Sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid #252b33; }}
QFrame#Header {{ background: {BG_PANEL}; border-bottom: 1px solid #252b33; }}
QLabel {{ font-family: 'Segoe UI'; color: {TEXT}; }}
QLabel#SectionLabel {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QTextBrowser, QTextEdit {{ background: {BG_CHAT}; border: 0; padding: 16px; color: {TEXT}; selection-background-color: #2d356b; }}
QPushButton {{ background: #1b2027; color: #d7dbe0; border: 1px solid #2a3038; border-radius: 8px; padding: 9px 13px; font-weight: 600; font-size: 12px; }}
QPushButton:hover {{ background: #252b33; border-color: #3a424d; color: white; }}
QPushButton:pressed {{ background: #161a1f; }}
QPushButton:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QPushButton:disabled {{ background: #15181c; color: #555b63; border-color: #20242a; }}
QLineEdit {{ background: #171b21; color: white; border: 1px solid #303740; border-radius: 20px; padding: 10px 16px; font-size: 14px; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; background: #1b2027; }}
QTabWidget::pane {{ border: 1px solid #252b33; border-radius: 8px; background: {BG_CHAT}; }}
QTabBar::tab {{ background: #171b21; color: #8f969f; padding: 10px 18px; border: 0; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {BG_CHAT}; color: white; font-weight: 700; border-bottom: 2px solid {ACCENT}; }}
QListWidget {{ background: #101419; color: #cdd2d8; border: 1px solid #252b33; border-radius: 7px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 9px; border-radius: 6px; }}
QListWidget::item:hover {{ background: #1b2027; }}
QListWidget::item:selected {{ background: #252b33; color: white; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343b45; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMessageBox, QDialog {{ background: {BG_WINDOW}; }}
QComboBox {{ background: #171b21; color: white; border: 1px solid #303740; border-radius: 6px; padding: 7px; }}
"""
