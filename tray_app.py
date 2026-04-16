import threading
import time
import sys
import ctypes
import tkinter as tk
from tkinter import scrolledtext
import keyboard
from PIL import Image, ImageDraw
import pystray
import clicker


# ===== ログ =====
log_buffer = []
log_lock = threading.Lock()
log_window = None
log_text_widget = None

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with log_lock:
        log_buffer.append(line)
        if len(log_buffer) > 500:
            log_buffer.pop(0)
    if log_text_widget:
        try:
            log_text_widget.after(0, _append_log, line)
        except Exception:
            pass

def _append_log(line: str):
    log_text_widget.config(state="normal")
    log_text_widget.insert(tk.END, line + "\n")
    log_text_widget.see(tk.END)
    log_text_widget.config(state="disabled")

def open_log_window(icon=None, item=None):
    global log_window, log_text_widget

    def _open():
        global log_window, log_text_widget
        if log_window and tk.Toplevel.winfo_exists(log_window):
            log_window.lift()
            return
        root = tk.Tk()
        root.title("AutoClick ログ")
        root.geometry("600x400")
        log_text_widget = scrolledtext.ScrolledText(root, state="disabled", font=("Consolas", 10))
        log_text_widget.pack(fill=tk.BOTH, expand=True)
        # 既存ログを流し込む
        log_text_widget.config(state="normal")
        with log_lock:
            for line in log_buffer:
                log_text_widget.insert(tk.END, line + "\n")
        log_text_widget.see(tk.END)
        log_text_widget.config(state="disabled")
        log_window = root

        def on_close():
            global log_window, log_text_widget
            log_window = None
            log_text_widget = None
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()

    t = threading.Thread(target=_open, daemon=True)
    t.start()

# ===== 設定 =====
# clicker.py の設定を編集してください
# ================

running = False
worker_thread = None


def make_icon(active: bool) -> Image.Image:
    """アクティブ=緑丸, 非アクティブ=赤丸 のアイコンを生成"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (50, 200, 50) if active else (200, 50, 50)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    # 中央に記号
    if active:
        # 再生ボタン風の三角
        draw.polygon([(22, 18), (22, 46), (48, 32)], fill="white")
    else:
        # 停止ボタン風の四角
        draw.rectangle([20, 20, 44, 44], fill="white")
    return img


def auto_click_loop():
    clicker.run(lambda: running, log)


def toggle(icon, item):
    global running, worker_thread
    running = not running
    icon.icon = make_icon(running)
    icon.title = "AutoClick: ON" if running else "AutoClick: OFF"
    if running:
        worker_thread = threading.Thread(target=auto_click_loop, daemon=True)
        worker_thread.start()
        log("AutoClick 開始")
    else:
        log("AutoClick 停止")


tray_icon = None

def on_esc(e):
    global running, tray_icon
    if running and tray_icon is not None:
        toggle(tray_icon, None)

keyboard.on_press_key('esc', on_esc)


def on_quit(icon, item):
    global running
    running = False
    log("終了しました")
    icon.stop()


def main():
    global tray_icon
    icon = pystray.Icon(
        name="AutoClick",
        icon=make_icon(False),
        title="AutoClick: OFF",
        menu=pystray.Menu(
            pystray.MenuItem("ON / OFF", toggle, default=True),
            pystray.MenuItem("ログを開く", open_log_window),
            pystray.MenuItem("終了", on_quit),
        )
    )
    tray_icon = icon
    log("タスクトレイに常駐しました")
    icon.run()


if __name__ == "__main__":
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "AutoClickTrayApp_Mutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0, "AutoClick はすでに起動しています。", "多重起動禁止", 0x10)
        sys.exit(0)
    main()
