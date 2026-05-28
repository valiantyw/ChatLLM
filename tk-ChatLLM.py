# -*- coding: utf-8 -*-
#
# ChatLLM - Chat LLM application with tkinter GUI mode
#

import os, sys, json, uuid, base64, threading, mimetypes
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import requests, dotenv

# Load environment variables
dotenv.load_dotenv(dotenv.find_dotenv())

# Supported file extensions for different types
TXT_EXTS   = {"txt", "md", "py", "csv", "json", "xml", "yaml", "yml"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
PDF_EXTS   = {"pdf"}
VOICE_EXTS = {"mp3", "wav", "ogg", "flac", "aac", "m4a"}
VIDEO_EXTS = {"mp4", "avi", "mkv", "mov", "flv", "wmv"}

# Conversations storage path
CONV_DIR = "conversations"
os.makedirs(CONV_DIR, exist_ok=True)

# Provider & Model configuration options
PROVIDERS = {
    "MiniMax (Native)": ["MiniMax-M2.7", "MiniMax-M2.5"],
    "MiniMax (OpenAI)": ["MiniMax-M2.7", "MiniMax-M2.5"],
    "MiniMax (Anthropic)": ["MiniMax-M2.7", "MiniMax-M2.5"],
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
        def _close():    self.destroy()
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
        
        # Right side panel (Chat main area)
        self.chat_frame = ttk.Frame(self.main_paned)
        self.main_paned.add(self.chat_frame)
        
        # Status bar at the very bottom of the right panel
        self.status_bar = ttk.Label(self.chat_frame, text="准备就绪", relief="flat", anchor="w", padding=(6, 4))
        self.status_bar.pack(side="bottom", fill="x")
        
        # Chat frame header for toolbar (only session title now)
        chat_header = ttk.Frame(self.chat_frame, padding=(5, 5))
        chat_header.pack(side="top", fill="x")
        
        self.lbl_session_title = ttk.Label(chat_header, text="", font=("Microsoft YaHei", 10, "bold"), foreground="#1e293b")
        self.lbl_session_title.pack(side="left", padx=10)
        
        # Split chat_frame vertically using vertical PanedWindow
        chat_paned = ttk.PanedWindow(self.chat_frame, orient="vertical")
        chat_paned.pack(side="top", fill="both", expand=True, padx=5, pady=5)
        
        # 5. Upper Right part: Chat display content
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
            background="#e0f2fe",      # Blue 100 (beautiful soft blue bubble background)
            font=("Microsoft YaHei", 10),
            lmargin1=180,              # Push left margin inwards to make it a right-sided bubble
            lmargin2=180,
            rmargin=15,                # Keep right margin small to sit on the right
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
            background="#f1f5f9",      # Slate 100 (beautiful soft grey bubble background)
            font=("Microsoft YaHei", 10),
            lmargin1=15,               # Keep left margin small to sit on the left
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
            background="#fef3c7",      # Amber 100 (system notice bubble)
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
            background="#fafafa",      # Zinc 50 (beautiful off-white bubble for thoughts)
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
        
        # Keep selection highlight visible above message background tags
        self.chat_display.tag_raise(tk.SEL)
        
        # 4. Lower Right part: User input panel (status bar directly below this frame)
        input_container = ttk.Frame(chat_paned)
        chat_paned.add(input_container, weight=1)
        
        # Attached files bar
        attachments_bar = ttk.Frame(input_container)
        attachments_bar.pack(fill="x", pady=(0, 2))
        
        self.btn_add_file = ttk.Button(attachments_bar, text="添加文件", command=self.add_file)
        self.btn_add_file.pack(side="left", padx=5)
        
        self.lbl_attachments = ttk.Label(attachments_bar, text="未选择附件", foreground="gray", font=("Microsoft YaHei", 9))
        self.lbl_attachments.pack(side="left", fill="x", expand=True, padx=5)
        
        self.btn_clear_attachments = ttk.Button(attachments_bar, text="清除附件", command=self.clear_attachments, state="disabled")
        self.btn_clear_attachments.pack(side="right", padx=5)
        
        # Input Text Area
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

    # ─────────────────────────────────────────────
    #  Model Dropdown Synchronization
    # ─────────────────────────────────────────────

    def _fix_taskbar(self):
        """Make the frameless window appear in the Windows taskbar."""
        try:
            import ctypes
            hwnd  = ctypes.windll.user32.GetParent(self.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)   # GWL_EXSTYLE
            style = (style & ~0x00000080) | 0x00040000                # ~TOOLWINDOW | APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
            ctypes.windll.user32.ShowWindow(hwnd, 5)                  # SW_SHOW
        except Exception:
            pass

    def _copy_selection(self, _event=None):
        try:
            selected = self.chat_display.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(selected)
            self.update_status("选中文本已复制到剪贴板。")
        except tk.TclError:
            pass
        return "break"

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

    # ─────────────────────────────────────────────
    #  Conversation Persistence & Indexing
    # ─────────────────────────────────────────────
    def load_all_sessions(self):
        self.sessions = []
        index_path = os.path.join(CONV_DIR, "index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    self.sessions = json.load(f)
            except Exception as e:
                print(f"Error loading index: {e}")
                self.sessions = []
                
        self.history_listbox.delete(0, tk.END)
        for s in self.sessions:
            self.history_listbox.insert(tk.END, s.get("title", "未命名会话"))
            
        if self.sessions:
            # Select the first session by default
            self.history_listbox.selection_set(0)
            self.current_session_id = self.sessions[0]["id"]
            self.load_session_by_id(self.current_session_id)
        else:
            self.new_session()

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
        title = "未命名会话"
        for s in self.sessions:
            if s["id"] == session_id:
                title = s.get("title", "未命名会话")
                break
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
            "messages": self.current_messages
        }
        
        # Save individual dialogue records
        filepath = os.path.join(CONV_DIR, f"{session_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(session_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving session data {session_id}: {e}")
            
        # Update metadata in index.json
        for s in self.sessions:
            if s["id"] == session_id:
                s["provider"] = provider
                s["model"] = model
                s["system_prompt"] = system_prompt
                s["updated_at"] = datetime.now().isoformat()
                break
        self.save_index()

    def save_index(self):
        index_path = os.path.join(CONV_DIR, "index.json")
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(self.sessions, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error writing index: {e}")

    def refresh_listbox_titles(self):
        selection = self.history_listbox.curselection()
        selected_idx = selection[0] if selection else None
        
        self.history_listbox.delete(0, tk.END)
        for s in self.sessions:
            self.history_listbox.insert(tk.END, s.get("title", "未命名会话"))
            
        if selected_idx is not None and selected_idx < len(self.sessions):
            self.history_listbox.selection_set(selected_idx)

    # ─────────────────────────────────────────────
    #  Sidebar Actions (New & Delete Chat)
    # ─────────────────────────────────────────────
    def new_session(self):
        if self.current_session_id:
            self.save_session_by_id(self.current_session_id)
            
        new_id = str(uuid.uuid4())
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"新会话 - {now_str}"
        
        new_meta = {
            "id": new_id,
            "title": title,
            "provider": "MiniMax (Native)",
            "model": "MiniMax-M2.7",
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        self.sessions.insert(0, new_meta)
        self.save_index()
        
        self.refresh_listbox_titles()
        self.history_listbox.selection_set(0)
        self.load_session_by_id(new_id)
        self.update_status("新建会话成功。")

    def delete_session(self):
        selection = self.history_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的会话。")
            return
            
        idx = selection[0]
        target_session = self.sessions[idx]
        session_id = target_session["id"]
        
        if not messagebox.askyesno("删除会话", f"确定删除会话 「{target_session.get('title')}」 吗？此操作不可恢复。"):
            return
            
        self.sessions.pop(idx)
        self.save_index()
        
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
            self.load_session_by_id(self.sessions[0]["id"])
        else:
            self.new_session()
            
        self.update_status("会话已成功删除。")

    def on_session_select(self, event):
        selection = self.history_listbox.curselection()
        if not selection:
            return
            
        idx = selection[0]
        selected_id = self.sessions[idx]["id"]
        
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
                name, ext = os.path.splitext(names_str)
                names_str = name[:70] + "..." + name[-3:] + ext
            self.lbl_attachments.config(text=f"{names_str}  ({len(self.attached_files)}个)", foreground="#722ed1")
            self.btn_clear_attachments.config(state="normal")
    # ─────────────────────────────────────────────
    #  Dialogue Area Render
    # ─────────────────────────────────────────────
    def refresh_chat_display(self):
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        
        for msg in self.current_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            thinking = msg.get("thinking", "")
            
            if role == "user":
                self.chat_display.insert(tk.END, "用户\n", "user")
                self.chat_display.insert(tk.END, content + "\n", "user_body")
            elif role == "assistant":
                model_name = msg.get("model") or self.model_combo.get() or "助手"
                if thinking:
                    self.chat_display.insert(tk.END, "思考过程\n", "thinking_title")
                    self.chat_display.insert(tk.END, thinking + "\n", "thinking")
                self.chat_display.insert(tk.END, f"{model_name}\n", "assistant")
                self.chat_display.insert(tk.END, content + "\n", "assistant_body")
            elif role == "system":
                self.chat_display.insert(tk.END, "系统提示\n", "system")
                self.chat_display.insert(tk.END, content + "\n", "system_body")
                
        self.chat_display.config(state="disabled")
        self.chat_display.see(tk.END)

    # ─────────────────────────────────────────────
    #  Input Hotkeys
    # ─────────────────────────────────────────────
    def on_enter_pressed(self, event):
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
        self.btn_new_chat.config(state=state)
        self.btn_delete_chat.config(state=state)
        self.provider_combo.config(state="readonly" if state == "normal" else "disabled")
        self.model_combo.config(state="readonly" if state == "normal" else "disabled")
        self.system_text.config(state=state)
        self.history_listbox.config(state=state)

    def update_status(self, text):
        self.status_bar.config(text=f" {text}")

    # ─────────────────────────────────────────────
    #  SendMessage Execution Flow
    # ─────────────────────────────────────────────
    def send_message(self):
        user_text = self.input_text.get("1.0", tk.END).strip()
        
        if not user_text and not self.attached_files:
            return
            
        if not self.current_session_id:
            self.new_session()
            
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
        
        # Extract previous messages for conversation history
        api_history = []
        for msg in self.current_messages[:-1]:
            api_history.append({"role": msg["role"], "content": msg["content"]})
            
        success = False
        text_result = ""
        thinking_result = ""
        error_msg = ""
        
        try:
            if provider == "MiniMax (Native)":
                text_result, thinking_result = self.call_minimax_native(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "MiniMax (OpenAI)":
                text_result, thinking_result = self.call_minimax_openai(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "MiniMax (Anthropic)":
                text_result, thinking_result = self.call_minimax_anthropic(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "OpenAI":
                text_result, thinking_result = self.call_openai(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "Anthropic":
                text_result, thinking_result = self.call_anthropic(model, api_history, prompt, b64_images, system_prompt)
                success = True
            elif provider == "Google Gemini":
                text_result, thinking_result = self.call_gemini(model, api_history, prompt, b64_images, system_prompt)
                success = True
            else:
                raise ValueError(f"未识别的API提供商: {provider}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            success = False
            error_msg = str(e)
            
        if success:
            self.after(0, self.handle_api_response, text_result, thinking_result)
        else:
            self.after(0, self.handle_api_error, error_msg)

    # ─────────────────────────────────────────────
    #  LLM API Clients Dispatch
    # ─────────────────────────────────────────────
    def call_minimax_native(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("MINIMAX_API_KEY")
        base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
        if not api_key:
            raise ValueError("未在环境变量中设置 MINIMAX_API_KEY")
            
        url = f"{base_url}/text/chatcompletion_v2"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        for msg in history:
            messages.append({
                "role": msg["role"],
                "content": [{"type": "text", "text": msg["content"]}]
            })
            
        user_content = [{"type": "text", "text": prompt}]
        for b64, mime in b64_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
            
        messages.append({
            "role": "user",
            "content": user_content
        })
        
        payload = {
            "model": model,
            "messages": messages
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        try:
            reply = data["choices"][0]["message"]["content"]
            return reply, ""
        except (KeyError, IndexError):
            raise ValueError(f"解析Native API返回的数据结构出错: {json.dumps(data, ensure_ascii=False)}")

    def call_minimax_openai(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("MINIMAX_API_KEY")
        base_url = os.getenv("MINIMAX_OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
        if not api_key:
            raise ValueError("未在环境变量中设置 MINIMAX_API_KEY")
            
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        user_content = [{"type": "text", "text": prompt}]
        for b64, mime in b64_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
            
        content_payload = user_content if b64_images else prompt
        messages.append({"role": "user", "content": content_payload})
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body={"reasoning_split": True}
        )
        
        reply = response.choices[0].message.content or ""
        thinking = ""
        
        msg_obj = response.choices[0].message
        if hasattr(msg_obj, "reasoning_details") and msg_obj.reasoning_details:
            try:
                if isinstance(msg_obj.reasoning_details, list) and len(msg_obj.reasoning_details) > 0:
                    detail = msg_obj.reasoning_details[0]
                    if isinstance(detail, dict) and 'text' in detail:
                        thinking = detail['text']
                    elif hasattr(detail, 'text'):
                        thinking = detail.text
                    elif isinstance(detail, str):
                        thinking = detail
            except Exception:
                pass
                
        return reply, thinking
    def call_minimax_anthropic(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("MINIMAX_API_KEY")
        base_url = os.getenv("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        if not api_key:
            raise ValueError("未在环境变量中设置 MINIMAX_API_KEY")
            
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key, base_url=base_url)
        
        messages = []
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        user_content = [{"type": "text", "text": prompt}]
        for b64, mime in b64_images:
            user_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": b64
                }
            })
            
        content_payload = user_content if b64_images else prompt
        messages.append({"role": "user", "content": content_payload})
        
        kwargs = {
            "model": model,
            "max_tokens": 2048,
            "messages": messages
        }
        if system_prompt:
            kwargs["system"] = system_prompt
            
        response = client.messages.create(**kwargs)
        
        reply = ""
        thinking = ""
        for block in response.content:
            if block.type == "thinking":
                thinking += block.thinking
            elif block.type == "text":
                reply += block.text
                
        return reply, thinking

    def call_openai(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if not api_key:
            raise ValueError("未在环境变量中设置 OPENAI_API_KEY")
            
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
            
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        user_content = [{"type": "text", "text": prompt}]
        for b64, mime in b64_images:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
            
        content_payload = user_content if b64_images else prompt
        messages.append({"role": "user", "content": content_payload})
        
        response = client.chat.completions.create(
            model=model,
            messages=messages
        )
        reply = response.choices[0].message.content or ""
        return reply, ""

    def call_anthropic(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        if not api_key:
            raise ValueError("未在环境变量中设置 ANTHROPIC_API_KEY")
            
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key, base_url=base_url)
        
        messages = []
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
            
        user_content = [{"type": "text", "text": prompt}]
        for b64, mime in b64_images:
            user_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": b64
                }
            })
            
        content_payload = user_content if b64_images else prompt
        messages.append({"role": "user", "content": content_payload})
        
        kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages
        }
        if system_prompt:
            kwargs["system"] = system_prompt
            
        response = client.messages.create(**kwargs)
        
        reply = ""
        thinking = ""
        for block in response.content:
            if block.type == "text":
                reply += block.text
            elif block.type == "thinking":
                thinking += block.thinking
                
        return reply, thinking

    def call_gemini(self, model, history, prompt, b64_images, system_prompt):
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
        base_url = os.getenv("GOOGLE_GEMINI_BASE_URL")
        if not api_key:
            raise ValueError("未在环境变量中设置 GOOGLE_GEMINI_API_KEY")
            
        from google import genai
        from google.genai import types
        
        kwargs = {"api_key": api_key}
        if base_url:
            from google.genai.types import HttpOptions
            kwargs["http_options"] = HttpOptions(base_url=base_url)
            
        client = genai.Client(**kwargs)
        
        contents = []
        for msg in history:
            role_mapping = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role_mapping,
                parts=[types.Part.from_text(text=msg["content"])]
            ))
            
        parts = [types.Part.from_text(text=prompt)]
        for b64, mime in b64_images:
            parts.append(types.Part(
                inline_data=types.Blob(
                    mime_type=mime,
                    data=base64.b64decode(b64)
                )
            ))
            
        contents.append(types.Content(role="user", parts=parts))
        
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7
        )
        
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
        return response.text or "", ""

    # ─────────────────────────────────────────────
    #  Callback Handler after thread finished
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
        
        # Dynamically set session title if first round
        if len(self.current_messages) <= 2:
            first_user_msg = self.current_messages[0]["content"].split("\n")[0]
            if len(first_user_msg) > 15:
                first_user_msg = first_user_msg[:12] + "..."
            for s in self.sessions:
                if s["id"] == self.current_session_id:
                    s["title"] = first_user_msg
                    break
            self.save_index()
            self.refresh_listbox_titles()
            
        if hasattr(self, "lbl_session_title") and len(self.current_messages) > 0:
            first_user_msg = self.current_messages[0]["content"].split("\n")[0]
            if len(first_user_msg) > 15:
                first_user_msg = first_user_msg[:12] + "..."
            self.lbl_session_title.config(text=first_user_msg)

        self.save_session_by_id(self.current_session_id)
        self.refresh_chat_display()
        
        self.set_controls_state("normal")
        self.update_status("准备就绪")

    def handle_api_error(self, error_message):
        self.chat_display.config(state="normal")
        self.chat_display.insert(tk.END, "错误信息:\n", "error")
        self.chat_display.insert(tk.END, f"调用大模型API失败: {error_message}\n\n")
        self.chat_display.config(state="disabled")
        self.chat_display.see(tk.END)
        
        self.set_controls_state("normal")
        self.update_status(f" 错误: {error_message}")


if __name__ == "__main__":
    app = ChatLLM_GUI()
    app.mainloop()