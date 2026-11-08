import threading
import time
import sys
import ctypes
import gc
import json
import os
import queue
import traceback

# exe 版では exe のあるフォルダにログを置く
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
ERROR_LOG_PATH = os.path.join(APP_DIR, "autoclick_error.log")
LOG_PATH = os.path.join(APP_DIR, "autoclick.log")  # 操作ログ（不具合の調査用）
LOG_MAX_BYTES = 2 * 1024 * 1024


def show_error(message):
    """pythonw（コンソールなし）で起動していてもエラーが分かるようにする。"""
    print(message, file=sys.stderr)
    try:
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "AutoClick", 0x10)  # MB_ICONERROR
    except Exception:
        pass


try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox, filedialog
    import keyboard
    from PIL import Image, ImageColor, ImageDraw, ImageTk
    import pystray
    import clicker
except ImportError as exc:
    show_error(
        f"必要なライブラリを読み込めませんでした: {exc}\n\n"
        "setup_venv.bat を実行してから、もう一度起動してください。"
    )
    sys.exit(1)


TOGGLE_HOTKEY = "f7"   # 通常の AutoClick ON/OFF（None で無効）
SNOWMAN_HOTKEY = "f8"  # 雪だるま自動（None で無効）
COOKING_HOTKEY = "f9"  # クッキング自動（None で無効）
STOP_HOTKEY = "esc"   # 動作中のみ停止

OVERLAY_ENABLED = True          # 画面の隅に状態を表示する（ゲーム中でも見える）
OVERLAY_POSITION = "top-left"   # top-left / top-right / bottom-left / bottom-right
OVERLAY_MARGIN = 12             # 画面の端からの距離（px）
OVERLAY_ALPHA = 0.85            # 不透明度（0〜1）
OVERLAY_OFF_SECONDS = 2.0       # 停止した時、この秒数だけ「停止」を表示して消す
HELP_HOTKEY = "f1"              # キー設定を画面に表示（None で無効）
HELP_SECONDS = 6.0              # キー設定を表示しておく秒数（もう一度押すと消える）

# ===== UI スレッド =====
# tkinter は1つのスレッドからしか触れないため、ログ・設定ウィンドウは
# すべて専用の UI スレッド上の Toplevel として作る。他スレッドからは
# ui_queue に依頼を積むだけにする。
ui_queue = queue.Queue()
ui_root = None


def run_on_ui(func, *args):
    ui_queue.put((func, args))


def ui_main():
    global ui_root
    ui_root = tk.Tk()
    ui_root.withdraw()

    def poll():
        while True:
            try:
                func, args = ui_queue.get_nowait()
            except queue.Empty:
                break
            if func is None:  # 終了
                ui_root.destroy()
                return
            try:
                func(*args)
            except Exception:
                print(traceback.format_exc(), file=sys.stderr)
        ui_root.after(50, poll)

    ui_root.after(50, poll)
    ui_root.mainloop()

    # Tcl インタプリタはこのスレッドで解放する（別スレッドの GC に任せると
    # "Tcl_AsyncDelete: async handler deleted by the wrong thread" で落ちる）
    global log_window, log_text_widget, settings_window, overlay_window, overlay_canvas
    log_window = log_text_widget = settings_window = ui_root = None
    overlay_window = overlay_canvas = None
    global help_window, help_canvas
    help_window = help_canvas = None
    del poll
    gc.collect()


# ===== ログ =====
log_buffer = []
log_lock = threading.Lock()
log_window = None
log_text_widget = None
settings_window = None

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    _write_log_file(f"[{time.strftime('%Y-%m-%d')} {ts}] {msg}")
    with log_lock:
        log_buffer.append(line)
        if len(log_buffer) > 500:
            log_buffer.pop(0)
    run_on_ui(_append_log, line)

