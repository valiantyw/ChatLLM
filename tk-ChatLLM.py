#
# ChatLLM - Chat LLM application with tkinter GUI mode
#

# Supported file extensions for different types
TXT_EXTS   = {"txt", "md", "py", "csv", "json", "xml", "yaml", "yml"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
PDF_EXTS   = {"pdf"}
VOICE_EXTS = {"mp3", "wav", "ogg", "flac", "aac", "m4a"}
VIDEO_EXTS = {"mp4", "avi", "mkv", "mov", "flv", "wmv"}


# ─────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────
class ChatLLM_GUI(tk.Tk):
    def __init__(self):


# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = ChatLLM_GUI()
    app.mainloop()
