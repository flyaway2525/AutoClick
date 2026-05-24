import threading
import time
import sys
import ctypes
import json
import os
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import keyboard
from PIL import Image, ImageDraw, ImageTk
import pystray
import clicker


# ===== ログ =====
log_buffer = []
log_lock = threading.Lock()
log_window = None
log_text_widget = None
settings_window = None

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
    global settings_window

    def _open():
        global settings_window
        if settings_window and tk.Toplevel.winfo_exists(settings_window):
            settings_window.lift()
            return

        project_root = os.path.dirname(clicker.CONFIG_PATH)
        config = _read_config_for_ui()
        default_config = dict(clicker.DEFAULT_CONFIG)

        root = tk.Tk()
        root.title("AutoClick 設定")
        root.geometry("920x860")
        root.minsize(920, 860)
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
                messagebox.showerror("パスエラー", f"画像ファイルが見つかりません: {p}")
                return False
            file_name = os.path.basename(p)
            if any(os.path.basename(existing) == file_name for existing in selected_template_paths):
                messagebox.showwarning("重複ファイル名", f"同名ファイルが既に有効化されています: {file_name}")
                return False
            if p in selected_template_paths:
                return False
            selected_template_paths.append(p)
            log(f"{source_label}画像を追加: {p}")
            return True

        def add_selected_candidate_images():
            indices = candidate_listbox.curselection()
            if not indices:
                messagebox.showwarning("選択なし", "候補画像を1件以上選択してください。")
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

        def _apply_default_numeric_values():
            set_threshold(default_config.get("threshold", 0.7))
            set_click_interval(default_config.get("click_interval", 0.2))
            set_set_interval(default_config.get("set_interval", 0.2))
            set_scale_min(default_config.get("scale_min", 0.8))
            set_scale_max(default_config.get("scale_max", 1.2))
            set_scale_step(default_config.get("scale_step", 0.05))
            set_min_template_size(default_config.get("min_template_size", 10))

        def on_reset_defaults():
            if not messagebox.askyesno("確認", "数値設定だけをデフォルト値に戻しますか？"):
                return
            _apply_default_numeric_values()
            log("設定UIの数値設定をデフォルト値に戻しました（未保存）")

        def _save():
            try:
                payload = {
                    "button_names": [],
                    "templates": [os.path.basename(p) for p in selected_template_paths],
                    "candidate_templates": list(available_image_paths),
                    "threshold": get_threshold(),
                    "click_interval": get_click_interval(),
                    "set_interval": get_set_interval(),
                    "scale_min": get_scale_min(),
                    "scale_max": get_scale_max(),
                    "scale_step": get_scale_step(),
                    "min_template_size": get_min_template_size(),
                }
            except (TypeError, ValueError):
                messagebox.showerror("入力エラー", "数値項目の形式が正しくありません。")
                return

            if not payload["templates"]:
                messagebox.showerror("入力エラー", "クリック対象画像を1件以上追加してください。")
                return

            try:
                with open(clicker.CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                clicker.reload_config(log)
                messagebox.showinfo("保存完了", "設定を保存して再読み込みしました。")
                log("設定UIから設定を保存しました")
            except Exception as e:
                messagebox.showerror("保存エラー", str(e))

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


def reload_settings(icon, item):
    open_settings_window(icon, item)


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
    clicker.reload_config(log)
    icon = pystray.Icon(
        name="AutoClick",
        icon=make_icon(False),
        title="AutoClick: OFF",
        menu=pystray.Menu(
            pystray.MenuItem("ON / OFF", toggle, default=True),
            pystray.MenuItem("設定", reload_settings),
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
