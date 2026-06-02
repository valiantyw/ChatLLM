# -*- coding: utf-8 -*-
#
# ChatLLM - Chat LLM application with tkinter GUI mode
#

import sys
sys.dont_write_bytecode = True

import os, json, base64, threading, mimetypes
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import requests, dotenv

import webbrowser, io, hashlib
try:
    from PIL import Image as PILImage, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from providers import (
    PROVIDERS,
    DEFAULT_PROVIDER,
    DEFAULT_MODEL,
    SHORT_TITLE_PROVIDER,
    SHORT_TITLE_MODEL,
    MUSIC_MODEL,
    DEFAULT_IMAGE_MODEL,
    is_image_model,
    is_music_model,
    call_chat_api,
    call_image_api,
    call_music_api,
)

# Load environment variables
dotenv.load_dotenv(dotenv.find_dotenv())

# Supported file extensions for different types
TXT_EXTS   = {"txt", "md", "py", "csv", "json", "xml", "yaml", "yml"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
PDF_EXTS   = {"pdf"}
VOICE_EXTS = {"mp3", "wav", "ogg", "flac", "aac", "m4a"}
VIDEO_EXTS = {"mp4", "avi", "mkv", "mov", "flv", "wmv"}

# Common HTTP headers for downloads
DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Conversations storage path (absolute path)
CONV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversations")
os.makedirs(CONV_DIR, exist_ok=True)

# Provider & Model configuration options (loaded from providers/__init__.py)
# PROVIDERS is now a merged dict from all provider modules

# Default System Prompt
DEFAULT_SYSTEM_PROMPT = "你是智能助手，始终用中文回复。"

# Constants for duplicate strings
IMAGE_FILE_FILTER = "图片文件 (*.png;*.jpg;*.jpeg)"
DATETIME_FORMAT = "%Y-%m-%d-%H%M%S"
DEFAULT_LYRICS = "美妙的旋律在夜空流淌\n轻风拂过思念的琴弦\n每一个音符都是真挚的向往\n让我们一起歌唱到地久天长"


# Helpers for file reading
def read_text_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        return f"[读取文件错误: {str(e)}]"

def read_image_base64(filepath):
    try:
        mime, _ = mimetypes.guess_type(filepath)
        if not mime:
            mime = "image/jpeg"
        with open(filepath, 'rb') as f:
            b64_data = base64.b64encode(f.read()).decode('utf-8')
        return b64_data, mime
    except Exception as e:
        return None, None

# ─────────────────────────────────────────────
#  Color & Style Constants
# ─────────────────────────────────────────────
# Title bar colors
TB_BG        = "#e2e8f0"
TB_FG        = "#1e293b"
TB_HOVER     = "#cbd5e1"
CLOSE_HOVER  = "#ef4444"

# Chat bubble colors
BUBBLE_USER_BG       = "#e0f2fe"   # Blue 100
BUBBLE_ASSISTANT_BG  = "#f1f5f9"   # Slate 100
BUBBLE_SYSTEM_BG     = "#fef3c7"   # Amber 100
BUBBLE_THINKING_BG   = "#fafafa"   # Zinc 50

# Text colors
TEXT_DARK    = "#0f172a"   # Slate 900
TEXT_MEDIUM  = "#475569"   # Slate 600
TEXT_LIGHT   = "#64748b"   # Slate 500
TEXT_GRAY    = "#94a3b8"   # Slate 400 - light gray for placeholders

# Accent colors
ACCENT_BLUE      = "#3b82f6"   # Blue 500 - selection highlight
ACCENT_VIOLET    = "#7c3aed"   # Violet 600
ACCENT_PURPLE    = "#722ed1"   # Purple for attachment labels

# Special text colors
COLOR_ERROR      = "#ef4444"   # Red 500
COLOR_AMBER      = "#d97706"   # Amber 600 - system prompt
COLOR_ZINC_DARK  = "#52525b"   # Zinc 600 - thinking text
COLOR_ZINC_MID   = "#71717a"   # Zinc 500 - thinking title

# Accent colors
ACCENT_BLUE      = "#3b82f6"   # Blue 500 - selection highlight
ACCENT_VIOLET    = "#7c3aed"   # Violet 600

# Image size constants
THUMBNAIL_CARD_SIZE  = 180  # Card thumbnail max width
INLINE_IMAGE_MAX_W   = 350  # Inline image display max width
IMAGE_SCROLL_STEP    = 220  # Scroll step for multi-image carousel

# Network timeout constants (seconds)
DOWNLOAD_TIMEOUT_SHORT = 30   # Image download timeout
DOWNLOAD_TIMEOUT_DEFAULT = 60  # Default download timeout
DOWNLOAD_TIMEOUT_LARGE = 120  # Large file (audio) download timeout

# Bubble layout margins
BUBBLE_CHAT_MARGIN    = 180  # Push margin to create bubble effect
BUBBLE_CENTER_MARGIN  = 120  # Center margin for system messages

# Font families
FONT_UI      = "Microsoft YaHei"
FONT_SYSTEM  = "Segoe UI"

# ─────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────
class ChatLLM_GUI(tk.Tk):
    def __init__(self):
        super().__init__()
        
        # Enable frameless window design
        self.overrideredirect(True)
        self.title("ChatLLM - 智能助手")
        self.withdraw()  # hide until positioned
        
        # 1. Size window to 2/3 of screen and center it
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        width = int(screen_width * 2 / 3)
        height = int(screen_height * 2 / 3)
        
        x = int((screen_width - width) / 2)
        y = int((screen_height - height) / 2)
        
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(800, 600)
        self.deiconify()  # show centered
        
        # State variables
        self.current_session_id = None
        self.sessions = []
        self.current_messages = []
        self.attached_files = []
        self.sidebar_visible = True
        self.sidebar_width = 260
        self.custom_lyrics = ""
        self._is_processing = False  # Guard against concurrent API calls
        self._loading_flag = False   # Suppress on_session_select during startup
        self.session_titles = {}     # Initialize session_titles dictionary
        
        # Configure styles - Use native theme of platform to avoid the retro 'clam' style
        self.style = ttk.Style()
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        elif "xpnative" in self.style.theme_names():
            self.style.theme_use("xpnative")
            
        # Customize standard widgets with clean, modern styles
        self.style.configure("TFrame", background="#f8fafc")
        self.style.configure("TLabelframe", background="#fafafa", borderwidth=1, relief="solid")
        self.style.configure("TLabelframe.Label", background="#fafafa", font=(FONT_UI, 9, "bold"), foreground=TEXT_MEDIUM)
        self.style.configure("TLabel", background="#f8fafc", font=(FONT_UI, 10), foreground=TEXT_DARK)
        self.style.configure("TButton", font=(FONT_UI, 9), relief="flat")
        self.style.configure("TCombobox", font=(FONT_UI, 10))
        
        # Build UI layout
        self.setup_ui()
        
        # Bind events
        self.history_listbox.bind("<<ListboxSelect>>", self.on_session_select)
        
        # Load all history sessions
        self.load_all_sessions()
        
        # Fix taskbar visibility for frameless window on Windows
        if os.name == 'nt':
            self.after(100, self._fix_taskbar)
            
        # Save session on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)
    def setup_ui(self):
        # ── Custom Title Bar ──────────────────────────
        tb = tk.Frame(self, bg=TB_BG, height=36)
        tb.pack(fill=tk.X, side=tk.TOP)
        tb.pack_propagate(False)

        # Left: app title
        title_lbl = tk.Label(tb, text=" 💬 ChatLLM - 智能助手", bg=TB_BG, fg=TB_FG,
                             font=(FONT_UI, 10, "bold"), padx=5)
        title_lbl.pack(side=tk.LEFT, padx=(8, 0))

        # Right: Close / Maximise / Minimise buttons
        def _close():    self._on_close()
        def _minimize():
            self.overrideredirect(False)
            self.state("iconic")
            def _on_map(e=None):
                if self.state() == "normal":
                    self.overrideredirect(True)
                    self.unbind("<Map>")
            self.bind("<Map>", _on_map)
        def _toggle_max():
            self.state("normal" if self.state() == "zoomed" else "zoomed")

        for _txt, _cmd, _hcolor in [
            (" ✕ ", _close,      CLOSE_HOVER),
            (" □ ", _toggle_max, TB_HOVER),
            (" – ", _minimize,   TB_HOVER),
        ]:
            _b = tk.Button(tb, text=_txt, command=_cmd,
                           bg=TB_BG, fg=TB_FG, relief="flat", bd=0,
                           font=(FONT_SYSTEM, 10), cursor="arrow",
                           activebackground=_hcolor, activeforeground="black")
            _b.pack(side=tk.RIGHT, fill=tk.Y)
            
            # High-end hover responsiveness
            def _on_enter(e, btn=_b, hc=_hcolor):
                btn.config(bg=hc)
                if hc == CLOSE_HOVER:
                    btn.config(fg="white")
            def _on_leave(e, btn=_b, bg=TB_BG, fg=TB_FG):
                btn.config(bg=bg, fg=fg)
            _b.bind("<Enter>", _on_enter)
            _b.bind("<Leave>", _on_leave)

        # Drag-to-move (title bar background + title label, not child buttons)
        tb._ox = tb._oy = 0
        def _start_drag(e):
            tb._ox = e.x_root - self.winfo_x()
            tb._oy = e.y_root - self.winfo_y()
        def _do_drag(e):
            self.geometry(f"+{e.x_root - tb._ox}+{e.y_root - tb._oy}")
        for _w in (tb, title_lbl):
            _w.bind("<Button-1>",       _start_drag)
            _w.bind("<B1-Motion>",      _do_drag)
            _w.bind("<Double-Button-1>", lambda e: _toggle_max())
        
        # Main layout frame above status bar (using horizontal tk.PanedWindow to allow programmatic resizing)
        self.main_paned = tk.PanedWindow(self, orient="horizontal", bd=0, sashwidth=4, sashrelief="flat", bg="#e2e8f0")
        self.main_paned.pack(side="top", fill="both", expand=True)
        
        # Left side panel (Sidebar)
        self.sidebar_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.sidebar_frame, width=self.sidebar_width)
        
        # Top part of Sidebar: Toggle button
        self.sidebar_header = ttk.Frame(self.sidebar_frame)
        self.sidebar_header.pack(side="top", fill="x", pady=(2, 5))
        
        self.btn_toggle_sidebar = ttk.Button(self.sidebar_header, text="◀ 收起侧边栏", command=self.toggle_sidebar)
        self.btn_toggle_sidebar.pack(fill="x", padx=5)
        
        # Rest of Sidebar: Split vertically using vertical PanedWindow
        self.sidebar_paned = ttk.PanedWindow(self.sidebar_frame, orient="vertical")
        self.sidebar_paned.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 6. Sidebar upper part: Conversation history
        history_frame = ttk.LabelFrame(self.sidebar_paned, text=" 会话历史 ", padding=(5, 5))
        self.sidebar_paned.add(history_frame, weight=3)
        
        # Action buttons for sessions
        session_btn_frame = ttk.Frame(history_frame)
        session_btn_frame.pack(fill="x", pady=(0, 5))
        
        self.btn_new_chat = ttk.Button(session_btn_frame, text="➕ 新建会话", command=self.new_session)
        self.btn_new_chat.pack(side="left", fill="x", expand=True, padx=(0, 2))
        
        self.btn_delete_chat = ttk.Button(session_btn_frame, text="🗑️ 删除", command=self.delete_session)
        self.btn_delete_chat.pack(side="right", fill="x", expand=True, padx=(2, 0))
        
        # Scrollable listbox for sessions
        list_scroll_frame = ttk.Frame(history_frame)
        list_scroll_frame.pack(fill="both", expand=True)
        
        self.history_listbox = tk.Listbox(
            list_scroll_frame, 
            selectmode="single", 
            bd=0, 
            highlightthickness=1, 
            highlightbackground="#cbd5e1",
            highlightcolor=ACCENT_BLUE,
            selectbackground=ACCENT_BLUE,
            selectforeground="#ffffff",
            font=(FONT_UI, 10),
            bg="#fbfbfb",
            fg=TEXT_DARK,
            activestyle="none"
        )
        self.history_listbox.pack(side="left", fill="both", expand=True)
        
        list_scroll = ttk.Scrollbar(list_scroll_frame, orient="vertical", command=self.history_listbox.yview)
        list_scroll.pack(side="right", fill="y")
        self.history_listbox.config(yscrollcommand=list_scroll.set)
        
        # 6. Sidebar lower part: Model Settings & Selection
        model_frame = ttk.LabelFrame(self.sidebar_paned, text=" 选择模型 ", padding=(8, 8))
        self.sidebar_paned.add(model_frame, weight=2)
        
        # Provider
        lbl_prov = ttk.Label(model_frame, text="提供商:")
        lbl_prov.pack(anchor="w", pady=(0, 2))
        self.provider_combo = ttk.Combobox(model_frame, state="readonly", values=list(PROVIDERS.keys()))
        self.provider_combo.pack(fill="x", pady=(0, 8))
        self.provider_combo.set(DEFAULT_PROVIDER)
        self.provider_combo.bind("<<ComboboxSelected>>", self.update_model_options)
        
        # Model
        lbl_model = ttk.Label(model_frame, text="模型名称:")
        lbl_model.pack(anchor="w", pady=(0, 2))
        self.model_combo = ttk.Combobox(model_frame, state="readonly")
        self.model_combo.pack(fill="x", pady=(0, 8))
        self.model_combo.bind("<<ComboboxSelected>>", self.on_model_changed)
        self.update_model_options(None)
        

        
        # System Prompt
        lbl_sys = ttk.Label(model_frame, text="系统提示词 (System Prompt):")
        lbl_sys.pack(anchor="w", pady=(0, 2))
        
        sys_scroll_frame = ttk.Frame(model_frame)
        sys_scroll_frame.pack(fill="both", expand=True)
        
        self.system_text = tk.Text(
            sys_scroll_frame, 
            height=3, 
            wrap="word", 
            bd=0, 
            highlightthickness=1, 
            highlightbackground="#cbd5e1",
            highlightcolor=ACCENT_BLUE,
            font=(FONT_UI, 9),
            bg="#fbfbfb",
            fg=TEXT_MEDIUM,
            selectbackground="#cbd5e1",
            selectforeground=TEXT_DARK,
            padx=4,
            pady=4
        )
        self.system_text.pack(side="left", fill="both", expand=True)
        self.system_text.insert("1.0", DEFAULT_SYSTEM_PROMPT)
        
        sys_scroll = ttk.Scrollbar(sys_scroll_frame, orient="vertical", command=self.system_text.yview)
        sys_scroll.pack(side="right", fill="y")
        self.system_text.config(yscrollcommand=sys_scroll.set)
        
        # Right side panel (Container with shared status bar, direct Chat Frame)
        self.right_container = ttk.Frame(self.main_paned)
        self.main_paned.add(self.right_container)
        
        # Status bar at the very bottom of the right panel
        self.status_bar = ttk.Label(self.right_container, text="准备就绪", relief="flat", anchor="w", padding=(6, 4))
        self.status_bar.pack(side="bottom", fill="x")
        
        # Chat Frame directly mapped to self.chat_frame for seamless compatibility
        self.chat_frame = ttk.Frame(self.right_container)
        self.chat_frame.pack(side="top", fill="both", expand=True)
        
        # Chat frame header for toolbar
        chat_header = ttk.Frame(self.chat_frame, padding=(5, 5))
        chat_header.pack(side="top", fill="x")
        
        self.lbl_session_title = ttk.Label(chat_header, text="", font=(FONT_UI, 10, "bold"), foreground=TEXT_DARK)
        self.lbl_session_title.pack(side="left", padx=10)
        
        # Split chat_frame vertically using vertical PanedWindow
        chat_paned = ttk.PanedWindow(self.chat_frame, orient="vertical")
        chat_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)
        
        # Upper Right part: Chat display content
        display_outer_frame = ttk.Frame(chat_paned)
        chat_paned.add(display_outer_frame, weight=4)
        
        self.chat_display = tk.Text(
            display_outer_frame, 
            wrap="word", 
            state="disabled", 
            bd=0, 
            highlightthickness=1, 
            highlightbackground="#e2e8f0",
            highlightcolor="#e2e8f0",
            bg="#ffffff",
            font=(FONT_UI, 10),
            selectbackground="#cbd5e1",
            selectforeground=TEXT_DARK,
            cursor="xterm"
        )
        self.chat_display.pack(side="left", fill="both", expand=True)
        
        display_scroll = ttk.Scrollbar(display_outer_frame, orient="vertical", command=self.chat_display.yview)
        display_scroll.pack(side="right", fill="y")
        self.chat_display.config(yscrollcommand=display_scroll.set)
        
        # Bind copy shortcuts on chat display
        self.chat_display.bind("<Control-c>", self._copy_selection)
        self.chat_display.bind("<Control-C>", self._copy_selection)
        # Setup colors and fonts tags in Text Widget with alignments and bubble backgrounds
        self.chat_display.tag_configure(
            "user",
            justify="right",
            foreground=TEXT_LIGHT,
            font=(FONT_UI, 9, "italic"),
            spacing1=15,
            spacing3=4,
            rmargin=15
        )
        self.chat_display.tag_configure(
            "user_body",
            justify="right",
            foreground=TEXT_DARK,
            background=BUBBLE_USER_BG,
            font=(FONT_UI, 10),
            lmargin1=BUBBLE_CHAT_MARGIN,
            lmargin2=BUBBLE_CHAT_MARGIN,
            rmargin=15,
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "assistant",
            justify="left",
            foreground=TEXT_LIGHT,
            font=(FONT_UI, 9, "italic"),
            spacing1=15,
            spacing3=4,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "assistant_body",
            justify="left",
            foreground=TEXT_DARK,
            background=BUBBLE_ASSISTANT_BG,
            font=(FONT_UI, 10),
            lmargin1=15,
            lmargin2=15,
            rmargin=BUBBLE_CHAT_MARGIN,
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "system",
            justify="center",
            foreground=COLOR_AMBER,
            font=(FONT_UI, 9, "bold"),
            spacing1=15,
            spacing3=4
        )
        self.chat_display.tag_configure(
            "system_body",
            justify="center",
            foreground=TEXT_MEDIUM,
            background=BUBBLE_SYSTEM_BG,
            font=(FONT_UI, 9),
            lmargin1=BUBBLE_CENTER_MARGIN,
            lmargin2=BUBBLE_CENTER_MARGIN,
            rmargin=BUBBLE_CENTER_MARGIN,
            spacing1=4,
            spacing2=4,
            spacing3=15
        )
        self.chat_display.tag_configure(
            "thinking_title",
            justify="left",
            foreground=COLOR_ZINC_MID,
            font=(FONT_UI, 9, "bold"),
            spacing1=15,
            spacing3=4,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "thinking",
            justify="left",
            foreground=COLOR_ZINC_DARK,
            background=BUBBLE_THINKING_BG,
            font=(FONT_UI, 9, "italic"),
            lmargin1=15,
            lmargin2=15,
            rmargin=BUBBLE_CHAT_MARGIN,
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "error",
            justify="left",
            foreground=COLOR_ERROR,
            font=(FONT_UI, 10, "bold"),
            spacing1=15,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "filename",
            foreground=ACCENT_VIOLET,
            font=(FONT_UI, 9, "underline"),
            justify="left"
        )
        self.chat_display.tag_configure(
            "info",
            foreground=TEXT_LIGHT,
            font=(FONT_UI, 9),
            lmargin1=15,
            lmargin2=15,
            spacing1=2,
            spacing3=2
        )
        self.chat_display.tag_configure(
            "prompt",
            foreground=TEXT_DARK,
            font=(FONT_UI, 9, "bold"),
            lmargin1=15,
            lmargin2=15,
            spacing1=2,
            spacing3=2
        )
        self.chat_display.tag_configure(
            "lyrics",
            foreground=TEXT_MEDIUM,
            font=(FONT_UI, 9, "italic"),
            lmargin1=15,
            lmargin2=15,
            spacing1=2,
            spacing3=2
        )
        
        # Keep selection highlight visible above message background tags
        self.chat_display.tag_raise(tk.SEL)
        
        # Lower Right part: User input panel (status bar directly below this frame)
        input_container = ttk.Frame(chat_paned)
        chat_paned.add(input_container, weight=1)
        
        # (A) Parameters Bar above the input text
        params_bar = ttk.Frame(input_container, padding=(2, 2))
        params_bar.pack(fill="x", pady=(0, 2))
        
        self.lbl_aspect = ttk.Label(params_bar, text="图片比例:", font=(FONT_UI, 9))
        # self.lbl_aspect.pack(side="left", padx=(5, 2))
        self.img_aspect_combo = ttk.Combobox(params_bar, state="readonly", values=["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "21:9"], width=8)
        self.img_aspect_combo.set("16:9")
        # self.img_aspect_combo.pack(side="left", padx=2)
        
        self.lbl_n = ttk.Label(params_bar, text="张数:", font=(FONT_UI, 9))
        # self.lbl_n.pack(side="left", padx=(10, 2))
        self.img_n_combo = ttk.Combobox(params_bar, state="readonly", values=["1", "2", "3", "4", "5", "6", "7", "8", "9"], width=6)
        self.img_n_combo.set("1")
        # self.img_n_combo.pack(side="left", padx=2)
        
        self.btn_edit_lyrics = ttk.Button(params_bar, text="添加歌词", command=self.edit_lyrics_popup)
        # self.btn_edit_lyrics.pack(side="left", padx=(15, 2))
        
        #self.lyrics_hint_lbl = ttk.Label(params_bar, text="💡 提示：是否要添加歌词？可点击右侧按钮", foreground="#2563eb", font=("Microsoft YaHei", 9, "bold"))

        # Attachment widgets (placed at the top-right of user input)
        self.btn_add_file = ttk.Button(params_bar, text="添加附件", command=self.add_file)
        self.btn_add_file.pack(side="right", padx=5)
        
        self.btn_clear_attachments = ttk.Button(params_bar, text="清除", command=self.clear_attachments, state="disabled")
        self.btn_clear_attachments.pack(side="right", padx=2)
        
        self.lbl_attachments = ttk.Label(params_bar, text="未选择附件", foreground="gray", font=(FONT_UI, 9))
        self.lbl_attachments.pack(side="right", padx=5)
        
        # (B) Input Text Area
        input_controls = ttk.Frame(input_container)
        input_controls.pack(fill="both", expand=True)
        
        self.input_text = tk.Text(
            input_controls, 
            height=4, 
            wrap="word", 
            bd=0, 
            highlightthickness=1, 
            highlightbackground="#e2e8f0",
            highlightcolor=ACCENT_BLUE,
            font=(FONT_UI, 10),
            bg="#ffffff",
            fg=TEXT_DARK,
            selectbackground="#cbd5e1",
            selectforeground=TEXT_DARK,
            padx=6,
            pady=6
        )
        self.input_text.pack(side="left", fill="both", expand=True)
        self.input_text.bind("<Return>", self.on_enter_pressed)
        self.input_text.bind("<Shift-Return>", self.on_shift_enter_pressed)
        


    def _copy_selection(self, _event=None):
        try:
            selected = self.chat_display.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(selected)
            self.update_status("选中文本已复制到剪贴板。")
        except tk.TclError:
            pass
        return "break"

    def _fix_taskbar(self):
        if os.name == 'nt':
            import ctypes
            try:
                GWL_EXSTYLE = -20
                WS_EX_APPWINDOW = 0x00040000
                WS_EX_TOOLWINDOW = 0x00000080
                
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                if hwnd == 0:
                    hwnd = self.winfo_id()
                    
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
                
                # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
            except Exception as e:
                print(f"Error fixing taskbar: {e}")

    def toggle_sidebar(self):
        if self.sidebar_visible:
            current_width = self.sidebar_frame.winfo_width()
            if current_width > 100:
                self.sidebar_width = current_width
                
            self.sidebar_paned.pack_forget()
            self.main_paned.paneconfigure(self.sidebar_frame, width=45)
            self.btn_toggle_sidebar.config(text="▶")
            self.sidebar_visible = False
        else:
            self.sidebar_paned.pack(fill="both", expand=True, padx=5, pady=5)
            self.main_paned.paneconfigure(self.sidebar_frame, width=self.sidebar_width)
            self.btn_toggle_sidebar.config(text="◀ 收起侧边栏")
            self.sidebar_visible = True

    def update_model_options(self, event):
        provider = self.provider_combo.get()
        models = PROVIDERS.get(provider, [])
        self.model_combo["values"] = models
        if models:
            self.model_combo.set(models[0])
        self.on_model_changed(None)

    # ─────────────────────────────────────────────
    #  Conversation Persistence (no index.json)
    # ─────────────────────────────────────────────
    def _is_image_model(self, model):
        """Helper to check if a model is an image generation model."""
        return is_image_model(model)

    def _extract_history_list(self):
        """Extract historical messages up to (but not including) the current round's user request and assistant loading message."""
        history = []
        if len(self.current_messages) < 3:
            return history
            
        for msg in self.current_messages[:-2]:
            role = msg.get("role")
            msg_type = msg.get("type", "text")
            
            content_text = ""
            if role == "user":
                content_text = msg.get("content", "")
            elif role == "assistant":
                if msg_type == "text":
                    content_text = msg.get("content", "")
                elif msg_type == "image":
                    content_text = f"[Image prompt generated]: {msg.get('prompt', '')}"
                elif msg_type == "music":
                    content_text = f"[Music prompt generated]: {msg.get('prompt', '')}\n[Lyrics]: {msg.get('lyrics', '')}"
                elif msg_type == "error":
                    content_text = f"[Error]: {msg.get('content', '')}"
                    
            if content_text:
                history.append({"role": role, "content": content_text})
        return history

    @staticmethod
    def _session_title_from_id(session_id):
        """Extract display title from session filename (last `-` segment)."""
        if not session_id:
            return "新会话"
        slug = session_id.split("-")[-1]
        # Bare microsecond IDs are all digits — show "新会话"
        if slug.isdigit():
            return "新会话"
        return slug

    def _rescan_sessions(self):
        """Scan conversations/*.json, return sorted list of session IDs (newest first
        based on the datetime embedded in the session filename: YYYY-MM-DD-HHMMSS-slug).
        The timestamp prefix in the ID is extracted for sorting, which is more reliable
        than relying on updated_at inside JSON (which changes on every save)."""
        entries = []
        if not os.path.isdir(CONV_DIR):
            return []
        for fname in os.listdir(CONV_DIR):
            if fname.endswith(".json") and fname != "index.json":
                fpath = os.path.join(CONV_DIR, fname)
                sid = fname[:-5]  # remove .json
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    msgs = data.get("messages") or []
                    # Skip incomplete sessions (no assistant reply)
                    if not any(m.get("role") == "assistant" for m in msgs):
                        os.remove(fpath)
                        print(f"Removed incomplete session: {fname}")
                        continue
                    # Extract datetime from filename for sorting: YYYY-MM-DD-HHMMSS-slug
                    # The first 17 chars (YYYY-MM-DD-HHMMSS) are the timestamp
                    ts = sid[:17] if len(sid) >= 17 else sid
                    entries.append((sid, ts))
                except Exception:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
                    continue
        # Sort by timestamp descending (newest first).
        # The timestamp prefix sorts lexicographically as ISO format.
        entries.sort(key=lambda x: x[1], reverse=True)
        return [sid for sid, _ in entries]

    def load_all_sessions(self):
        self._loading_flag = True
        # Always start with a NEW session on program startup
        self.new_session()
        
        # Then scan existing sessions for sidebar history (don't auto-load them)
        self.sessions = self._rescan_sessions()
                
        self.history_listbox.delete(0, tk.END)
        for sid in self.sessions:
            self.history_listbox.insert(tk.END, self._session_title_from_id(sid))
            
        self._loading_flag = False

    def load_session_by_id(self, session_id):
        self.current_session_id = session_id
        filepath = os.path.join(CONV_DIR, f"{session_id}.json")
        
        messages = []
        provider = DEFAULT_PROVIDER
        model = DEFAULT_MODEL
        system_prompt = DEFAULT_SYSTEM_PROMPT
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    messages = data.get("messages", [])
                    provider = data.get("provider", DEFAULT_PROVIDER)
                    model = data.get("model", DEFAULT_MODEL)
                    system_prompt = data.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
            except Exception as e:
                print(f"Error loading session file {session_id}.json: {e}")
                
        self.current_messages = messages
        
        # Restore settings in UI
        if provider in PROVIDERS:
            self.provider_combo.set(provider)
            # Update the values list for model_combo without resetting/triggering on_model_changed
            models = PROVIDERS.get(provider, [])
            self.model_combo["values"] = models
            if model in models:
                self.model_combo.set(model)
            else:
                self.model_combo.set(models[0]) if models else self.model_combo.set("")
            self.on_model_changed(None)
            
            # If the model is an image model, restore the last used aspect ratio and count from history
            if self._is_image_model(model):
                last_aspect = "16:9"
                last_n = "1"
                for msg in reversed(self.current_messages):
                    if msg.get("type") == "image":
                        last_aspect = msg.get("aspect_ratio", "16:9")
                        last_n = str(msg.get("n", "1"))
                        break
                if hasattr(self, 'img_aspect_combo'):
                    self.img_aspect_combo.set(last_aspect)
                if hasattr(self, 'img_n_combo'):
                    self.img_n_combo.set(last_n)
                
        self.system_text.delete("1.0", tk.END)
        self.system_text.insert("1.0", system_prompt)
        

        
        # Update session title label
        title = self._session_title_from_id(session_id)
        if hasattr(self, 'lbl_session_title'):
            self.lbl_session_title.config(text=title)
            
        # Render historical chat dialogue
        self.refresh_chat_display()
        self.update_idletasks()
        self.chat_display.see(tk.END)
        self.after(100, lambda: self.chat_display.see(tk.END))

    def save_session_by_id(self, session_id):
        if not session_id:
            return
            
        provider = self.provider_combo.get()
        model = self.model_combo.get()
        system_prompt = self.system_text.get("1.0", tk.END).strip()
        
        session_data = {
            "id": session_id,
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt,
            "messages": self.current_messages,
            "updated_at": datetime.now().isoformat()
        }
        
        filepath = os.path.join(CONV_DIR, f"{session_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error writing session file: {e}")

    def refresh_listbox_titles(self):
        selection = self.history_listbox.curselection()
        selected_idx = selection[0] if selection else None
        
        self.history_listbox.delete(0, tk.END)
        for sid in self.sessions:
            self.history_listbox.insert(tk.END, self._session_title_from_id(sid))
            
        if selected_idx is not None and selected_idx < len(self.sessions):
            self.history_listbox.selection_set(selected_idx)
    # ─────────────────────────────────────────────
    #  Sidebar Actions (New & Delete Chat)
    # ─────────────────────────────────────────────
    def new_session(self):
        # Do NOT auto-save current session - only save when user explicitly selects an old session
        new_id = datetime.now().strftime("%Y-%m-%d-%H%M%S-%f")
        title = "新会话"
        
        # Reset state for new session
        self.current_session_id = new_id
        self.current_messages = []
        
        # Update UI
        if hasattr(self, 'lbl_session_title'):
            self.lbl_session_title.config(text=title)
        if hasattr(self, 'chat_display'):
            self.chat_display.config(state="normal")
            self.chat_display.delete("1.0", tk.END)
            self.chat_display.config(state="disabled")
        
        self.update_status("新建会话成功，请输入内容。")

    def delete_session(self):
        selection = self.history_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的会话。")
            return
            
        idx = selection[0]
        session_id = self.sessions[idx]
        title = self._session_title_from_id(session_id)
        
        if not messagebox.askyesno("删除会话", f"确定删除会话 「{title}」 吗？此操作不可恢复。"):
            return
            
        self.sessions.pop(idx)
        
        filepath = os.path.join(CONV_DIR, f"{session_id}.json")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                print(f"Error removing session file: {e}")
                
        if session_id == self.current_session_id:
            self.current_session_id = None
            self.current_messages = []
            
        self.refresh_listbox_titles()
        
        if self.sessions:
            self.history_listbox.selection_set(0)
            self.load_session_by_id(self.sessions[0])
        else:
            self.new_session()
            
        self.update_status("会话已成功删除。")

    def on_session_select(self, event):
        if self._loading_flag:
            return
        selection = self.history_listbox.curselection()
        if not selection:
            return
            
        idx = selection[0]
        selected_id = self.sessions[idx]
        
        if selected_id == self.current_session_id:
            return
        
        # Don't save current session if it's a new temp session with no messages
        # (temp session IDs have slug that is all digits, shown as "新会话")
        current_slug = self.current_session_id.split("-")[-1] if self.current_session_id else ""
        is_new_temp_session = current_slug.isdigit()
        should_save = not (is_new_temp_session and len(self.current_messages) == 0)
            
        if self.current_session_id and should_save:
            self.save_session_by_id(self.current_session_id)
            
        self.load_session_by_id(selected_id)

    # ─────────────────────────────────────────────
    #  Attachments & File Operations
    # ─────────────────────────────────────────────
    def add_file(self):
        file_paths = filedialog.askopenfilenames(
            title="选择要添加的文件",
            filetypes=[
                ("所有支持的文件 (*.txt; *.png; ...)", "*.txt *.md *.py *.csv *.json *.xml *.yaml *.yml *.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("文本文件 (*.txt;*.md;*.py; ...)", "*.txt *.md *.py *.csv *.json *.xml *.yaml *.yml"),
                ("图片文件 (*.png;*.jpg; ...)", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("所有文件", "*.*")
            ]
        )
        if not file_paths:
            return
            
        for path in file_paths:
            if path not in self.attached_files:
                self.attached_files.append(path)
                
        self.update_attachments_ui()
        self.update_status(f"成功添加了 {len(file_paths)} 个文件。")

    def clear_attachments(self):
        self.attached_files = []
        self.update_attachments_ui()
        self.update_status("附件清除完毕。")

    def update_attachments_ui(self):
        if not self.attached_files:
            self.lbl_attachments.config(text="未选择附件", foreground="gray")
            self.btn_clear_attachments.config(state="disabled")
        else:
            names = [os.path.basename(p) for p in self.attached_files]
            names_str = ", ".join(names)
            if len(names_str) > 80:
                names_str = names_str[:77] + "..."
            self.lbl_attachments.config(text=f"{names_str}  ({len(self.attached_files)}个)", foreground=ACCENT_PURPLE)
            self.btn_clear_attachments.config(state="normal")

    # ─────────────────────────────────────────────
    #  Dialogue Area Render
    # ─────────────────────────────────────────────
    def refresh_chat_display(self):
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        
        # Track active inline image threads to prevent conflicts
        self._rendered_images = []
            
        for msg in self.current_messages:
            role = msg.get("role")
            msg_type = msg.get("type", "text")
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            
            if role == "user":
                self.chat_display.insert(tk.END, "用户\n", "user")
                self.chat_display.insert(tk.END, content + "\n", "user_body")
                
            elif role == "assistant":
                model_name = msg.get("model") or self.model_combo.get() or "助手"
                
                if msg_type == "text":
                    if thinking:
                        self.chat_display.insert(tk.END, "思考过程\n", "thinking_title")
                        self.chat_display.insert(tk.END, thinking + "\n", "thinking")
                    self.chat_display.insert(tk.END, f"{model_name}\n", "assistant")
                    self.chat_display.insert(tk.END, content + "\n", "assistant_body")
                    
                elif msg_type == "image_loading":
                    self.chat_display.insert(tk.END, f"🎨 AI绘画中...\n", "assistant")
                    self.chat_display.insert(tk.END, f"正在生成图片，提示词: \"{msg.get('prompt')}\"...\n", "assistant_body")
                    
                elif msg_type == "image":
                    self.chat_display.insert(tk.END, f"🎨 AI绘画\n", "assistant")
                    info_text = f"提示词: {msg.get('prompt', '')}\n长宽比: {msg.get('aspect_ratio', '16:9')} | 数量: {msg.get('n', 1)}"
                    self.chat_display.insert(tk.END, info_text, "assistant_body")
                    self.chat_display.insert(tk.END, "\n", ())  # empty tuple = NO tag, starts clean line
                    
                    images = msg.get("images", [])
                    if not images:
                        self.chat_display.insert(tk.END, "[未获取到图片链接]\n\n", "error")
                    else:
                        # Prepare short title for filename use
                        img_short_title = msg.get("short_title", "")
                        img_safe_title = self._sanitize_filename(img_short_title) if img_short_title else "image"
                        img_ts = datetime.now().strftime("%m%d-%H%M%S")
                        
                        if len(images) > 1 and HAS_PIL:
                            # ── Multiple images: ◀ cards ... ▶ (left/right arrows) ──
                            scroll_container = tk.Frame(self.chat_display, background="#f1f5f9")

                            # Outer row: [◀] [canvas] [▶]
                            btn_left = tk.Button(scroll_container, text="◀", font=("Segoe UI", 14, "bold"),
                                                 relief="flat", bg="#f1f5f9", fg="#64748b",
                                                 activebackground="#cbd5e1", cursor="hand2",
                                                 width=2, bd=0)
                            btn_left.pack(side="left", fill="y", expand=False)

                            canvas = tk.Canvas(scroll_container, bg="#f1f5f9",
                                               highlightthickness=0, bd=0, height=160)
                            canvas.pack(side="left", fill="both", expand=True)

                            btn_right = tk.Button(scroll_container, text="▶", font=("Segoe UI", 14, "bold"),
                                                  relief="flat", bg="#f1f5f9", fg="#64748b",
                                                  activebackground="#cbd5e1", cursor="hand2",
                                                  width=2, bd=0)
                            btn_right.pack(side="left", fill="y", expand=False)
                            
                            # Inner frame inside canvas
                            card_row = tk.Frame(canvas, bg="#f1f5f9")
                            canvas.create_window((0, 0), window=card_row, anchor="nw")
                            
                            scroll_step = IMAGE_SCROLL_STEP
                            def _scroll_left():
                                canvas.xview_scroll(-1, "units")
                            def _scroll_right():
                                canvas.xview_scroll(1, "units")
                            
                            def _on_card_row_configure(event):
                                canvas.config(scrollregion=canvas.bbox("all"))
                            card_row.bind("<Configure>", _on_card_row_configure)
                            
                            # Populate cards
                            for i, img_obj in enumerate(images):
                                img_url = img_obj.get("url", "")
                                img_ext = self._get_url_extension(img_url, ".png")
                                img_default_name = f"{img_ts}-{img_safe_title}{img_ext}"
                                
                                card = tk.Frame(card_row, background="#ffffff",
                                                highlightbackground="#e2e8f0",
                                                highlightthickness=1, padx=4, pady=4)
                                
                                cached_path = img_obj.get("cache_path")
                                img_label = None
                                if cached_path and os.path.exists(cached_path):
                                    img_label = self._make_thumbnail_label(card, cached_path, THUMBNAIL_CARD_SIZE)
                                else:
                                    cache_dir = os.path.join(CONV_DIR, "cache")
                                    os.makedirs(cache_dir, exist_ok=True)
                                    cache_name = hashlib.md5(img_url.encode('utf-8')).hexdigest() + img_ext
                                    cache_path_fb = os.path.join(cache_dir, cache_name)
                                    if os.path.exists(cache_path_fb):
                                        img_label = self._make_thumbnail_label(card, cache_path_fb, THUMBNAIL_CARD_SIZE)
                                
                                if img_label:
                                    img_label.pack(pady=(0, 4))
                                    self._rendered_images.append(img_label._tk_img_ref)
                                else:
                                    placeholder_lbl = tk.Label(card, text=f"图片 {i+1}\n[加载中…]",
                                                               bg="#ffffff", fg=TEXT_GRAY,
                                                               font=(FONT_UI, 9),
                                                               width=18, height=6)
                                    placeholder_lbl.pack(pady=(0, 4))
                                    dl_cache_path = os.path.join(
                                        os.path.join(CONV_DIR, "cache"),
                                        hashlib.md5(img_url.encode('utf-8')).hexdigest() + img_ext
                                    )
                                    dl_thread = threading.Thread(
                                        target=self._thread_download_and_update_card,
                                        args=(img_url, dl_cache_path, card, placeholder_lbl)
                                    )
                                    dl_thread.daemon = True
                                    dl_thread.start()
                                
                                btn_card = tk.Frame(card, background="#ffffff")
                                tk.Button(btn_card, text="🌐 打开", font=(FONT_UI, 8),
                                          command=lambda u=img_url: webbrowser.open(u),
                                          cursor="hand2", relief="flat", bg="#f1f5f9",
                                          activebackground="#e2e8f0").pack(side="left", padx=2)
                                tk.Button(btn_card, text="💾 保存", font=(FONT_UI, 8),
                                          command=lambda u=img_url, dn=img_default_name:
                                              self.download_and_save_file(u, dn, IMAGE_FILE_FILTER),
                                          cursor="hand2", relief="flat", bg="#f1f5f9",
                                          activebackground="#e2e8f0").pack(side="left", padx=2)
                                btn_card.pack()
                                
                                card.pack(side="left", padx=6, pady=4)
                            
                            btn_left.config(command=_scroll_left)
                            btn_right.config(command=_scroll_right)
                            
                            # Embed and force-stretch to fill the entire chat width
                            self.chat_display.window_create(tk.END, window=scroll_container)
                            self.chat_display.insert(tk.END, "\n")
                            # Retry with increasing delays to catch layout completion
                            for delay in (50, 300):
                                self.after(delay, lambda w=scroll_container: self._force_wide_stretch(w))
                        else:
                            # ── Single image: vertical layout (existing) ──
                            for i, img_obj in enumerate(images):
                                img_url = img_obj.get("url", "")
                                img_ext = self._get_url_extension(img_url, ".png")
                                self.chat_display.insert(tk.END, f"\n图片 {i+1}:\n", "info")
                                
                                btn_frame = tk.Frame(self.chat_display, background="#f1f5f9")
                                btn_open = ttk.Button(btn_frame, text="浏览器打开", command=lambda url=img_url: webbrowser.open(url))
                                btn_open.pack(side="left", padx=5, pady=2)
                                
                                img_default_name = f"{img_ts}-{img_safe_title}{img_ext}"
                                btn_save = ttk.Button(btn_frame, text="保存图片", command=lambda url=img_url, dn=img_default_name: self.download_and_save_file(url, dn, IMAGE_FILE_FILTER))
                                btn_save.pack(side="left", padx=5, pady=2)
                                
                                self.chat_display.window_create(tk.END, window=btn_frame)
                                self.chat_display.insert(tk.END, "\n")
                                
                                # Handle caching and displaying inline image in chat
                                if HAS_PIL:
                                    cache_dir = os.path.join(CONV_DIR, "cache")
                                    os.makedirs(cache_dir, exist_ok=True)
                                    img_ext = self._get_url_extension(img_url, ".png")
                                    cache_name = hashlib.md5(img_url.encode('utf-8')).hexdigest() + img_ext
                                    cache_path = os.path.join(cache_dir, cache_name)
                                    
                                    # Prefer existing cache_path stored by handle_image_success
                                    cached_path = img_obj.get("cache_path")
                                    if cached_path and os.path.exists(cached_path):
                                        self.display_cached_image_in_chat(cached_path)
                                        continue
                                    
                                    is_valid = False
                                    if os.path.exists(cache_path):
                                        try:
                                            with PILImage.open(cache_path) as test_img:
                                                test_img.verify()
                                            is_valid = True
                                        except Exception:
                                            try:
                                                os.remove(cache_path)
                                            except Exception:
                                                pass
                                                    
                                    if is_valid:
                                        self.display_cached_image_in_chat(cache_path)
                                    else:
                                        placeholder = f"[正在下载并渲染图片 {i+1} ({cache_name[:8]})...]"
                                        self.chat_display.insert(tk.END, placeholder + "\n", "info")
                                        t = threading.Thread(target=self.thread_download_and_render_image_in_chat, args=(img_url, cache_path, placeholder))
                                        t.daemon = True
                                        t.start()
                                    
                elif msg_type == "music_loading":
                    self.chat_display.insert(tk.END, f"🎵 AI 音乐生成中...\n", "assistant")
                    self.chat_display.insert(tk.END, f"正在呼叫 AI 音乐生成接口，提示词: \"{msg.get('prompt')}\"...\n", "assistant_body")
                    
                elif msg_type == "music":
                    self.chat_display.insert(tk.END, f"🎵 AI 音乐\n", "assistant")
                    info_text = f"风格提示词: {msg.get('prompt', '')}\n"
                    self.chat_display.insert(tk.END, info_text, "assistant_body")
                    
                    lyrics = msg.get("lyrics", "")
                    if lyrics:
                        self.chat_display.insert(tk.END, "--- 歌词 ---\n", "info")
                        self.chat_display.insert(tk.END, f"{lyrics}\n", "lyrics")
                        self.chat_display.insert(tk.END, "------------\n", "info")
                        
                    audio_url = msg.get("audio_url", "")
                    if not audio_url:
                        self.chat_display.insert(tk.END, "[生成失败或音频数据为空]\n\n", "error")
                    else:
                        audio_ext = self._get_url_extension(audio_url, ".mp3")
                        audio_short_title = msg.get("short_title", "")
                        audio_safe_title = self._sanitize_filename(audio_short_title) if audio_short_title else "music"
                        audio_ts = datetime.now().strftime("%m%d-%H%M%S")
                        audio_default_name = f"{audio_ts}-{audio_safe_title}{audio_ext}"
                        
                        btn_frame = tk.Frame(self.chat_display, background="#f1f5f9")
                        btn_open = ttk.Button(btn_frame, text="浏览器播放/下载", command=lambda url=audio_url: webbrowser.open(url))
                        btn_open.pack(side="left", padx=5, pady=2)
                        
                        btn_save = ttk.Button(btn_frame, text="保存音频文件", command=lambda url=audio_url, dn=audio_default_name: self.download_and_save_file(url, dn, f"音频文件 (*{audio_ext};*.mp3)"))
                        btn_save.pack(side="left", padx=5, pady=2)
                        
                        self.chat_display.window_create(tk.END, window=btn_frame)
                        self.chat_display.insert(tk.END, "\n\n")
                        
            elif role == "system":
                self.chat_display.insert(tk.END, "系统提示\n", "system")
                self.chat_display.insert(tk.END, content + "\n", "system_body")
                
        self.chat_display.config(state="disabled")
        self._bind_mousewheel_recursive(self.chat_display)
        self.chat_display.see(tk.END)

    def _bind_mousewheel_recursive(self, widget):
        for child in widget.winfo_children():
            # Bind MouseWheel to redirect to chat_display
            child.bind("<MouseWheel>", self._on_child_mousewheel, add="+")
            child.bind("<Button-4>", self._on_child_mousewheel, add="+")
            child.bind("<Button-5>", self._on_child_mousewheel, add="+")
            self._bind_mousewheel_recursive(child)

    def _on_child_mousewheel(self, event):
        if event.num == 4:
            self.chat_display.yview_scroll(-2, "units")
        elif event.num == 5:
            self.chat_display.yview_scroll(2, "units")
        elif event.delta:
            scroll_units = -1 * int(event.delta / 120) * 3
            self.chat_display.yview_scroll(scroll_units, "units")
        return "break"

    def _make_thumbnail_label(self, parent, cache_path, max_w=200):
        """Create a tk.Label with a fitted thumbnail from a cached image."""
        try:
            if not HAS_PIL:
                return None
            pil_img = PILImage.open(cache_path)
            w, h = pil_img.size
            if w > max_w:
                h = int(h * (max_w / w))
                w = max_w
            resample = getattr(getattr(PILImage, "Resampling", None), "LANCZOS",
                               getattr(PILImage, "LANCZOS", 1))
            pil_img = pil_img.resize((w, h), resample)
            tk_img = ImageTk.PhotoImage(pil_img)
            lbl = tk.Label(parent, image=tk_img, bg="#ffffff")
            # Keep a reference so the image is not garbage-collected
            lbl._tk_img_ref = tk_img
            self._rendered_images.append(tk_img)
            return lbl
        except Exception as e:
            print(f"Error making thumbnail: {e}")
            return None

    def _thread_download_and_update_card(self, url, cache_path, card, placeholder_lbl):
        """Download image and replace placeholder label with thumbnail."""
        try:
            if cache_path is None:
                cache_dir = os.path.join(CONV_DIR, "cache")
                os.makedirs(cache_dir, exist_ok=True)
                ext = self._get_url_extension(url, ".png")
                cache_name = hashlib.md5(url.encode('utf-8')).hexdigest() + ext
                cache_path = os.path.join(cache_dir, cache_name)
            
            # Download if not already cached
            if not os.path.exists(cache_path):
                try:
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_DEFAULT)
                except requests.exceptions.SSLError:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_DEFAULT, verify=False)
                if r.status_code == 200 and len(r.content) > 0:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, 'wb') as f:
                        f.write(r.content)
            
            if os.path.exists(cache_path):
                self.after(0, self._replace_card_placeholder, card, cache_path, placeholder_lbl)
        except Exception as e:
            print(f"Error downloading image for card: {e}")

    def _replace_card_placeholder(self, card, cache_path, placeholder_lbl):
        """Replace placeholder label with actual thumbnail in card."""
        try:
            img_lbl = self._make_thumbnail_label(card, cache_path, THUMBNAIL_CARD_SIZE)
            if img_lbl:
                placeholder_lbl.pack_forget()
                img_lbl.pack(pady=(0, 4))
        except Exception as e:
            print(f"Error replacing card placeholder: {e}")

    def _force_wide_stretch(self, widget):
        """Force embedded widget to fill the full chat display width."""
        try:
            self.update_idletasks()
            cw = self.chat_display.winfo_width()
            if cw > 1:
                self.chat_display.window_config(widget, width=cw)
            self.chat_display.window_config(widget, stretch=1)
        except tk.TclError:
            pass

    def display_cached_image_in_chat(self, cache_path):
        try:
            if not HAS_PIL:
                return
            pil_img = PILImage.open(cache_path)
            w, h = pil_img.size
            if w > INLINE_IMAGE_MAX_W:
                h = int(h * (INLINE_IMAGE_MAX_W / w))
                w = INLINE_IMAGE_MAX_W
                resample_filter = getattr(getattr(PILImage, "Resampling", None), "LANCZOS", getattr(PILImage, "LANCZOS", 1))
                pil_img = pil_img.resize((w, h), resample_filter)
                
            tk_img = ImageTk.PhotoImage(pil_img)
            self._rendered_images.append(tk_img)
            
            self.chat_display.image_create(tk.END, image=tk_img)
            self.chat_display.insert(tk.END, "\n")
        except Exception as e:
            print(f"Error displaying cached image in chat: {e}")

    def thread_download_and_render_image_in_chat(self, url, cache_path, placeholder):
        try:
            try:
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_SHORT)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_SHORT, verify=False)
            if r.status_code == 200:
                if len(r.content) > 0:
                    with open(cache_path, 'wb') as f:
                        f.write(r.content)
                    self.after(0, self.render_image_from_cache_in_chat, cache_path, placeholder)
                else:
                    self.after(0, self.handle_render_image_error_in_chat, placeholder, "空图片数据")
            else:
                self.after(0, self.handle_render_image_error_in_chat, placeholder, f"HTTP {r.status_code}")
        except Exception as e:
            self.after(0, self.handle_render_image_error_in_chat, placeholder, str(e))

    def render_image_from_cache_in_chat(self, cache_path, placeholder):
        try:
            if not HAS_PIL:
                return
            pil_img = PILImage.open(cache_path)
            w, h = pil_img.size
            if w > INLINE_IMAGE_MAX_W:
                h = int(h * (INLINE_IMAGE_MAX_W / w))
                w = INLINE_IMAGE_MAX_W
                resample_filter = getattr(getattr(PILImage, "Resampling", None), "LANCZOS", getattr(PILImage, "LANCZOS", 1))
                pil_img = pil_img.resize((w, h), resample_filter)
                
            tk_img = ImageTk.PhotoImage(pil_img)
            self._rendered_images.append(tk_img)
            
            self.chat_display.config(state="normal")
            pos = self.chat_display.search(placeholder, "1.0", tk.END, exact=True)
            if pos:
                self.chat_display.delete(pos, f"{pos}+{len(placeholder)}c")
                self.chat_display.image_create(pos, image=tk_img)
                self.chat_display.image_create(pos, image=tk_img)
            else:
                self.chat_display.image_create(tk.END, image=tk_img)
                self.chat_display.insert(tk.END, "\n")
            self.chat_display.config(state="disabled")
        except Exception as e:
            print(f"Error rendering image from cache in chat: {e}")
            self.handle_render_image_error_in_chat(placeholder, str(e))

    def handle_render_image_error_in_chat(self, placeholder, error_msg):
        try:
            self.chat_display.config(state="normal")
            pos = self.chat_display.search(placeholder, "1.0", tk.END, exact=True)
            if pos:
                self.chat_display.delete(pos, f"{pos}+{len(placeholder)}c")
                self.chat_display.insert(pos, f"[图片下载/渲染失败: {error_msg}]", "error")
            self.chat_display.config(state="disabled")
        except Exception as e:
            print(f"Error handling render image error in chat: {e}")

    # ─────────────────────────────────────────────
    #  Input Hotkeys
    # ─────────────────────────────────────────────
    # ─────────────────────────────────────────────
    #  Unified Session Management
    # ─────────────────────────────────────────────
    def _async_ai_short_title(self, prompt, msg_idx):
        """Call LLM in background to generate a ≤10 char description from prompt."""
        try:
            ai_prompt = (
                f"请用最多10个汉字概括以下内容的核心主题（只输出概括文字，不要标点、引号、多余字）：{prompt}"
            )
            desc, _ = call_chat_api(
                provider=SHORT_TITLE_PROVIDER,
                model=SHORT_TITLE_MODEL,
                history=[],
                prompt=ai_prompt,
                b64_images=[],
                system_prompt="???????????????10???????????????????"
            )
            short = desc.strip().strip('"').strip("'").strip('「」『』（）【】') if desc else ""
            short = short[:10].replace("\n", "").replace("\r", "")
            if short:
                self.after(0, self._update_msg_short_title, msg_idx, short)
        except Exception as e:
            print(f"AI short title generation failed: {e}")

    def _update_msg_short_title(self, msg_idx, short_title):
        """Update short_title in a message (thread-safe, called from main thread)."""
        if 0 <= msg_idx < len(self.current_messages):
            self.current_messages[msg_idx]["short_title"] = short_title
            self.refresh_chat_display()

    def _ensure_current_session(self):
        """Ensure a current session exists, create new one if needed."""
        if not self.current_session_id:
            self.new_session()

    def _generate_short_title(self, text, max_len=9):
        """Generate a short title (max_len chars) from text for use in filename."""
        import re
        # Take first line, remove extra whitespace
        title = text.split("\n")[0].strip()
        
        # Remove common prefixes for clean titles
        prefixes_to_remove = [
            r'^生成音乐[:：]\s*',
            r'^生成图片[:：]\s*".*?"\s*',  # Must come before simple prefix to match quoted prompts
            r'^生成图片[:：]\s*',
        ]
        for prefix in prefixes_to_remove:
            title = re.sub(prefix, '', title)
        
        # Remove content in parentheses like (比例: 16:9, 数量: 1)
        title = re.sub(r'\s*\(比例[:：]\s*[^)]+\)\s*', '', title)
        
        # Remove quotes
        title = title.replace('"', '').replace('"', '').replace("'", "")
        
        # Remove extra spaces
        title = " ".join(title.split())
        
        if len(title) > max_len:
            title = title[:max_len]
        return title

    def _sanitize_filename(self, text):
        """Remove/replace characters invalid in filenames."""
        # Remove chars invalid in Windows filenames: \ / : * ? " < > |
        for ch in '\\/:*?"<>|':
            text = text.replace(ch, "_")
        # Replace spaces with underscores
        text = text.replace(" ", "_")
        return text

    def _get_url_extension(self, url, default_ext="png"):
        """Extract file extension from URL, return default if not found."""
        import re
        # Try to find extension pattern like .jpg, .jpeg, .png, .mp3, .wav, etc.
        match = re.search(r'(\.[a-zA-Z0-9]+)(?:\?|$)', url)
        if match:
            ext = match.group(1).lower()
            # Normalize common extensions
            if ext in ['.jpeg', '.jpg']:
                return '.jpg'  # Standardize to .jpg
            return ext
        return default_ext

    def _download_to_cache(self, url, custom_name=None, timeout=60):
        """Download file from URL and save to cache directory. Returns cache path or None.
        
        Args:
            url: URL of the file to download
            custom_name: Optional custom name for the file. If provided, filename format is MM-DD-HHMMSS-custom_name.ext
                       If None, uses MD5 hash of URL as filename.
            timeout: Request timeout in seconds (default 60, use 120 for larger audio files)
        """
        try:
            cache_dir = os.path.join(CONV_DIR, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            
            ext = self._get_url_extension(url, ".png")
            
            if custom_name:
                # Sanitize custom_name for use in filename
                safe_name = self._sanitize_filename(custom_name)
                # Format: MM-DD-HHMMSS-custom_name.ext
                timestamp_str = datetime.now().strftime("%m-%d-%H%M%S")
                cache_name = f"{timestamp_str}-{safe_name}{ext}"
            else:
                cache_name = hashlib.md5(url.encode('utf-8')).hexdigest() + ext
            
            cache_path = os.path.join(cache_dir, cache_name)
            
            if os.path.exists(cache_path):
                return cache_path
            
            try:
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=timeout)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=timeout, verify=False)
                
            if r.status_code == 200 and len(r.content) > 0:
                with open(cache_path, 'wb') as f:
                    f.write(r.content)
                return cache_path
        except Exception as e:
            print(f"Error downloading to cache: {e}")
        return None

    def on_enter_pressed(self, event):
        model = self.model_combo.get()
        if is_music_model(model):
            self.generate_music()
        elif self._is_image_model(model):
            self.generate_image()
        else:
            self.send_message()
        return "break" # Prevent typing standard newline in the widget

    def on_shift_enter_pressed(self, event):
        self.input_text.insert(tk.INSERT, "\n")
        self.input_text.see(tk.INSERT)
        return "break"

    # ─────────────────────────────────────────────
    #  Control States Lock
    # ─────────────────────────────────────────────
    def set_controls_state(self, state):
        self.btn_add_file.config(state=state)
        if hasattr(self, 'btn_send'): self.btn_send.config(state=state)
        if hasattr(self, 'input_text'): self.input_text.config(state="normal" if state == "normal" else "disabled")
        self._is_processing = (state != "normal")

    def update_status(self, text):
        self.status_bar.config(text=text)

    # ─────────────────────────────────────────────
    #  SendMessage Execution Flow
    # ─────────────────────────────────────────────
    def send_message(self):
        if self._is_processing:
            return

        model = self.model_combo.get()
        if is_music_model(model):
            self.generate_music()
            return
        if self._is_image_model(model):
            self.generate_image()
            return
            
        user_text = self.input_text.get("1.0", tk.END).strip()
        
        if not user_text and not self.attached_files:
            return
            
        self._ensure_current_session()
            
        text_attachments = []
        b64_images = []
        attached_names = []
        
        for filepath in self.attached_files:
            filename = os.path.basename(filepath)
            attached_names.append(filename)
            ext = filename.split(".")[-1].lower() if "." in filename else ""
            
            if ext in TXT_EXTS:
                content = read_text_file(filepath)
                text_attachments.append(f"\n\n[附带文本文件: {filename}]\n---\n{content}\n---")
            elif ext in IMAGE_EXTS:
                b64_data, mime = read_image_base64(filepath)
                if b64_data:
                    b64_images.append((b64_data, mime))
                else:
                    text_attachments.append(f"\n\n[附带图片: {filename} (读取失败)]")
            elif ext in VIDEO_EXTS:
                b64_data, mime = read_image_base64(filepath)
                if mime and mime.startswith("image/"):
                    mime = "video/mp4"
                elif not mime:
                    mime = "video/mp4"
                if b64_data:
                    b64_images.append((b64_data, mime))
                else:
                    text_attachments.append(f"\n\n[附带视频: {filename} (读取失败)]")
            else:
                try:
                    size_kb = os.path.getsize(filepath) / 1024
                    text_attachments.append(f"\n\n[附带外部文件: {filename} ({size_kb:.1f} KB)]")
                except Exception:
                    text_attachments.append(f"\n\n[附带外部文件: {filename}]")
                    
        full_prompt = user_text
        if text_attachments:
            full_prompt += "".join(text_attachments)
            
        display_content = user_text
        if attached_names:
            display_content += "\n[已附加文件: " + ", ".join(attached_names) + "]"
            
        user_msg = {
            "role": "user",
            "content": display_content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.current_messages.append(user_msg)
        self.refresh_chat_display()
        
        # Save session immediately with user's request as title
        self._save_session_on_user_request(display_content, model)
        
        # Clear fields
        self.input_text.delete("1.0", tk.END)
        self.attached_files = []
        self.update_attachments_ui()
        
        # Start call thread
        self.set_controls_state("disabled")
        self.update_status("正在请求大模型，生成回复中...")
        
        t = threading.Thread(target=self.thread_call_llm, args=(full_prompt, b64_images))
        t.daemon = True
        t.start()

    # ─────────────────────────────────────────────
    #  Async Backend Call Thread
    # ─────────────────────────────────────────────
    def thread_call_llm(self, prompt, b64_images):
        provider = self.provider_combo.get()
        model = self.model_combo.get()
        system_prompt = self.system_text.get("1.0", tk.END).strip()
        
        # Extract previous messages for conversation history (skip image/music messages)
        api_history = []
        for msg in self.current_messages[:-1]:
            if msg.get("type", "text") == "text":
                api_history.append({"role": msg["role"], "content": msg["content"]})
            
        success = False
        text_result = ""
        thinking_result = ""
        error_msg = ""
        
        try:
            text_result, thinking_result = call_chat_api(provider, model, api_history, prompt, b64_images, system_prompt)
            success = True
        except Exception as e:
            import traceback
            traceback.print_exc()
            success = False
            
            # Clean error message for display
            error_msg = str(e)
            # Truncate if too long
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            
            # Handle specific request exceptions
            import requests.exceptions
            if isinstance(e, requests.exceptions.ReadTimeout):
                error_msg = f"API 请求超时 (ReadTimeout) - 服务器响应过慢，请重试"
            elif isinstance(e, requests.exceptions.ConnectionError):
                error_msg = f"网络连接错误 (ConnectionError) - 请检查网络连接"
            elif isinstance(e, requests.exceptions.HTTPError):
                # Show the actual response text from the API
                resp_text = str(e)
                if len(resp_text) > 200:
                    resp_text = resp_text[:200] + "..."
                error_msg = f"API HTTP 错误: {resp_text}"
            elif isinstance(e, requests.exceptions.RequestException):
                error_msg = f"API 请求错误: {str(e)[:200]}"
            
        if success:
            self.after(0, self.handle_api_response, text_result, thinking_result)
        else:
            self.after(0, self.handle_api_error, error_msg)

    # ─────────────────────────────────────────────
    #  Immediate Session Save on User Request
    # ─────────────────────────────────────────────
    def _save_session_on_user_request(self, user_content, model):
        """Save session immediately when user sends a request (before AI replies).
        
        Generates title from user content, creates the final filename,
        writes the session file, and updates the sidebar list.
        """
        self._ensure_current_session()
        
        current_id = self.current_session_id
        current_slug = current_id.split("-")[-1] if current_id else ""
        # Session is temp if slug is digits or not in self.sessions yet
        is_new_temp_session = current_slug.isdigit() or (current_id not in self.sessions)
        
        if is_new_temp_session:
            first_line = user_content.split("\n")[0].strip() if user_content else ""
            if first_line:
                slug = self._generate_short_title(first_line, max_len=10)
                safe_slug = self._sanitize_filename(slug)
                if safe_slug and safe_slug != "_":
                    ts = datetime.now().strftime(DATETIME_FORMAT)
                    final_id = f"{ts}-{safe_slug}"
                    old_path = os.path.join(CONV_DIR, f"{current_id}.json")
                    if os.path.exists(old_path):
                        try:
                            os.remove(old_path)
                        except Exception:
                            pass
                    self.current_session_id = final_id
                    
        if self.current_session_id not in self.sessions:
            self.sessions.insert(0, self.current_session_id)
        self.refresh_listbox_titles()
        
        title = self._session_title_from_id(self.current_session_id)
        if hasattr(self, "lbl_session_title"):
            self.lbl_session_title.config(text=title)
        
        self.save_session_by_id(self.current_session_id)

    # ─────────────────────────────────────────────
    #  Shared Session Registration & Rename
    # ─────────────────────────────────────────────
    def _register_and_rename_session(self, model):
        """Persist the newly added assistant message after first response."""
        if not self.current_messages:
            return
        
        # Session title and file name are already set by _save_session_on_user_request.
        # Do NOT update them in subsequent rounds.
        # Only save if this session was already saved (has a proper ID, not the temp -%f format)
        if '-' in self.current_session_id and not self.current_session_id.endswith('-'):
            self.save_session_by_id(self.current_session_id)
            
            # 💡 新增：在对话首轮（1条user消息+1条assistant消息）触发后台AI异步会话摘要生成，智能提炼侧边栏标题
            text_msgs = [m for m in self.current_messages if m.get("type", "text") == "text" or m.get("type") in ["image", "music"]]
            if len(text_msgs) <= 2:
                first_msg_content = text_msgs[0]["content"] if text_msgs else ""
                if first_msg_content:
                    t = threading.Thread(target=self._thread_generate_ai_session_title, args=(first_msg_content,))
                    t.daemon = True
                    t.start()

    def _thread_generate_ai_session_title(self, user_prompt):
        """后台线程：使用当前服务商的文本模型根据用户首条 Prompt 提炼出 3-16 字的高清会话标题"""
        provider = self.provider_combo.get()
        # 寻找当前提供商排在第一位的文本模型来进行摘要提炼
        text_model = None
        for m in PROVIDERS.get(provider, []):
            if not self._is_image_model(m) and not is_music_model(m):
                text_model = m
                break
                
        if not text_model:
            return
            
        system_prompt = (
            "You are a professional session summarizer. Give an extremely short, concise Chinese title (3 to 16 Chinese characters) "
            "for this conversation based on the user's first prompt. Do not use quotes, punctuation, or any extra text."
        )
        
        try:
            # 去除前缀和参数干扰，提取纯净的提示词描述
            cleaned_prompt = user_prompt
            if cleaned_prompt.startswith("生成图片:"):
                cleaned_prompt = cleaned_prompt.replace("生成图片:", "").strip()
            elif cleaned_prompt.startswith("生成音乐:"):
                cleaned_prompt = cleaned_prompt.replace("生成音乐:", "").strip()
                
            import re
            cleaned_prompt = re.sub(r"\(比例:.*?, 数量:.*?\)", "", cleaned_prompt).strip()
            cleaned_prompt = re.sub(r"\(歌词自动生成\)", "", cleaned_prompt).strip()
            cleaned_prompt = re.sub(r"\(自定义歌词:.*?\)", "", cleaned_prompt).strip()
            cleaned_prompt = cleaned_prompt.strip('"').strip("'").strip('“').strip('”')
            
            text_result, _ = call_chat_api(provider, text_model, [], cleaned_prompt, [], system_prompt)
                
            ai_title = text_result.strip().strip('"').strip("'").strip('「」『』（）【】“’”')
            if ai_title and len(ai_title) > 0:
                ai_title = ai_title[:16]  # 限制在16字以内，避免侧边栏溢出
                # 线程安全地在主线程更新UI
                self.after(0, self._apply_ai_session_title, self.current_session_id, ai_title)
        except Exception as e:
            print(f"Error generating AI session title in background: {e}")

    def _apply_ai_session_title(self, session_id, ai_title):
        """主线程安全：将 AI 生成的精致标题应用到缓存、UI、和磁盘物理文件名中"""
        if session_id == self.current_session_id:
            old_session_id = self.current_session_id
            
            # 1. 自动提取原本的时间戳前缀并组装包含新 AI 摘要的安全文件名/ID
            ts = old_session_id[:17] if len(old_session_id) >= 17 else datetime.now().strftime(DATETIME_FORMAT)
            safe_ai_slug = self._sanitize_filename(ai_title)
            new_session_id = f"{ts}-{safe_ai_slug}"
            
            # 2. 如果文件名需要改变，执行平滑安全的磁盘文件迁移
            if new_session_id != old_session_id:
                old_filepath = os.path.join(CONV_DIR, f"{old_session_id}.json")
                new_filepath = os.path.join(CONV_DIR, f"{new_session_id}.json")
                
                # 迁移内存 Session 变量与集合
                self.current_session_id = new_session_id
                
                if old_session_id in self.sessions:
                    idx = self.sessions.index(old_session_id)
                    self.sessions[idx] = new_session_id
                    
                # 迁移内存中的展示标题缓存
                self.session_titles[new_session_id] = ai_title
                self.session_titles.pop(old_session_id, None)
                
                # 安全保存新 JSON 文件
                self.save_session_by_id(new_session_id)
                
                # 释放并清除磁盘上的旧 JSON 文件
                if os.path.exists(old_filepath):
                    try:
                        os.remove(old_filepath)
                    except Exception as e:
                        print(f"Error removing old session file {old_session_id}.json during rename: {e}")
            else:
                self.session_titles[old_session_id] = ai_title
            
            # 3. 更新右侧顶部大标题
            if hasattr(self, 'lbl_session_title'):
                self.lbl_session_title.config(text=ai_title)
                
            # 4. 刷新侧边栏列表标题显示
            self.refresh_listbox_titles()
            
            # 5. 再次保存确保万无一失
            self.save_session_by_id(self.current_session_id)
            
            self.update_status(f"会话及 JSON 文件名提炼成功: 「{ai_title}」")

    # ─────────────────────────────────────────────
    #  LLM API Clients Dispatch
    # ─────────────────────────────────────────────
    def handle_api_response(self, text, thinking):
        import re
        if text:
            text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()
        if thinking:
            thinking = re.sub(r'\n\s*\n\s*\n+', '\n\n', thinking).strip()

        model = self.model_combo.get()
        asst_msg = {
            "role": "assistant",
            "model": model,
            "content": text,
            "thinking": thinking,
            "timestamp": datetime.now().isoformat()
        }
        self.current_messages.append(asst_msg)
        
        self._register_and_rename_session(model)

        self.refresh_chat_display()
        
        self.set_controls_state("normal")
        self.update_status("准备就绪")

    def handle_api_error(self, error_message):
        try:
            self.chat_display.config(state="normal")
            self.chat_display.insert(tk.END, "错误信息:\n", "error")
            self.chat_display.insert(tk.END, f"调用大模型API失败: {error_message}\n\n")
            self.chat_display.config(state="disabled")
            self.chat_display.see(tk.END)
            
            self.set_controls_state("normal")
            self.update_status(f"错误: {error_message[:50]}...")
        except Exception as e:
            # Fallback error handling if UI update fails
            print(f"Error displaying API error: {e}")
            self.set_controls_state("normal")
            self.update_status(f"API 调用失败: {error_message[:30]}...")

    # ─────────────────────────────────────────────
    #  Model selection handlers & Overrides
    # ─────────────────────────────────────────────
    def on_model_changed(self, event=None):
        if not hasattr(self, 'model_combo'):
            return
        model = self.model_combo.get()
        
        # Unpack all optional elements from params_bar to maintain clean state
        if hasattr(self, 'lbl_aspect'): self.lbl_aspect.pack_forget()
        if hasattr(self, 'img_aspect_combo'): self.img_aspect_combo.pack_forget()
        if hasattr(self, 'lbl_n'): self.lbl_n.pack_forget()
        if hasattr(self, 'img_n_combo'): self.img_n_combo.pack_forget()
        if hasattr(self, 'btn_edit_lyrics'): self.btn_edit_lyrics.pack_forget()
        
        if is_music_model(model):
            # Show music specific options
            if hasattr(self, 'btn_edit_lyrics'): self.btn_edit_lyrics.pack(side="left", padx=(15, 2))
                
        elif self._is_image_model(model):
            # Show image specific options
            if hasattr(self, 'lbl_aspect'): self.lbl_aspect.pack(side="left", padx=(5, 2))
            if hasattr(self, 'img_aspect_combo'): self.img_aspect_combo.pack(side="left", padx=2)
            if hasattr(self, 'lbl_n'): self.lbl_n.pack(side="left", padx=(10, 2))
            if hasattr(self, 'img_n_combo'): self.img_n_combo.pack(side="left", padx=2)
            
            # Set default values
            if hasattr(self, 'img_aspect_combo'): self.img_aspect_combo.set("16:9")
            if hasattr(self, 'img_n_combo'): self.img_n_combo.set("1")
            
        self.update_idletasks()

    def parse_image_prompt_overrides(self, prompt, default_aspect="16:9", default_n=1):
        aspect_ratio = default_aspect
        n = default_n
        
        # Check for aspect ratios in prompt
        aspect_ratios = ["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "21:9"]
        for r in aspect_ratios:
            if r in prompt:
                aspect_ratio = r
                break
                
        # Handle Chinese variations
        aspect_map = {
            "1比1": "1:1",
            "16比9": "16:9",
            "9比16": "9:16",
            "4比3": "4:3",
            "3比4": "3:4",
            "2比3": "2:3",
            "21比9": "21:9",
        }
        for k, v in aspect_map.items():
            if k in prompt:
                aspect_ratio = v
                break
                
        # Handle orientation hints
        ratio_hints = {
            "头像": "1:1",
            "壁纸": "16:9",
            "横图": "16:9",
            "横版": "16:9",
            "竖图": "9:16",
            "竖版": "9:16",
        }
        for k, v in ratio_hints.items():
            if k in prompt:
                aspect_ratio = v
                break
                
        # Check for image quantity overrides
        import re
        num_map = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
        
        match = re.search(r'([1-4一两二三四])\s*(?:张|幅|个|张图|张图片|张照片)', prompt)
        if match:
            n = num_map[match.group(1)]
        else:
            match = re.search(r'(?:数量|张数)\s*[:：]?\s*([1-4一两二三四])', prompt)
            if match:
                n = num_map[match.group(1)]
                
        return aspect_ratio, n

    # ─────────────────────────────────────────────
    #  Edit Lyrics Dialog
    # ─────────────────────────────────────────────
    def edit_lyrics_popup(self):
        popup = tk.Toplevel(self)
        popup.title("编辑歌词")
        popup.geometry("400x350")
        popup.transient(self)
        popup.grab_set()
        
        # Center the popup relative to self
        x = self.winfo_x() + (self.winfo_width() - 400) // 2
        y = self.winfo_y() + (self.winfo_height() - 350) // 2
        popup.geometry(f"400x350+{x}+{y}")
        
        lbl = ttk.Label(popup, text="请输入自定义歌词（不输入则自动生成）：", font=(FONT_UI, 9, "bold"))
        lbl.pack(anchor="w", padx=10, pady=(10, 5))
        
        text_frame = ttk.Frame(popup)
        text_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        lyric_text = tk.Text(text_frame, wrap="word", font=(FONT_UI, 10), bd=0, highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor=ACCENT_BLUE)
        lyric_text.pack(side="left", fill="both", expand=True)
        
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=lyric_text.yview)
        scroll.pack(side="right", fill="y")
        lyric_text.config(yscrollcommand=scroll.set)
        
        # Insert current lyrics if any
        if hasattr(self, "custom_lyrics") and self.custom_lyrics:
            lyric_text.insert("1.0", self.custom_lyrics)
            
        def on_save():
            self.custom_lyrics = lyric_text.get("1.0", tk.END).strip()
            if self.custom_lyrics:
                self.btn_edit_lyrics.config(text="✍️ 添加歌词 (已编辑)")
            else:
                self.btn_edit_lyrics.config(text="✍️ 添加歌词")
            popup.destroy()
            
        def on_clear():
            self.custom_lyrics = ""
            self.btn_edit_lyrics.config(text="✍️ 添加歌词")
            popup.destroy()
            
        btn_frame = ttk.Frame(popup, padding=(0, 10))
        btn_frame.pack(fill="x", side="bottom")
        
        btn_ok = ttk.Button(btn_frame, text="确定", command=on_save)
        btn_ok.pack(side="right", padx=(5, 10))
        
        btn_cl = ttk.Button(btn_frame, text="清空并关闭", command=on_clear)
        btn_cl.pack(side="right", padx=5)

    # ─────────────────────────────────────────────
    #  AI Image Generation
    # ─────────────────────────────────────────────
    def generate_image(self):
        if self._is_processing:
            return

        prompt = self.input_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("警告", "请先在输入框中输入图片生成提示词！")
            return
            
        aspect_ratio = self.img_aspect_combo.get()
        try:
            n = int(self.img_n_combo.get())
        except Exception:
            n = 1
            
        # Parse prompt overrides
        aspect_ratio, n = self.parse_image_prompt_overrides(prompt, default_aspect=aspect_ratio, default_n=n)
        self.img_aspect_combo.set(aspect_ratio)
        self.img_n_combo.set(str(n))
            
        model = self.model_combo.get()
        if not self._is_image_model(model):
            model = DEFAULT_IMAGE_MODEL
        prompt_optimizer = True
        
        # Clear main input text
        self.input_text.delete("1.0", tk.END)
        
        self._ensure_current_session()
        
        # Log the user's action
        user_msg = {
            "role": "user",
            "content": f"生成图片: \"{prompt}\" (比例: {aspect_ratio}, 数量: {n})",
            "timestamp": datetime.now().isoformat()
        }
        self.current_messages.append(user_msg)
        
        # Insert temporary image_loading message
        loading_msg = {
            "role": "assistant",
            "type": "image_loading",
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": n,
            "timestamp": datetime.now().isoformat()
        }
        self.current_messages.append(loading_msg)
        self.refresh_chat_display()
        
        # Save session immediately with user's request as title
        self._save_session_on_user_request(f"生成图片: {prompt}", model)
        
        self.set_controls_state("disabled")
        self.update_status("正在生成图片，请稍候...")
        
        t = threading.Thread(target=self.thread_generate_image, args=(prompt, model, aspect_ratio, n, prompt_optimizer, ""))
        t.daemon = True
        t.start()

    def _rewrite_prompt_with_history(self, history, current_prompt, media_type="image"):
        """Use text LLM to rewrite, expand, and merge the current prompt based on previous session history.
        Makes image/music generation perfectly aware of previous context.
        """
        if not history:
            return current_prompt
            
        provider = self.provider_combo.get()
        # Find the first text model of the current provider
        text_model = None
        for m in PROVIDERS.get(provider, []):
            if not self._is_image_model(m) and not is_music_model(m):  # Skip multimedia models
                text_model = m
                break
                
        if not text_model:
            return current_prompt  # Fallback to current prompt if no text model
            
        if media_type == "image":
            system_prompt = (
                "You are an expert AI image prompt optimizer. The user wants to generate an image. "
                "They might refer to previous images, descriptions, or prompts in the conversation. "
                "Based on the conversation history and their new request, rewrite and output a single, "
                "detailed, and optimized English or Chinese text-to-image prompt that incorporates their feedback. "
                "If the user asks to modify the previous image, you must look at the previous image prompts and "
                "apply the user's modifications (e.g. changing colors, adding/removing objects, altering style, "
                "changing mood/brightness) to the previous detailed prompt, and output the new combined detailed prompt. "
                "Output ONLY the final expanded/optimized prompt string. Do not include any explanations, "
                "introductory words, or quotes."
            )
        else:  # music
            system_prompt = (
                "You are an expert AI music style and prompt optimizer. The user wants to generate music. "
                "They might refer to previous music style descriptions or lyrics in the conversation. "
                "Based on the conversation history and their new request, rewrite and output a single, "
                "detailed, and optimized music style prompt (e.g. Mandopop, Upbeat, Celebration, New Year, with instruments and tempo) "
                "incorporating their feedback. "
                "Output ONLY the final expanded/optimized music style prompt string. Do not include any explanations, "
                "introductory words, or quotes."
            )
            
        history_str = ""
        for h in history:
            role_label = "User" if h["role"] == "user" else "AI"
            history_str += f"{role_label}: {h['content']}\n"
            
        rewrite_input = (
            f"Conversation History:\n{history_str}\n"
            f"New feedback or request: {current_prompt}\n"
            f"Generate the combined final detailed {media_type} prompt:"
        )
        
        try:
            text_result = ""
            thinking_result = ""
            
            text_result, thinking_result = call_chat_api(provider, text_model, [], rewrite_input, [], system_prompt)
                
            refined_prompt = text_result.strip()
            if refined_prompt:
                # Strip wrapping quotes
                if refined_prompt.startswith('"') and refined_prompt.endswith('"'):
                    refined_prompt = refined_prompt[1:-1]
                if refined_prompt.startswith("'") and refined_prompt.endswith("'"):
                    refined_prompt = refined_prompt[1:-1]
                print(f"[{media_type} context understanding] Prompt refined from '{current_prompt}' to: '{refined_prompt}'")
                return refined_prompt
        except Exception as e:
            print(f"Error refining {media_type} prompt with history: {e}")
            
        return current_prompt

    def thread_generate_image(self, prompt, model, aspect_ratio, n, prompt_optimizer, ref_path):
        history = self._extract_history_list()
        refined_prompt = self._rewrite_prompt_with_history(history, prompt, media_type="image")
        
        provider = self.provider_combo.get()
        subject_reference = None
        try:
            result = call_image_api(
                provider=provider,
                prompt=refined_prompt,
                model=model,
                aspect_ratio=aspect_ratio,
                n=n,
                prompt_optimizer=prompt_optimizer,
                subject_reference=subject_reference
            )
            
            images_list = []
            if result:
                if "images" in result:
                    raw_images = result["images"]
                elif "data" in result:
                    raw_images = result["data"]
                else:
                    raw_images = []
                
                # Standardize raw_images to a list of dicts with {"url": ...}
                if isinstance(raw_images, list):
                    for img in raw_images:
                        if isinstance(img, dict):
                            url = img.get("url") or img.get("image_url") or img.get("image_file")
                            if url:
                                images_list.append({"url": url})
                        elif isinstance(img, str):
                            images_list.append({"url": img})
                elif isinstance(raw_images, dict):
                    for k in ["image_urls", "images", "urls"]:
                        if k in raw_images and isinstance(raw_images[k], list):
                            for img in raw_images[k]:
                                if isinstance(img, str):
                                    images_list.append({"url": img})
                                elif isinstance(img, dict):
                                    url = img.get("url") or img.get("image_url") or img.get("image_file")
                                    if url:
                                        images_list.append({"url": url})
                                        
            if images_list:
                self.after(0, self.handle_image_success, prompt, aspect_ratio, n, images_list)
            else:
                err_msg = "API 未返回有效图片数据或解析失败"
                if result and "base_resp" in result:
                    err_msg = result["base_resp"].get("status_msg", err_msg)
                self.after(0, self.handle_image_error, err_msg)
                
        except Exception as e:
            self.after(0, self.handle_image_error, str(e))

    def handle_image_success(self, prompt, aspect_ratio, n, images_list):
        self.set_controls_state("normal")
        self.update_status("图片生成成功！")
        
        # Generate short title (≤10 chars) for filename use
        short_title = self._generate_short_title(prompt, max_len=10)
        
        # Auto-save images to cache with MM-DD-HHMMSS-short_title.ext naming
        for i, img_obj in enumerate(images_list):
            img_url = img_obj.get("url", "")
            if img_url:
                cache_name = f"{short_title}_{i+1}" if len(images_list) > 1 else short_title
                cache_path = self._download_to_cache(img_url, custom_name=cache_name)
                if cache_path:
                    img_obj["cache_path"] = cache_path
        
        # Replace the loading message in current messages
        for idx in range(len(self.current_messages) - 1, -1, -1):
            if self.current_messages[idx].get("type") == "image_loading":
                self.current_messages[idx] = {
                    "role": "assistant",
                    "type": "image",
                    "prompt": prompt,
                    "short_title": short_title,
                    "aspect_ratio": aspect_ratio,
                    "n": n,
                    "images": images_list,
                    "timestamp": datetime.now().isoformat()
                }
                msg_idx = idx
                break
        else:
            self.current_messages.append({
                "role": "assistant",
                "type": "image",
                "prompt": prompt,
                "short_title": short_title,
                "aspect_ratio": aspect_ratio,
                "n": n,
                "images": images_list,
                "timestamp": datetime.now().isoformat()
            })
            msg_idx = len(self.current_messages) - 1
        
        # Start background AI call to generate better short title
        t = threading.Thread(target=self._async_ai_short_title, args=(prompt, msg_idx))
        t.daemon = True
        t.start()
            
        self._register_and_rename_session("image-01")
            
        self.refresh_chat_display()

    def handle_image_error(self, err_msg):
        self.set_controls_state("normal")
        self.update_status(f"图片生成失败: {err_msg}")
        
        for idx in range(len(self.current_messages) - 1, -1, -1):
            if self.current_messages[idx].get("type") == "image_loading":
                self.current_messages[idx] = {
                    "role": "assistant",
                    "type": "error",
                    "content": f"图片生成失败: {err_msg}",
                    "timestamp": datetime.now().isoformat()
                }
                break
        else:
            self.current_messages.append({
                "role": "assistant",
                "type": "error",
                "content": f"图片生成失败: {err_msg}",
                "timestamp": datetime.now().isoformat()
            })
            
        self.save_session_by_id(self.current_session_id)
        self.refresh_chat_display()

    # ─────────────────────────────────────────────
    #  AI Music Generation
    # ─────────────────────────────────────────────
    def generate_music(self):
        if self._is_processing:
            return

        prompt = self.input_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("警告", "请先在输入框中输入音乐风格提示词！")
            return
            
        lyrics = self.custom_lyrics
        if not lyrics:
            lyrics = None
            
        # Fixed model parameters as requested
        model = MUSIC_MODEL
        sample_rate = 44100
        
        # Clear fields
        self.input_text.delete("1.0", tk.END)
        self.custom_lyrics = ""
        self.btn_edit_lyrics.config(text="添加歌词")
        
        self._ensure_current_session()
        
        # Log the user's action
        user_msg = {
            "role": "user",
            "content": f"生成音乐: \"{prompt}\"" + (f" (自定义歌词: {lyrics[:20]}...)" if lyrics else " (歌词自动生成)"),
            "timestamp": datetime.now().isoformat()
        }
        self.current_messages.append(user_msg)
        
        # Insert temporary music_loading message
        loading_msg = {
            "role": "assistant",
            "type": "music_loading",
            "prompt": prompt,
            "lyrics": lyrics,
            "timestamp": datetime.now().isoformat()
        }
        self.current_messages.append(loading_msg)
        self.refresh_chat_display()
        
        # Save session immediately with user's request as title
        self._save_session_on_user_request(f"生成音乐: {prompt}", model)
        
        self.set_controls_state("disabled")
        self.update_status("正在生成音乐，请稍候...")
        
        t = threading.Thread(target=self.thread_generate_music, args=(prompt, lyrics, model, sample_rate))
        t.daemon = True
        t.start()

    def thread_generate_music(self, prompt, lyrics, model, sample_rate):
        history = self._extract_history_list()
        refined_prompt = self._rewrite_prompt_with_history(history, prompt, media_type="music")
        try:
            if not lyrics:
                self.after(0, lambda: self.update_status("正在智能创作歌词..."))
                try:
                    gen_prompt = f"请根据以下音乐风格或主题提示词，创作一首适合用于音乐生成的简短中文歌词。注意：只需要直接输出歌词文本，绝对不要带有任何前言、引言、标题、副标题、[主歌/副歌]等段落标记、括号说明或后记。格式为每句一行，控制在10-15行。提示词：{prompt}"
                    lyric_gen, _ = call_chat_api(
                        provider=SHORT_TITLE_PROVIDER,
                        model=SHORT_TITLE_MODEL,
                        history=[],
                        prompt=gen_prompt,
                        b64_images=[],
                        system_prompt="你是一个简洁的摘要工具，只输出10字以内的核心概括，不输出任何其他文字。"
                    )
                    if lyric_gen and lyric_gen.strip():
                        lyrics = lyric_gen.strip()
                    else:
                        lyrics = DEFAULT_LYRICS
                except Exception as ex:
                    print(f"Auto-generate lyrics error: {ex}")
                    lyrics = DEFAULT_LYRICS

            self.after(0, lambda: self.update_status("正在生成音乐，请稍候..."))
            provider = self.provider_combo.get()
            result = call_music_api(
                provider=provider,
                prompt=refined_prompt,
                lyrics=lyrics,
                model=model,
                sample_rate=sample_rate
            )
            
            base_resp = result.get("base_resp", {}) if result else {}
            status_code = base_resp.get("status_code", -1)
            
            if result and status_code == 0 and result.get("data") is not None:
                data_obj = result.get("data") or {}
                audio_url = data_obj.get("audio", "")
                status = data_obj.get("status", 2)
                returned_lyrics = data_obj.get("lyrics") or lyrics
                
                self.after(0, self.handle_music_success, prompt, returned_lyrics, audio_url, status)
            else:
                err_msg = "API 返回结果为空或解析失败"
                if base_resp and "status_msg" in base_resp:
                    err_msg = f"{base_resp.get('status_msg')} (Code: {status_code})"
                self.after(0, self.handle_music_error, err_msg)
        except Exception as e:
            self.after(0, self.handle_music_error, str(e))

    def handle_music_success(self, prompt, lyrics, audio_url, status):
        self.set_controls_state("normal")
        self.update_status("音乐生成成功！")
        
        # Generate short title (≤10 chars) for filename use
        short_title = self._generate_short_title(prompt, max_len=10)
        
        # Auto-save music to cache with MM-DD-HHMMSS-short_title.ext naming
        music_cache_path = None
        if audio_url:
            music_cache_path = self._download_to_cache(audio_url, custom_name=short_title, timeout=120)
        
        for idx in range(len(self.current_messages) - 1, -1, -1):
            if self.current_messages[idx].get("type") == "music_loading":
                self.current_messages[idx] = {
                    "role": "assistant",
                    "type": "music",
                    "prompt": prompt,
                    "short_title": short_title,
                    "lyrics": lyrics or "（使用默认歌词）",
                    "audio_url": audio_url,
                    "cache_path": music_cache_path,
                    "status": status,
                    "timestamp": datetime.now().isoformat()
                }
                msg_idx = idx
                break
        else:
            self.current_messages.append({
                "role": "assistant",
                "type": "music",
                "prompt": prompt,
                "short_title": short_title,
                "lyrics": lyrics or "（使用默认歌词）",
                "audio_url": audio_url,
                "cache_path": music_cache_path,
                "status": status,
                "timestamp": datetime.now().isoformat()
            })
            msg_idx = len(self.current_messages) - 1
        
        # Start background AI call to generate better short title
        t = threading.Thread(target=self._async_ai_short_title, args=(prompt, msg_idx))
        t.daemon = True
        t.start()
            
        self._register_and_rename_session("music-2.6")
            
        self.refresh_chat_display()

    def handle_music_error(self, err_msg):
        self.set_controls_state("normal")
        self.update_status(f"音乐生成失败: {err_msg}")
        
        for idx in range(len(self.current_messages) - 1, -1, -1):
            if self.current_messages[idx].get("type") == "music_loading":
                self.current_messages[idx] = {
                    "role": "assistant",
                    "type": "error",
                    "content": f"音乐生成失败: {err_msg}",
                    "timestamp": datetime.now().isoformat()
                }
                break
        else:
            self.current_messages.append({
                "role": "assistant",
                "type": "error",
                "content": f"音乐生成失败: {err_msg}",
                "timestamp": datetime.now().isoformat()
            })
            
        self.save_session_by_id(self.current_session_id)
        self.refresh_chat_display()

    # ─────────────────────────────────────────────
    #  File Saving & Downloads
    # ─────────────────────────────────────────────
    def download_and_save_file(self, url, default_name, file_types_str):
        if default_name.lower().endswith((".png", ".jpg", ".jpeg")):
            filetypes = [("图片文件", "*.png *.jpg *.jpeg"), ("所有文件", "*.*")]
        elif default_name.lower().endswith(".mp3"):
            filetypes = [("音频文件", "*.mp3"), ("所有文件", "*.*")]
        else:
            filetypes = [("所有文件", "*.*")]

        file_path = filedialog.asksaveasfilename(
            title="保存文件",
            initialfile=default_name,
            filetypes=filetypes
        )
        if not file_path:
            return

        def thread_download():
            try:
                self.update_status("正在下载并保存文件...")
                try:
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_DEFAULT)
                except requests.exceptions.SSLError:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=DOWNLOAD_TIMEOUT_DEFAULT, verify=False)
                    
                if r.status_code == 200:
                    with open(file_path, "wb") as f:
                        f.write(r.content)
                    self.after(0, lambda: messagebox.showinfo("保存成功", f"文件已成功保存到:\n{file_path}"))
                    self.after(0, lambda: self.update_status("文件保存成功！"))
                else:
                    self.after(0, lambda: messagebox.showerror("保存失败", f"下载失败: HTTP {r.status_code}"))
                    self.after(0, lambda: self.update_status(f"文件下载失败: HTTP {r.status_code}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("保存失败", f"下载出错: {e}"))
                self.after(0, lambda: self.update_status(f"文件下载出错: {e}"))

        t = threading.Thread(target=thread_download)
        t.daemon = True
        t.start()

    def _on_close(self):
        """Save current session then destroy window."""
        if self.current_session_id and self.current_messages:
            has_assistant = any(m.get("role") == "assistant" for m in self.current_messages)
            if has_assistant:
                self.save_session_by_id(self.current_session_id)
        self.destroy()


if __name__ == "__main__":
    app = ChatLLM_GUI()
    app.mainloop()