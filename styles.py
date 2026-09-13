BG_WINDOW = "#090909"     
BG_SIDEBAR = "#111111"
BG_CHAT = "#000000"       
STATUS_LOCAL = "#23a559"
STATUS_REMOTE = "#5865F2"
STATUS_GUEST = "#757575"
STATUS_COLLAB = "#F59E0B"

PRO_STYLE = f"""
QMainWindow {{ background-color: {BG_WINDOW}; }}
QFrame#Sidebar {{ background-color: {BG_SIDEBAR}; border-right: 1px solid #222; }}
QLabel {{ font-family: 'Segoe UI', sans-serif; color: #ccc; }}
QTextBrowser {{ background-color: {BG_CHAT}; border: none; padding: 20px; color: #eee; }}
QPushButton {{
    background-color: #262626; color: #ccc; border: 1px solid #333;
    border-radius: 4px; padding: 8px 16px; font-weight: 600; font-size: 12px; text-align: left;
}}
QPushButton:hover {{ background-color: #333; color: white; border: 1px solid #555; }}
QPushButton:checked {{ background-color: #5865F2; color: white; border: 1px solid #5865F2; }}
QPushButton:disabled {{ background-color: #151515; color: #444; border: 1px solid #222; }}
QLineEdit {{
    background-color: #202020; color: white; border: 1px solid #333;
    border-radius: 20px; padding: 10px 20px; font-size: 14px;
}}
QLineEdit:focus {{ border: 1px solid #5865F2; background-color: #262626; }}
QTabWidget::pane {{ border: 1px solid #333; }}
QListWidget {{ background-color: #111; color: #ccc; border: 1px solid #222; padding: 4px; }}
QListWidget::item {{ padding: 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background-color: #262626; color: white; }}
QTabBar::tab {{
    background: #1a1a1a; color: #888; padding: 8px 20px;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {BG_CHAT}; color: #5865F2; font-weight: bold; }}
"""