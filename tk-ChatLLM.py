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
    call_minimax_native,
    call_minimax_openai,
    call_minimax_anthropic,
    image_MiniMax,
    music_MiniMax,
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

# Provider & Model configuration options
PROVIDERS = {
    "MiniMax (Native)": ["MiniMax-M2.7", "music-2.6", "image-01"],
    "MiniMax (OpenAI)": ["MiniMax-M2.7", "music-2.6", "image-01"],
    "MiniMax (Anthropic)": ["MiniMax-M2.7", "music-2.6", "image-01"],
}

# Default System Prompt
DEFAULT_SYSTEM_PROMPT = "你是一个智能助手，请始终用中文回复。"

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
        
        # Configure styles - Use native theme of platform to avoid the retro 'clam' style
        self.style = ttk.Style()
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        elif "xpnative" in self.style.theme_names():
            self.style.theme_use("xpnative")
            
        # Customize standard widgets with clean, modern styles
        self.style.configure("TFrame", background="#f8fafc")
        self.style.configure("TLabelframe", background="#fafafa", borderwidth=1, relief="solid")
        self.style.configure("TLabelframe.Label", background="#fafafa", font=("Microsoft YaHei", 9, "bold"), foreground="#475569")
        self.style.configure("TLabel", background="#f8fafc", font=("Microsoft YaHei", 10), foreground="#1e293b")
        self.style.configure("TButton", font=("Microsoft YaHei", 9), relief="flat")
        self.style.configure("TCombobox", font=("Microsoft YaHei", 10))
        
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
        TB_BG       = "#e2e8f0"   # Clean slate gray titlebar background
        TB_FG       = "#1e293b"   # Slate 800 title text
        TB_HOVER    = "#cbd5e1"   # Hover background
        CLOSE_HOVER = "#ef4444"   # Close button hover color (red)

        tb = tk.Frame(self, bg=TB_BG, height=36)
        tb.pack(fill=tk.X, side=tk.TOP)
        tb.pack_propagate(False)

        # Left: app title
        title_lbl = tk.Label(tb, text=" 💬 ChatLLM - 智能助手", bg=TB_BG, fg=TB_FG,
                             font=("Microsoft YaHei", 10, "bold"), padx=5)
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
                           font=("Segoe UI", 10), cursor="arrow",
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
            highlightcolor="#3b82f6",
            selectbackground="#3b82f6",
            selectforeground="#ffffff",
            font=("Microsoft YaHei", 10),
            bg="#fbfbfb",
            fg="#1e293b",
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
        self.provider_combo.set("MiniMax (Native)")
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
            highlightcolor="#3b82f6",
            font=("Microsoft YaHei", 9),
            bg="#fbfbfb",
            fg="#475569",
            selectbackground="#cbd5e1",
            selectforeground="#1e293b",
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
        
        self.lbl_session_title = ttk.Label(chat_header, text="", font=("Microsoft YaHei", 10, "bold"), foreground="#1e293b")
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
            font=("Microsoft YaHei", 10),
            selectbackground="#cbd5e1",
            selectforeground="#1e293b",
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
            foreground="#64748b",      # Slate 500
            font=("Microsoft YaHei", 9, "italic"),
            spacing1=15,
            spacing3=4,
            rmargin=15
        )
        self.chat_display.tag_configure(
            "user_body",
            justify="right",
            foreground="#0f172a",      # Slate 900
            background="#e0f2fe",      # Blue 100
            font=("Microsoft YaHei", 10),
            lmargin1=180,              # Push left margin inwards to make it a right-sided bubble
            lmargin2=180,
            rmargin=15,
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "assistant",
            justify="left",
            foreground="#64748b",      # Slate 500
            font=("Microsoft YaHei", 9, "italic"),
            spacing1=15,
            spacing3=4,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "assistant_body",
            justify="left",
            foreground="#0f172a",      # Slate 900
            background="#f1f5f9",      # Slate 100
            font=("Microsoft YaHei", 10),
            lmargin1=15,
            lmargin2=15,
            rmargin=180,               # Push right margin inwards to make it a left-sided bubble
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "system",
            justify="center",
            foreground="#d97706",      # Amber 600
            font=("Microsoft YaHei", 9, "bold"),
            spacing1=15,
            spacing3=4
        )
        self.chat_display.tag_configure(
            "system_body",
            justify="center",
            foreground="#4b5563",      # Grey 600
            background="#fef3c7",      # Amber 100
            font=("Microsoft YaHei", 9),
            lmargin1=120,
            lmargin2=120,
            rmargin=120,
            spacing1=4,
            spacing2=4,
            spacing3=15
        )
        self.chat_display.tag_configure(
            "thinking_title",
            justify="left",
            foreground="#71717a",      # Zinc 500
            font=("Microsoft YaHei", 9, "bold"),
            spacing1=15,
            spacing3=4,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "thinking",
            justify="left",
            foreground="#52525b",      # Zinc 600
            background="#fafafa",      # Zinc 50
            font=("Microsoft YaHei", 9, "italic"),
            lmargin1=15,
            lmargin2=15,
            rmargin=180,
            spacing1=2,
            spacing2=4,
            spacing3=15,
        )
        self.chat_display.tag_configure(
            "error",
            justify="left",
            foreground="#ef4444",      # Red 500
            font=("Microsoft YaHei", 10, "bold"),
            spacing1=15,
            lmargin1=15
        )
        self.chat_display.tag_configure(
            "filename",
            foreground="#7c3aed",      # Violet 600
            font=("Microsoft YaHei", 9, "underline"),
            justify="left"
        )
        self.chat_display.tag_configure(
            "info",
            foreground="#64748b",
            font=("Microsoft YaHei", 9),
            lmargin1=15,
            lmargin2=15,
            spacing1=2,
            spacing3=2
        )
        self.chat_display.tag_configure(
            "prompt",
            foreground="#0f172a",
            font=("Microsoft YaHei", 9, "bold"),
            lmargin1=15,
            lmargin2=15,
            spacing1=2,
            spacing3=2
        )
        self.chat_display.tag_configure(
            "lyrics",
            foreground="#475569",
            font=("Microsoft YaHei", 9, "italic"),
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
        
        self.lbl_aspect = ttk.Label(params_bar, text="图片比例:", font=("Microsoft YaHei", 9))
        # self.lbl_aspect.pack(side="left", padx=(5, 2))
        self.img_aspect_combo = ttk.Combobox(params_bar, state="readonly", values=["1:1", "16:9", "9:16", "4:3", "3:4", "2:3", "21:9"], width=8)
        self.img_aspect_combo.set("16:9")
        # self.img_aspect_combo.pack(side="left", padx=2)
        
        self.lbl_n = ttk.Label(params_bar, text="张数:", font=("Microsoft YaHei", 9))
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
        
        self.lbl_attachments = ttk.Label(params_bar, text="未选择附件", foreground="gray", font=("Microsoft YaHei", 9))
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
            highlightcolor="#3b82f6",
            font=("Microsoft YaHei", 10),
            bg="#ffffff",
            fg="#0f172a",
            selectbackground="#cbd5e1",
            selectforeground="#1e293b",
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
        based on updated_at / created_at inside the JSON file)."""
        entries = []
        if not os.path.isdir(CONV_DIR):
            return []
        for fname in os.listdir(CONV_DIR):
            if fname.endswith(".json") and fname != "index.json":
                fpath = os.path.join(CONV_DIR, fname)
                sid = fname[:-5]
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    msgs = data.get("messages") or []
                    # Skip incomplete sessions (no assistant reply)
                    if not any(m.get("role") == "assistant" for m in msgs):
                        os.remove(fpath)
                        print(f"Removed incomplete session: {fname}")
                        continue
                    # Use updated_at if available, fall back to mtime
                    ts = data.get("updated_at") or data.get("created_at") or ""
                    entries.append((sid, ts))
                except Exception:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
                    continue
        # Sort by timestamp descending (newest first).
        # Timestamps are ISO-8601 strings which sort lexicographically.
        entries.sort(key=lambda x: x[1], reverse=True)
        return [sid for sid, _ in entries]

    def load_all_sessions(self):
        self._loading_flag = True
        self.sessions = self._rescan_sessions()
                
        self.history_listbox.delete(0, tk.END)
        for sid in self.sessions:
            self.history_listbox.insert(tk.END, self._session_title_from_id(sid))
            
        if self.sessions:
            self.history_listbox.selection_set(0)
            self.current_session_id = self.sessions[0]
            self.load_session_by_id(self.current_session_id)
        else:
            self.new_session()
            
        self._loading_flag = False

    def load_session_by_id(self, session_id):
        self.current_session_id = session_id
        filepath = os.path.join(CONV_DIR, f"{session_id}.json")
        
        messages = []
        provider = "MiniMax (Native)"
        model = "MiniMax-M2.7"
        system_prompt = DEFAULT_SYSTEM_PROMPT
        
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    messages = data.get("messages", [])
                    provider = data.get("provider", "MiniMax (Native)")
                    model = data.get("model", "MiniMax-M2.7")
                    system_prompt = data.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
            except Exception as e:
                print(f"Error loading session file {session_id}.json: {e}")
                
        self.current_messages = messages
        
        # Restore settings in UI
        if provider in PROVIDERS:
            self.provider_combo.set(provider)
            self.update_model_options(None)
            if model in PROVIDERS[provider]:
                self.model_combo.set(model)
            else:
                self.model_combo.set(PROVIDERS[provider][0])
                
        self.system_text.delete("1.0", tk.END)
        self.system_text.insert("1.0", system_prompt)
        
        # Update session title label
        title = self._session_title_from_id(session_id)
        if hasattr(self, 'lbl_session_title'):
            self.lbl_session_title.config(text=title)
            
        # Render historical chat dialogue
        self.refresh_chat_display()

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
        if self.current_session_id:
            # Only save old session if it has an AI response
            has_assistant = any(m.get("role") == "assistant" for m in self.current_messages)
            if has_assistant:
                self.save_session_by_id(self.current_session_id)
            
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
            
        if self.current_session_id:
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
            self.lbl_attachments.config(text=f"{names_str}  ({len(self.attached_files)}个)", foreground="#722ed1")
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
                            
                            scroll_step = 220
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
                                    img_label = self._make_thumbnail_label(card, cached_path, 130)
                                else:
                                    cache_dir = os.path.join(CONV_DIR, "cache")
                                    os.makedirs(cache_dir, exist_ok=True)
                                    cache_name = hashlib.md5(img_url.encode('utf-8')).hexdigest() + img_ext
                                    cache_path_fb = os.path.join(cache_dir, cache_name)
                                    if os.path.exists(cache_path_fb):
                                        img_label = self._make_thumbnail_label(card, cache_path_fb, 130)
                                
                                if img_label:
                                    img_label.pack(pady=(0, 4))
                                    self._rendered_images.append(img_label._tk_img_ref)
                                else:
                                    placeholder_lbl = tk.Label(card, text=f"图片 {i+1}\n[加载中…]",
                                                               bg="#ffffff", fg="#94a3b8",
                                                               font=("Microsoft YaHei", 9),
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
                                tk.Button(btn_card, text="🌐 打开", font=("Microsoft YaHei", 8),
                                          command=lambda u=img_url: webbrowser.open(u),
                                          cursor="hand2", relief="flat", bg="#f1f5f9",
                                          activebackground="#e2e8f0").pack(side="left", padx=2)
                                tk.Button(btn_card, text="💾 保存", font=("Microsoft YaHei", 8),
                                          command=lambda u=img_url, dn=img_default_name:
                                              self.download_and_save_file(u, dn, "图片文件 (*.png;*.jpg;*.jpeg)"),
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
                                btn_save = ttk.Button(btn_frame, text="保存图片", command=lambda url=img_url, dn=img_default_name: self.download_and_save_file(url, dn, "图片文件 (*.png;*.jpg;*.jpeg)"))
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
                    self.chat_display.insert(tk.END, f"正在呼叫 MiniMax 音乐接口，提示词: \"{msg.get('prompt')}\"...\n", "assistant_body")
                    
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
        self.chat_display.see(tk.END)

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
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=60)
                except requests.exceptions.SSLError:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=60, verify=False)
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
            img_lbl = self._make_thumbnail_label(card, cache_path, 130)
            if img_lbl:
                placeholder_lbl.pack_forget()
                img_lbl.pack(pady=(0, 4))
        except Exception as e:
            print(f"Error replacing card placeholder: {e}")

    def _force_wide_stretch(self, widget):
        """Force embedded widget to fill the full chat display width."""
        try:
            # Use Tk's native stretch feature which expands to fill the line
            self.chat_display.window_config(widget, stretch=1)
        except tk.TclError:
            pass

    def display_cached_image_in_chat(self, cache_path):
        try:
            if not HAS_PIL:
                return
            pil_img = PILImage.open(cache_path)
            w, h = pil_img.size
            if w > 350:
                h = int(h * (350 / w))
                w = 350
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
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30)
            except requests.exceptions.SSLError:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=30, verify=False)
                
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
            if w > 350:
                h = int(h * (350 / w))
                w = 350
                resample_filter = getattr(getattr(PILImage, "Resampling", None), "LANCZOS", getattr(PILImage, "LANCZOS", 1))
                pil_img = pil_img.resize((w, h), resample_filter)
                
            tk_img = ImageTk.PhotoImage(pil_img)
            self._rendered_images.append(tk_img)
            
            self.chat_display.config(state="normal")
            pos = self.chat_display.search(placeholder, "1.0", tk.END, exact=True)
            if pos:
                self.chat_display.delete(pos, f"{pos}+{len(placeholder)}c")
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
            desc, _ = call_minimax_native(
                model="MiniMax-M2.7",
                history=[],
                prompt=ai_prompt,
                b64_images=[],
                system_prompt="你是一个简洁的摘要工具，只输出10字以内的核心概括，不输出任何其他文字。"
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
        print(f"[DEBUG] _ensure_current_session: current_session_id={self.current_session_id}")
        if not self.current_session_id:
            self.new_session()
            print(f"[DEBUG] _ensure_current_session: created new session, current_session_id={self.current_session_id}")

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
        if model == "music-2.6":
            self.generate_music()
        elif model == "image-01":
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
        if model == "music-2.6":
            self.generate_music()
            return
        if model == "image-01":
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
            if provider == "MiniMax (Native)":
                text_result, thinking_result = call_minimax_native(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "MiniMax (OpenAI)":
                text_result, thinking_result = call_minimax_openai(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "MiniMax (Anthropic)":
                text_result, thinking_result = call_minimax_anthropic(model, api_history, prompt, b64_images, system_prompt)
                success = True
            else:
                raise ValueError(f"未识别的API提供商: {provider}")
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
        
        first_line = user_content.split("\n")[0].strip() if user_content else ""
        if not first_line:
            return
        
        # Compute final file ID (timestamp + slug from first line)
        slug = self._generate_short_title(first_line, max_len=10)
        safe_slug = self._sanitize_filename(slug)
        current_id = self.current_session_id
        if safe_slug and safe_slug != "_":
            ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            final_id = f"{ts}-{safe_slug}"
            # Clean up any old file with bare ID
            old_path = os.path.join(CONV_DIR, f"{current_id}.json")
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass
            self.current_session_id = final_id
        else:
            final_id = current_id
        
        # Update sidebar: add to front of sessions list
        if final_id not in self.sessions:
            self.sessions.insert(0, final_id)
        self.refresh_listbox_titles()
        
        # Sidebar label from slug
        title = self._session_title_from_id(final_id)
        if hasattr(self, "lbl_session_title"):
            self.lbl_session_title.config(text=title)
        
        # Save session data to disk
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
        self.save_session_by_id(self.current_session_id)

    # ─────────────────────────────────────────────
    #  LLM API Clients Dispatch
    # ─────────────────────────────────────────────
    def handle_api_response(self, text, thinking):
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
        
        if model == "music-2.6":
            # Show music specific options
            if hasattr(self, 'btn_edit_lyrics'): self.btn_edit_lyrics.pack(side="left", padx=(15, 2))
                
        elif model == "image-01":
            # Show image specific options
            if hasattr(self, 'lbl_aspect'): self.lbl_aspect.pack(side="left", padx=(5, 2))
            if hasattr(self, 'img_aspect_combo'): self.img_aspect_combo.pack(side="left", padx=2)
            if hasattr(self, 'lbl_n'): self.lbl_n.pack(side="left", padx=(10, 2))
            if hasattr(self, 'img_n_combo'): self.img_n_combo.pack(side="left", padx=2)
            
            # Set default values
            if hasattr(self, 'img_aspect_combo'): self.img_aspect_combo.set("16:9")
            if hasattr(self, 'img_n_combo'): self.img_n_combo.set("1")

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
        
        lbl = ttk.Label(popup, text="请输入自定义歌词（不输入则自动生成）：", font=("Microsoft YaHei", 9, "bold"))
        lbl.pack(anchor="w", padx=10, pady=(10, 5))
        
        text_frame = ttk.Frame(popup)
        text_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        lyric_text = tk.Text(text_frame, wrap="word", font=("Microsoft YaHei", 10), bd=0, highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor="#3b82f6")
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
            
        # Fixed model parameters as requested
        model = "image-01"
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

    def thread_generate_image(self, prompt, model, aspect_ratio, n, prompt_optimizer, ref_path):
        subject_reference = None
        try:
            result = image_MiniMax(
                prompt=prompt,
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
        self.update_status("图片生成成功！正在缓存...")
        
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
        model = "music-2.6"
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
        try:
            if not lyrics:
                self.after(0, lambda: self.update_status("正在智能创作歌词..."))
                try:
                    gen_prompt = f"请根据以下音乐风格或主题提示词，创作一首适合用于音乐生成的简短中文歌词。注意：只需要直接输出歌词文本，绝对不要带有任何前言、引言、标题、副标题、[主歌/副歌]等段落标记、括号说明或后记。格式为每句一行，控制在10-15行。提示词：{prompt}"
                    lyric_gen, _ = call_minimax_native(
                        model="MiniMax-M2.7",
                        history=[],
                        prompt=gen_prompt,
                        b64_images=[],
                        system_prompt="你是一个简洁的摘要工具，只输出10字以内的核心概括，不输出任何其他文字。"
                    )
                    if lyric_gen and lyric_gen.strip():
                        lyrics = lyric_gen.strip()
                    else:
                        lyrics = "美妙的旋律在夜空流淌\n轻风拂过思念的琴弦\n每一个音符都是真挚的向往\n让我们一起歌唱到地久天长"
                except Exception as ex:
                    print(f"Auto-generate lyrics error: {ex}")
                    lyrics = "美妙的旋律在夜空流淌\n轻风拂过思念的琴弦\n每一个音符都是真挚的向往\n让我们一起歌唱到地久天长"

            self.after(0, lambda: self.update_status("正在生成音乐，请稍候..."))
            result = music_MiniMax(
                prompt=prompt,
                lyrics=lyrics,
                model=model,
                sample_rate=sample_rate,
                bitrate=256000,
                audio_format="mp3",
                output_format="url"
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
        self.update_status("音乐生成成功！正在缓存...")
        
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
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=60)
                except requests.exceptions.SSLError:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = requests.get(url, headers=DOWNLOAD_HEADERS, timeout=60, verify=False)
                    
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