def _write_log_file(line: str):
    try:
        if os.path.isfile(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _append_log(line: str):
    if log_text_widget is None:
        return
    log_text_widget.config(state="normal")
    log_text_widget.insert(tk.END, line + "\n")
    if int(log_text_widget.index("end-1c").split(".")[0]) > 501:
        log_text_widget.delete("1.0", "2.0")
    log_text_widget.see(tk.END)
    log_text_widget.config(state="disabled")

def open_log_window(icon=None, item=None):
    def _open():
        global log_window, log_text_widget
        if log_window is not None and log_window.winfo_exists():
            log_window.deiconify()
            log_window.lift()
            log_window.focus_force()
            return
        root = tk.Toplevel(ui_root)
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

    run_on_ui(_open)


def _read_config_for_ui():
    base = dict(clicker.DEFAULT_CONFIG)
    try:
        with open(clicker.CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                base.update(data)
                return base
    except Exception:
        pass
    return base


def _parse_lines(text: str):
    return [line.strip() for line in text.splitlines() if line.strip()]


def open_settings_window(icon=None, item=None):
    def _open():
        global settings_window
        if settings_window is not None and settings_window.winfo_exists():
            settings_window.deiconify()
            settings_window.lift()
            settings_window.focus_force()
            return

        project_root = os.path.dirname(clicker.CONFIG_PATH)
        config = _read_config_for_ui()
        default_config = dict(clicker.DEFAULT_CONFIG)

        root = tk.Toplevel(ui_root)
        root.title("AutoClick 設定")
        root.geometry("920x940")
        root.minsize(920, 900)
        settings_window = root

        frame = tk.Frame(root, padx=12, pady=12)
        frame.pack(fill=tk.BOTH, expand=True)

        selected_template_paths = []
        available_image_paths = []
        preview_photo = {"image": None}
        preview_size = (280, 220)
        tooltip_state = {"window": None, "index": None}

        def attach_tooltip(widget, text):
            tip_state = {"window": None}

            def hide_tip(_event=None):
                if tip_state["window"] is not None:
                    tip_state["window"].destroy()
                    tip_state["window"] = None

            def show_tip(event):
                hide_tip()
                tip = tk.Toplevel(root)
                tip.wm_overrideredirect(True)
                x = widget.winfo_rootx() + event.x + 12
                y = widget.winfo_rooty() + event.y + 12
                tip.geometry(f"+{x}+{y}")
                tk.Label(
                    tip,
                    text=text,
                    bg="#fff8cc",
                    relief=tk.SOLID,
                    borderwidth=1,
                    justify=tk.LEFT,
                    padx=6,
                    pady=4,
                ).pack()
                tip_state["window"] = tip

            widget.bind("<Enter>", show_tip)
            widget.bind("<Leave>", hide_tip)

        def normalize_template_path(path: str):
            p = os.path.abspath(os.path.normpath(str(path).strip()))
            return p if p else ""

        def resolve_template_entry(entry: str, candidate_paths):
            raw = str(entry).strip()
            if not raw:
                return ""
            if os.path.isabs(raw):
                return normalize_template_path(raw)

            basename = os.path.basename(raw)
            for candidate in candidate_paths:
                if os.path.basename(candidate) == basename:
                    return candidate

            guessed = raw
            if not os.path.isabs(guessed):
                guessed = os.path.join(project_root, guessed)
            return normalize_template_path(guessed)

        initial_candidates = []
        for p in config.get("candidate_templates", []):
            np = normalize_template_path(p)
            if np and np not in initial_candidates:
                initial_candidates.append(np)

        legacy_templates = []
        image_dir_for_init = str(config.get("image_dir", "images") or "images")
        for name in config.get("button_names", []):
            legacy_templates.append(os.path.join(image_dir_for_init, f"{name}.png"))
        for p in config.get("templates", []):
            legacy_templates.append(p)
        for p in legacy_templates:
            np = normalize_template_path(p)
            if np and np not in initial_candidates:
                initial_candidates.append(np)

        for p in config.get("templates", []):
            resolved = resolve_template_entry(p, initial_candidates)
            if resolved and resolved not in selected_template_paths:
                selected_template_paths.append(resolved)

        available_image_paths = list(initial_candidates)

        browser_row = tk.Frame(frame)
        browser_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(browser_row, text="候補画像", width=14, anchor="w").pack(side=tk.LEFT)

        def browse_image_path():
            selected_paths = filedialog.askopenfilenames(
                parent=root,
                title="画像ファイルを選択",
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")],
            )
            if selected_paths:
                normalized_list = [normalize_template_path(p) for p in selected_paths]
                added = 0
                for p in normalized_list:
                    if p and p not in available_image_paths:
                        available_image_paths.append(p)
                        added += 1
                if added > 0:
                    refresh_available_images()
                    log(f"候補画像を {added} 件登録")

        tk.Button(browser_row, text="参照...", command=browse_image_path).pack(side=tk.LEFT, padx=(8, 0))

        middle = tk.Frame(frame)
        middle.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        left = tk.LabelFrame(middle, text="候補画像（複数選択可）", padx=8, pady=8)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        candidate_list_area = tk.Frame(left)
        candidate_list_area.pack(fill=tk.BOTH, expand=True)

        candidate_scrollbar = tk.Scrollbar(candidate_list_area, orient="vertical")
        candidate_listbox = tk.Listbox(candidate_list_area, selectmode=tk.EXTENDED, yscrollcommand=candidate_scrollbar.set)
        candidate_scrollbar.config(command=candidate_listbox.yview)
        candidate_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        candidate_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        candidate_button_row = tk.Frame(left)
        candidate_button_row.pack(fill=tk.X, pady=(8, 0), anchor="w")
        btn_remove_candidate = tk.Button(candidate_button_row, text="候補から除外", width=14)
        btn_remove_candidate.pack(anchor="w")

        control_panel = tk.Frame(middle, padx=6, pady=8)
        control_panel.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(control_panel, text="→", font=("Yu Gothic UI", 18, "bold")).pack(pady=(120, 4))
        btn_add_enabled = tk.Button(control_panel, text="有効化へ追加", width=14)
        btn_add_enabled.pack(pady=(0, 18))

        tk.Label(control_panel, text="←", font=("Yu Gothic UI", 18, "bold")).pack(pady=(0, 4))
        btn_remove_enabled = tk.Button(control_panel, text="有効化から除外", width=14)
        btn_remove_enabled.pack(pady=(0, 18))

        right = tk.Frame(middle)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)

        preview_group = tk.LabelFrame(right, text="画像プレビュー", padx=8, pady=8)
        preview_group.pack(fill=tk.BOTH, expand=False)
        preview_canvas = tk.Canvas(preview_group, width=preview_size[0], height=preview_size[1], bg="white", highlightthickness=0)
        preview_canvas.pack(fill=tk.BOTH, expand=True)

        tk.Label(right, text="有効化する画像（クリック対象）", anchor="w").pack(fill=tk.X, pady=(8, 2))
        selected_listbox = tk.Listbox(right, height=9, width=48, selectmode=tk.EXTENDED)
        selected_listbox.pack(fill=tk.BOTH, expand=False)

        def refresh_selected_list():
            selected_listbox.delete(0, tk.END)
            for p in selected_template_paths:
                selected_listbox.insert(tk.END, os.path.basename(p))

        def select_enabled_image(index):
            selected_listbox.selection_clear(0, tk.END)
            if 0 <= index < len(selected_template_paths):
                selected_listbox.selection_set(index)
                selected_listbox.activate(index)
                selected_listbox.see(index)
                refresh_preview(selected_template_paths[index])
            else:
                refresh_preview("")

        def hide_selected_tooltip(event=None):
            if tooltip_state["window"] is not None:
                tooltip_state["window"].destroy()
                tooltip_state["window"] = None
                tooltip_state["index"] = None

        def show_selected_tooltip(event):
            if not selected_template_paths:
                hide_selected_tooltip()
                return
            index = selected_listbox.nearest(event.y)
            if index < 0 or index >= len(selected_template_paths):
                hide_selected_tooltip()
                return
            bbox = selected_listbox.bbox(index)
            if bbox is None or not (bbox[1] <= event.y <= bbox[1] + bbox[3]):
                hide_selected_tooltip()
                return
            text = selected_template_paths[index]
            if tooltip_state["window"] is not None and tooltip_state["index"] == index:
                x = selected_listbox.winfo_rootx() + event.x + 16
                y = selected_listbox.winfo_rooty() + event.y + 16
                tooltip_state["window"].geometry(f"+{x}+{y}")
                return
            hide_selected_tooltip()
            tip = tk.Toplevel(root)
            tip.wm_overrideredirect(True)
            x = selected_listbox.winfo_rootx() + event.x + 16
            y = selected_listbox.winfo_rooty() + event.y + 16
            tip.geometry(f"+{x}+{y}")
            tk.Label(tip, text=text, bg="#fff8cc", relief=tk.SOLID, borderwidth=1, padx=6, pady=3).pack()
            tooltip_state["window"] = tip
            tooltip_state["index"] = index

        selected_listbox.bind("<Motion>", show_selected_tooltip)
        selected_listbox.bind("<Leave>", hide_selected_tooltip)

        def on_selected_template_select(event=None):
            selected_indices = selected_listbox.curselection()
            if not selected_indices:
                refresh_preview("")
                return
            first_idx = int(selected_indices[0])
            if 0 <= first_idx < len(selected_template_paths):
                refresh_preview(selected_template_paths[first_idx])

        selected_listbox.bind("<<ListboxSelect>>", on_selected_template_select)

        def refresh_preview(path: str):
            preview_canvas.delete("all")
            if not path:
                preview_photo["image"] = None
                preview_canvas.create_text(preview_size[0] // 2, preview_size[1] // 2, text="画像を選択してください")
                return
            abs_path = normalize_template_path(path)
            try:
                with Image.open(abs_path) as img:
                    preview_image = img.convert("RGBA")
                preview_image.thumbnail(preview_size, Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(preview_image)
                preview_photo["image"] = photo
                preview_canvas.create_image(preview_size[0] // 2, preview_size[1] // 2, image=photo, anchor="center")
            except Exception:
                preview_photo["image"] = None
                preview_canvas.create_text(preview_size[0] // 2, preview_size[1] // 2, text="プレビュー失敗")

        def refresh_available_images():
            candidate_listbox.delete(0, tk.END)

            if not available_image_paths:
                refresh_preview("")
                return

            available_image_paths.sort(key=str.lower)
            for p in available_image_paths:
                candidate_listbox.insert(tk.END, p)

            if available_image_paths:
                candidate_listbox.selection_set(0)
                refresh_preview(available_image_paths[0])

        def on_candidate_select(event=None):
            selected_indices = candidate_listbox.curselection()
            if not selected_indices:
                refresh_preview("")
                return
            first_idx = int(selected_indices[0])
            if 0 <= first_idx < len(available_image_paths):
                refresh_preview(available_image_paths[first_idx])

        candidate_listbox.bind("<<ListboxSelect>>", on_candidate_select)

        def _add_template_path(path, source_label):
            p = normalize_template_path(path)
            if not p:
                return False
            if not os.path.isfile(p):
                messagebox.showerror("パスエラー", f"画像ファイルが見つかりません: {p}", parent=root)
                return False
            file_name = os.path.basename(p)
            if any(os.path.basename(existing) == file_name for existing in selected_template_paths):
                messagebox.showwarning("重複ファイル名", f"同名ファイルが既に有効化されています: {file_name}", parent=root)
                return False
            if p in selected_template_paths:
                return False
            selected_template_paths.append(p)
            log(f"{source_label}画像を追加: {p}")
            return True

        def add_selected_candidate_images():
            indices = candidate_listbox.curselection()
            if not indices:
                messagebox.showwarning("選択なし", "候補画像を1件以上選択してください。", parent=root)
                return
            added = 0
            last_added_index = -1
            for idx in indices:
                i = int(idx)
                if 0 <= i < len(available_image_paths):
                    if _add_template_path(available_image_paths[i], "候補"):
                        added += 1
                        last_added_index = len(selected_template_paths) - 1
            if added > 0:
                refresh_selected_list()
                select_enabled_image(last_added_index)

        def remove_selected_template():
            sel = selected_listbox.curselection()
            if not sel:
                return
            for idx in reversed(sel):
                i = int(idx)
                if 0 <= i < len(selected_template_paths):
                    selected_template_paths.pop(i)
            refresh_selected_list()
            hide_selected_tooltip()
            if selected_template_paths:
                next_index = min(int(sel[0]), len(selected_template_paths) - 1)
                select_enabled_image(next_index)
            else:
                refresh_preview("")

        def remove_selected_candidate_images():
            sel = candidate_listbox.curselection()
            if not sel:
                return
            removing = {available_image_paths[int(idx)] for idx in sel if 0 <= int(idx) < len(available_image_paths)}
            available_image_paths[:] = [p for p in available_image_paths if p not in removing]
            refresh_available_images()

        btn_add_enabled.config(command=add_selected_candidate_images)
        btn_remove_enabled.config(command=remove_selected_template)
        btn_remove_candidate.config(command=remove_selected_candidate_images)

        def add_numeric_control(parent, label, description, initial, min_value, max_value, resolution, is_int=False):
            row = tk.Frame(parent)
            row.pack(fill=tk.X, pady=(0, 6))

            label_widget = tk.Label(row, text=label, width=16, anchor="w")
            label_widget.pack(side=tk.LEFT)
            attach_tooltip(label_widget, description)

            var = tk.DoubleVar(value=float(initial))
            ent = tk.Entry(row, width=10)
            ent.pack(side=tk.LEFT, padx=(0, 8))
            attach_tooltip(ent, description)
            syncing = {"active": False}

            def fmt(value):
                if is_int:
                    return str(int(round(value)))
                return f"{value:.3f}".rstrip("0").rstrip(".")

            def clamp(value):
                return min(max(value, min_value), max_value)

            def apply_value(value, sync_scale=True):
                value = clamp(value)
                if is_int:
                    value = int(round(value))
                syncing["active"] = True
                if sync_scale:
                    var.set(value)
                ent.delete(0, tk.END)
                ent.insert(0, fmt(value))
                syncing["active"] = False

            def on_scale(_x):
                if syncing["active"]:
                    return
                apply_value(var.get(), sync_scale=False)

            scale = tk.Scale(
                row,
                from_=min_value,
                to=max_value,
                orient=tk.HORIZONTAL,
                resolution=resolution,
                showvalue=False,
                length=320,
                variable=var,
                command=on_scale,
            )
            scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
            attach_tooltip(scale, description)

            def on_entry_commit(event=None):
                try:
                    value = float(ent.get().strip())
                except ValueError:
                    apply_value(var.get())
                    return
                apply_value(value)

            ent.bind("<Return>", on_entry_commit)
            ent.bind("<FocusOut>", on_entry_commit)
            apply_value(float(initial))

            def get_value():
                on_entry_commit()
                if is_int:
                    return int(round(var.get()))
                return float(var.get())

            def set_value(value):
                apply_value(float(value))

            return get_value, set_value

        numeric_frame = tk.LabelFrame(frame, text="数値設定", padx=8, pady=8)
        numeric_frame.pack(fill=tk.X, pady=(0, 12))

        get_threshold, set_threshold = add_numeric_control(
            numeric_frame,
            "一致しきい値",
            "テンプレート一致判定の厳しさです。高いほど厳密に一致した画像だけを検出します。",
            config.get("threshold", 0.7),
            0.0,
            1.0,
            0.01,
        )
        get_click_interval, set_click_interval = add_numeric_control(
            numeric_frame,
            "クリック間隔",
            "1つの画像をクリックした後に待機する秒数です。短いほど連続クリックが速くなります。",
            config.get("click_interval", 0.2),
            0.0,
            2.0,
            0.01,
        )
        get_set_interval, set_set_interval = add_numeric_control(
            numeric_frame,
            "周回待機",
            "全候補画像を一通り探し終えた後に待機する秒数です。",
            config.get("set_interval", 0.2),
            0.0,
            5.0,
            0.01,
        )
        get_scale_min, set_scale_min = add_numeric_control(
            numeric_frame,
            "最小倍率",
            "画像検索時に試す最小拡大率です。小さい画像も拾いたい時に下げます。",
            config.get("scale_min", 0.8),
            0.1,
            2.0,
            0.01,
        )
        get_scale_max, set_scale_max = add_numeric_control(
            numeric_frame,
            "最大倍率",
            "画像検索時に試す最大拡大率です。大きい画像も拾いたい時に上げます。",
            config.get("scale_max", 1.2),
            0.1,
            3.0,
            0.01,
        )
        get_scale_step, set_scale_step = add_numeric_control(
            numeric_frame,
            "倍率刻み",
            "最小倍率から最大倍率までをどの間隔で試すかです。細かいほど検出精度は上がりますが重くなります。",
            config.get("scale_step", 0.05),
            0.01,
            0.5,
            0.01,
        )
        get_min_template_size, set_min_template_size = add_numeric_control(
            numeric_frame,
            "最小画像サイズ",
            "縮小後の画像サイズがこの値より小さい場合、その倍率では検索しません。",
            config.get("min_template_size", 10),
            1,
            300,
            1,
            is_int=True,
        )
        get_search_downscale, set_search_downscale = add_numeric_control(
            numeric_frame,
            "検索縮小率",
            "画面を縮小して候補を探してから、候補の周りだけを元の解像度で確かめます。\n"
            "小さいほど速くなりますが、小さい画像は見逃しやすくなります（1.0 で縮小なし）。",
            config.get("search_downscale", 0.5),
            0.2,
            1.0,
            0.05,
        )
        get_rearm_delay, set_rearm_delay = add_numeric_control(
            numeric_frame,
            "再クリック待ち",
            "クリック方式が「出現ごとに1回」のとき、対象が消えてからこの秒数たつと、\n"
            "次に出た時にまたクリックします。",
            config.get("rearm_delay", 0.3),
            0.0,
            5.0,
            0.05,
        )

        mode_row = tk.Frame(numeric_frame)
        mode_row.pack(fill=tk.X, pady=(0, 6))
        mode_label = tk.Label(mode_row, text="クリック方式", width=16, anchor="w")
        mode_label.pack(side=tk.LEFT)
        attach_tooltip(
            mode_label,
            "毎周クリック: 見えている間は毎周クリックします（ボタン連打向け）。\n"
            "出現ごとに1回: 出てきた時に1回だけクリックし、消えるまで待ちます（ポップアップ向け）。",
        )
        click_mode_var = tk.StringVar(value=config.get("click_mode", "repeat"))
        tk.Radiobutton(mode_row, text="毎周クリック", variable=click_mode_var, value="repeat").pack(side=tk.LEFT)
        tk.Radiobutton(mode_row, text="出現ごとに1回", variable=click_mode_var, value="once").pack(side=tk.LEFT, padx=(8, 0))

        def _default_templates(cfg):
            out = []
            image_dir = str(cfg.get("image_dir", "images") or "images")
            for name in cfg.get("button_names", []):
                np = normalize_template_path(os.path.join(image_dir, f"{name}.png"))
                if np and np not in out:
                    out.append(np)
            for p in cfg.get("templates", []):
                np = resolve_template_entry(p, cfg.get("candidate_templates", []))
                if np and np not in out:
                    out.append(np)
            return out

        def _apply_config_to_form(current):
            nonlocal selected_template_paths, available_image_paths
            current_candidates = [normalize_template_path(p) for p in current.get("candidate_templates", []) if normalize_template_path(p)]
            selected_template_paths = []
            for path in _default_templates({**current, "candidate_templates": current_candidates}):
                if path and path not in selected_template_paths:
                    selected_template_paths.append(path)
            available_image_paths = []
            for p in current_candidates:
                if p not in available_image_paths:
                    available_image_paths.append(p)
            for p in selected_template_paths:
                if p not in available_image_paths:
                    available_image_paths.append(p)
            refresh_selected_list()
            if selected_template_paths:
                select_enabled_image(0)
            else:
                refresh_preview("")
            refresh_available_images()

            set_threshold(current.get("threshold", default_config.get("threshold", 0.7)))
            set_click_interval(current.get("click_interval", default_config.get("click_interval", 0.2)))
            set_set_interval(current.get("set_interval", default_config.get("set_interval", 0.2)))
            set_scale_min(current.get("scale_min", default_config.get("scale_min", 0.8)))
            set_scale_max(current.get("scale_max", default_config.get("scale_max", 1.2)))
            set_scale_step(current.get("scale_step", default_config.get("scale_step", 0.05)))
            set_min_template_size(current.get("min_template_size", default_config.get("min_template_size", 10)))
            set_search_downscale(current.get("search_downscale", default_config.get("search_downscale", 0.5)))
            set_rearm_delay(current.get("rearm_delay", default_config.get("rearm_delay", 0.3)))
            click_mode_var.set(current.get("click_mode", default_config.get("click_mode", "repeat")))

        def _apply_default_numeric_values():
            set_threshold(default_config.get("threshold", 0.7))
            set_click_interval(default_config.get("click_interval", 0.2))
            set_set_interval(default_config.get("set_interval", 0.2))
            set_scale_min(default_config.get("scale_min", 0.8))
            set_scale_max(default_config.get("scale_max", 1.2))
            set_scale_step(default_config.get("scale_step", 0.05))
            set_min_template_size(default_config.get("min_template_size", 10))
            set_search_downscale(default_config.get("search_downscale", 0.5))
            set_rearm_delay(default_config.get("rearm_delay", 0.3))
            click_mode_var.set(default_config.get("click_mode", "repeat"))

        def on_reset_defaults():
            if not messagebox.askyesno("確認", "数値設定だけをデフォルト値に戻しますか？", parent=root):
                return
            _apply_default_numeric_values()
            log("設定UIの数値設定をデフォルト値に戻しました（未保存）")

        def _save():
            try:
                payload = {
                    "button_names": [],
                    "templates": [os.path.basename(p) for p in selected_template_paths],
                    "candidate_templates": [clicker.portable_path(p) for p in available_image_paths],
                    "threshold": get_threshold(),
                    "click_interval": get_click_interval(),
                    "set_interval": get_set_interval(),
                    "scale_min": get_scale_min(),
                    "scale_max": get_scale_max(),
                    "scale_step": get_scale_step(),
                    "min_template_size": get_min_template_size(),
                    "click_mode": click_mode_var.get(),
                    "rearm_delay": get_rearm_delay(),
                    "search_downscale": get_search_downscale(),
                }
            except (TypeError, ValueError):
                messagebox.showerror("入力エラー", "数値項目の形式が正しくありません。", parent=root)
                return

            if not payload["templates"]:
                messagebox.showerror("入力エラー", "クリック対象画像を1件以上追加してください。", parent=root)
                return

            # 設定UIに無い項目（cooking_steps など）は消さずに残す
            try:
                with open(clicker.CONFIG_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    payload = {**existing, **payload}
            except Exception:
                pass

            try:
                with open(clicker.CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                clicker.reload_config(log)
                messagebox.showinfo("保存完了", "設定を保存して再読み込みしました。", parent=root)
                log("設定UIから設定を保存しました")
            except Exception as e:
                messagebox.showerror("保存エラー", str(e), parent=root)

        def on_close():
            global settings_window
            hide_selected_tooltip()
            settings_window = None
            root.destroy()

        buttons = tk.Frame(frame, pady=8)
        buttons.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Button(buttons, text="保存して適用", command=_save, width=16, pady=4).pack(side=tk.LEFT)
        tk.Button(buttons, text="デフォルトに戻す", command=on_reset_defaults, width=16, pady=4).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(buttons, text="閉じる", command=on_close, width=12, pady=4).pack(side=tk.RIGHT)

        refresh_selected_list()
        refresh_available_images()

        root.protocol("WM_DELETE_WINDOW", on_close)

    run_on_ui(_open)

# ===== 設定 =====
# clicker.py の設定を編集してください
# ================

# 動作状態。ワーカーごとに専用の停止イベントを持たせることで、
# 素早く OFF→ON しても古いワーカーが動き続けて二重クリックすることがない。
state_lock = threading.Lock()
current_stop = None  # 動作中のワーカーの停止イベント（OFF なら None）
current_mode = None  # "normal" / "cooking" / "snowman"（OFF なら None）
current_finish = None  # 順番クリックの「作りかけを終えたら止める」依頼
MODE_LABELS = {"normal": "AutoClick", "cooking": "クッキング自動", "snowman": "雪だるま自動"}
# 順番クリックのモード（clicker.SEQUENCES と同じ名前）
SEQ_MODES = {
    "cooking": {"hotkey": COOKING_HOTKEY, "work": "料理", "color": "#f0961e"},
    "snowman": {"hotkey": SNOWMAN_HOTKEY, "work": "雪だるま", "color": "#46b4f0"},
}


def _hk(key):
    return key.upper() if key else "メニュー"
shutdown = threading.Event()
tray_icon = None


def is_active():
    with state_lock:
        return current_stop is not None


def active_mode():
    with state_lock:
        return current_mode


def is_finishing():
    with state_lock:
        return current_finish is not None and current_finish.is_set()


def make_icon(active: bool, mode: str = "normal", finishing: bool = False) -> Image.Image:
    """通常=緑丸, クッキング=オレンジ丸, 雪だるま=水色丸, 終了待ち=黄丸, 停止=赤丸 のアイコンを生成"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if not active:
        color = (200, 50, 50)
    elif finishing:
        color = (230, 210, 40)
    elif mode in SEQ_MODES:
        color = ImageColor.getrgb(SEQ_MODES[mode]["color"])
    else:
        color = (50, 200, 50)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    # 中央に記号
    if active:
        # 再生ボタン風の三角
        draw.polygon([(22, 18), (22, 46), (48, 32)], fill="white")
    else:
        # 停止ボタン風の四角
        draw.rectangle([20, 20, 44, 44], fill="white")
    return img


# ===== 画面上の状態表示（オーバーレイ） =====
# ゲーム中はタスクトレイが見えないので、画面の隅に小さく状態を出す。
# クリックは下のゲームにそのまま届く（クリック透過）。
# ※ ゲームが「排他的フルスクリーン」だと上に出せない。ボーダーレス／ウィンドウモードで使う。
overlay_window = None
overlay_canvas = None
overlay_hide_job = None
overlay_keep_job = None
OVERLAY_KEY_COLOR = "#010203"  # この色の部分は透明になる


def _set_click_through(win):
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020   # クリックを下のウィンドウへ通す
    WS_EX_TOOLWINDOW = 0x00000080    # タスクバーや Alt+Tab に出さない
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000    # フォーカスを奪わない
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE)


def _create_overlay():
    global overlay_window, overlay_canvas
    win = tk.Toplevel(ui_root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", OVERLAY_ALPHA)
    win.attributes("-transparentcolor", OVERLAY_KEY_COLOR)
    win.configure(bg=OVERLAY_KEY_COLOR)
    canvas = tk.Canvas(win, bg=OVERLAY_KEY_COLOR, highlightthickness=0, bd=0)
    canvas.pack()
    win.withdraw()
    win.update_idletasks()
    _set_click_through(win)
    overlay_window, overlay_canvas = win, canvas


def _round_rect(canvas, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _overlay_keep_on_top():
    """ゲームが前面に来ても隠れないよう、表示中は定期的に最前面へ戻す。"""
    global overlay_keep_job
    overlay_keep_job = None
    if overlay_window is None or not overlay_window.winfo_viewable():
        return
    overlay_window.attributes("-topmost", True)
    overlay_window.lift()
    overlay_keep_job = ui_root.after(1000, _overlay_keep_on_top)


def _overlay_hide():
    global overlay_hide_job
    overlay_hide_job = None
    if overlay_window is not None:
        overlay_window.withdraw()


def show_overlay(mode, finishing):
    """UI スレッドで呼ぶ。mode=None は OFF（少し表示して消す）。"""
    global overlay_hide_job, overlay_keep_job
    if not OVERLAY_ENABLED:
        return
    if overlay_window is None:
        _create_overlay()
    if overlay_hide_job is not None:
        ui_root.after_cancel(overlay_hide_job)
        overlay_hide_job = None

    if mode is None:
        color, text, sub = "#d23c3c", "AutoClick 停止", ""
    elif finishing:
        info = SEQ_MODES[mode]
        color = "#e6d228"
        text = f"{MODE_LABELS[mode]}  {info['work']}を終えたら停止"
        sub = f"{_hk(info['hotkey'])}: すぐ止める / ESC: 停止"
    elif mode in SEQ_MODES:
        info = SEQ_MODES[mode]
        color, text = info["color"], f"{MODE_LABELS[mode]}  ON"
        sub = f"{_hk(info['hotkey'])}: {info['work']}を終えたら停止 / ESC: すぐ停止"
    else:
        color, text, sub = "#32c832", f"{MODE_LABELS[mode]}  ON", f"{_hk(TOGGLE_HOTKEY)}: 停止 / ESC: 停止"

    c = overlay_canvas
    c.delete("all")
    font = ("Yu Gothic UI", 12, "bold")
    sub_font = ("Yu Gothic UI", 9)
    pad, dot = 12, 14
    t = c.create_text(0, 0, text=text, font=font, anchor="nw")
    tw, th = c.bbox(t)[2] - c.bbox(t)[0], c.bbox(t)[3] - c.bbox(t)[1]
    c.delete(t)
    sw = sh = 0
    if sub:
        s = c.create_text(0, 0, text=sub, font=sub_font, anchor="nw")
        sw, sh = c.bbox(s)[2] - c.bbox(s)[0], c.bbox(s)[3] - c.bbox(s)[1]
        c.delete(s)
    w = pad + dot + 8 + max(tw, sw) + pad
    h = pad + th + (sh + 2 if sub else 0) + pad
    c.config(width=w, height=h)
    _round_rect(c, 0, 0, w - 1, h - 1, 14, fill="#1e1e22", outline=color, width=2)
    cy = pad + th // 2
    c.create_oval(pad, cy - dot // 2, pad + dot, cy + dot // 2, fill=color, outline="")
    c.create_text(pad + dot + 8, pad, text=text, font=font, fill="white", anchor="nw")
    if sub:
        c.create_text(pad + dot + 8, pad + th + 2, text=sub, font=sub_font, fill="#bbbbbb", anchor="nw")

    sx, sy = ui_root.winfo_screenwidth(), ui_root.winfo_screenheight()
    m = OVERLAY_MARGIN
    x = m if "left" in OVERLAY_POSITION else sx - w - m
    y = m if "top" in OVERLAY_POSITION else sy - h - m - 48  # 下はタスクバーの分あける
    overlay_window.geometry(f"{w}x{h}+{x}+{y}")
    overlay_window.deiconify()
    overlay_window.attributes("-topmost", True)
    overlay_window.lift()
    if overlay_keep_job is None:
        overlay_keep_job = ui_root.after(1000, _overlay_keep_on_top)
    if mode is None:
        overlay_hide_job = ui_root.after(int(OVERLAY_OFF_SECONDS * 1000), _overlay_hide)


# ===== キー設定の表示（F1） =====
help_window = None
help_canvas = None
help_hide_job = None


def _help_rows():
    """(キー, 説明, このモード) の一覧。"""
    rows = []
    if TOGGLE_HOTKEY:
        rows.append((_hk(TOGGLE_HOTKEY), "通常の AutoClick  ON / OFF", "normal"))
    for name, info in sorted(SEQ_MODES.items(), key=lambda kv: (len(kv[1]["hotkey"] or ""), kv[1]["hotkey"] or "")):
        if info["hotkey"]:
            rows.append((_hk(info["hotkey"]),
                         f"{MODE_LABELS[name]}  開始 → もう一度で{info['work']}を終えたら停止", name))
    rows.append((STOP_HOTKEY.upper(), "すぐ停止", None))
    if HELP_HOTKEY:
        rows.append((_hk(HELP_HOTKEY), "この表示", None))
    return rows


def _help_hide():
    global help_hide_job
    help_hide_job = None
    if help_window is not None:
        help_window.withdraw()


def show_help(mode):
    """UI スレッドで呼ぶ。表示中ならたたむ。"""
    global help_window, help_canvas, help_hide_job
    if help_window is None:
        win = tk.Toplevel(ui_root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", OVERLAY_ALPHA)
        win.attributes("-transparentcolor", OVERLAY_KEY_COLOR)
        win.configure(bg=OVERLAY_KEY_COLOR)
        canvas = tk.Canvas(win, bg=OVERLAY_KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack()
        win.withdraw()
        win.update_idletasks()
        _set_click_through(win)
        help_window, help_canvas = win, canvas
    if help_hide_job is not None:
        ui_root.after_cancel(help_hide_job)
        help_hide_job = None
    if help_window.winfo_viewable():
        _help_hide()
        return

    c = help_canvas
    c.delete("all")
    title_font = ("Yu Gothic UI", 12, "bold")
    key_font = ("Consolas", 12, "bold")
    font = ("Yu Gothic UI", 11)
    pad, gap, row_h = 14, 12, 26

    def text_w(text, f):
        item = c.create_text(0, 0, text=text, font=f, anchor="nw")
        x0, _, x1, _ = c.bbox(item)
        c.delete(item)
        return x1 - x0

    rows = _help_rows()
    key_w = max(text_w(k, key_font) for k, _, _ in rows) + 16
    desc_w = max(text_w(d + "  ● 動作中", font) for _, d, _ in rows)
    w = pad + key_w + gap + desc_w + pad
    h = pad + 26 + len(rows) * row_h + pad
    c.config(width=w, height=h)
    _round_rect(c, 0, 0, w - 1, h - 1, 14, fill="#1e1e22", outline="#888888", width=2)
    c.create_text(pad, pad, text="AutoClick キー設定", font=title_font, fill="white", anchor="nw")
    y = pad + 30
    for key, desc, row_mode in rows:
        active = row_mode is not None and row_mode == mode
        color = SEQ_MODES[row_mode]["color"] if row_mode in SEQ_MODES else "#32c832"
        _round_rect(c, pad, y, pad + key_w, y + row_h - 6, 6,
                    fill=color if active else "#3a3a40", outline="")
        c.create_text(pad + key_w // 2, y + (row_h - 6) // 2, text=key, font=key_font,
                      fill="black" if active else "white")
        label = desc + ("  ● 動作中" if active else "")
        c.create_text(pad + key_w + gap, y + (row_h - 6) // 2, text=label, font=font,
                      fill=color if active else "#dddddd", anchor="w")
        y += row_h

    # 状態表示の下に出す
    sx, sy = ui_root.winfo_screenwidth(), ui_root.winfo_screenheight()
    m = OVERLAY_MARGIN
    status_h = 80 if OVERLAY_ENABLED else 0
    x = m if "left" in OVERLAY_POSITION else sx - w - m
    y = m + status_h if "top" in OVERLAY_POSITION else sy - h - m - 48 - status_h
    help_window.geometry(f"{w}x{h}+{x}+{y}")
    help_window.deiconify()
    help_window.attributes("-topmost", True)
    help_window.lift()
    help_hide_job = ui_root.after(int(HELP_SECONDS * 1000), _help_hide)


def toggle_help(icon=None, item=None):
    run_on_ui(show_help, active_mode())


def update_tray_icon():
    mode = active_mode()
    finishing = is_finishing()
    run_on_ui(show_overlay, mode, finishing)
    if tray_icon is None:
        return
    tray_icon.icon = make_icon(mode is not None, mode or "normal", finishing)
    if finishing:
        tray_icon.title = f"{MODE_LABELS[mode]}: {SEQ_MODES[mode]['work']}を終えたら停止"
    else:
        tray_icon.title = f"{MODE_LABELS[mode]}: ON" if mode else "AutoClick: OFF"
    try:
        tray_icon.update_menu()
    except Exception:
        pass


def auto_click_loop(stop, mode, finish):
    global current_stop, current_mode, current_finish
    running = lambda: not stop.is_set() and not shutdown.is_set()
    try:
        if mode in SEQ_MODES:
            clicker.run_sequence(running, log, should_finish=finish.is_set, name=mode)
        else:
            clicker.run(running, log)
    except Exception as e:
        log(f"[エラー] {type(e).__name__}: {e}")
    finally:
        # 緊急停止やエラーで自分から止まった場合は OFF 表示に戻す。
        # すでに新しいワーカーに置き換わっていたら何もしない。
        with state_lock:
            changed = current_stop is stop
            if changed:
                current_stop = None
                current_mode = None
                current_finish = None
        if changed:
            update_tray_icon()
            log(f"{MODE_LABELS[mode]} 停止")


def set_active(active, mode="normal"):
    """active=True で mode を開始（別モードが動いていたら止めて切り替え）。"""
    global current_stop, current_mode, current_finish
    with state_lock:
        old_mode = current_mode
        if active and old_mode == mode:
            return
        if not active and old_mode is None:
            return
        if current_stop is not None:
            current_stop.set()
        if active:
            stop, finish = threading.Event(), threading.Event()
            current_stop, current_mode, current_finish = stop, mode, finish
        else:
            current_stop, current_mode, current_finish = None, None, None
    update_tray_icon()
    if old_mode is not None:
        log(f"{MODE_LABELS[old_mode]} 停止")
    if active:
        log(f"{MODE_LABELS[mode]} 開始")
        threading.Thread(target=auto_click_loop, args=(stop, mode, finish), daemon=True).start()


def _busy_with_other(mode):
    """別のモードが動作中なら True（同時には動かさない）。"""
    other = active_mode()
    if other is not None and other != mode:
        log(f"{MODE_LABELS[other]} が動作中なので {MODE_LABELS[mode]} は開始しません（先に止めてください）")
        return True
    return False


def toggle(icon=None, item=None):
    if _busy_with_other("normal"):
        return
    set_active(active_mode() != "normal", "normal")


def toggle_sequence(name):
    """OFF → 開始 / 動作中 → 作りかけを終えたら止める / 終了待ち中 → すぐ止める"""
    if _busy_with_other(name):
        return
    with state_lock:
        running = current_mode == name
        finish = current_finish
        request_finish = running and not finish.is_set()
        if request_finish:
            finish.set()
    if request_finish:
        work = SEQ_MODES[name]["work"]
        log(f"{MODE_LABELS[name]}: 作りかけの{work}を終えたら止めます（もう一度押すとすぐ止めます）")
        update_tray_icon()
    else:
        set_active(not running, name)


def toggle_cooking(icon=None, item=None):
    toggle_sequence("cooking")


def toggle_snowman(icon=None, item=None):
    toggle_sequence("snowman")


def reload_settings(icon, item):
    open_settings_window(icon, item)


def register_hotkeys():
    try:
        keyboard.on_press_key(STOP_HOTKEY, lambda e: set_active(False))
        if TOGGLE_HOTKEY:
            keyboard.add_hotkey(TOGGLE_HOTKEY, toggle)
        if COOKING_HOTKEY:
            keyboard.add_hotkey(COOKING_HOTKEY, toggle_cooking)
        if SNOWMAN_HOTKEY:
            keyboard.add_hotkey(SNOWMAN_HOTKEY, toggle_snowman)
        if HELP_HOTKEY:
            keyboard.add_hotkey(HELP_HOTKEY, toggle_help)
        keys = f"{TOGGLE_HOTKEY.upper()}: ON/OFF, " if TOGGLE_HOTKEY else ""
        keys += f"{SNOWMAN_HOTKEY.upper()}: 雪だるま自動, " if SNOWMAN_HOTKEY else ""
        keys += f"{COOKING_HOTKEY.upper()}: クッキング自動, " if COOKING_HOTKEY else ""
        keys += f"{HELP_HOTKEY.upper()}: キー設定を表示, " if HELP_HOTKEY else ""
        log(f"ホットキー {keys}{STOP_HOTKEY.upper()}: 停止")
    except Exception as e:
        log(f"[警告] ホットキーを登録できませんでした: {e}")


def on_quit(icon, item):
    shutdown.set()
    set_active(False)
    log("終了しました")
    run_on_ui(None)
    icon.stop()


def main():
    global tray_icon
    threading.Thread(target=ui_main, daemon=True).start()
    register_hotkeys()
    clicker.reload_config(log)
    icon = pystray.Icon(
        name="AutoClick",
        icon=make_icon(False),
        title="AutoClick: OFF",
        menu=pystray.Menu(
            pystray.MenuItem("ON / OFF", toggle, default=True,
                             checked=lambda item: active_mode() == "normal"),
            pystray.MenuItem(
                lambda item: "クッキング自動 すぐ止める"
                if is_finishing() and active_mode() == "cooking" else "クッキング自動 ON / OFF",
                toggle_cooking,
                checked=lambda item: active_mode() == "cooking"),
            pystray.MenuItem(
                lambda item: "雪だるま自動 すぐ止める"
                if is_finishing() and active_mode() == "snowman" else "雪だるま自動 ON / OFF",
                toggle_snowman,
                checked=lambda item: active_mode() == "snowman"),
            pystray.MenuItem("設定", reload_settings),
            pystray.MenuItem("キー設定を表示", toggle_help),
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
    try:
        main()
    except KeyboardInterrupt:
        shutdown.set()
    except Exception:
        show_error("予期しないエラーで終了しました:\n\n" + traceback.format_exc())
        sys.exit(1)
