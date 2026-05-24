import json
import os
import threading
import time
import pyautogui
import cv2
import numpy as np

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "autoclick_config.json")
CONFIG_DIR = os.path.dirname(CONFIG_PATH)
DEFAULT_CONFIG = {
    "button_names": ["button_1"],
    "templates": [],
    "candidate_templates": [],
    "image_dir": "images",
    "threshold": 0.7,
    "click_interval": 0.2,
    "set_interval": 0.2,
    "scale_min": 0.8,
    "scale_max": 1.2,
    "scale_step": 0.05,
    "min_template_size": 10,
}

_config_lock = threading.Lock()
_config = dict(DEFAULT_CONFIG)
_config_version = 0

pyautogui.PAUSE = 0


def _build_scales(config):
    scale_min = float(config.get("scale_min", DEFAULT_CONFIG["scale_min"]))
    scale_max = float(config.get("scale_max", DEFAULT_CONFIG["scale_max"]))
    scale_step = float(config.get("scale_step", DEFAULT_CONFIG["scale_step"]))

    if scale_step <= 0:
        scale_step = DEFAULT_CONFIG["scale_step"]
    if scale_min <= 0:
        scale_min = DEFAULT_CONFIG["scale_min"]
    if scale_max < scale_min:
        scale_max = scale_min

    # np.arange の端数ずれ対策として上限に step の半分を足す
    return np.arange(scale_min, scale_max + scale_step / 2, scale_step)


def _normalize_template_path(path):
    value = str(path).strip()
    if not value:
        return ""
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(CONFIG_DIR, value))


def _resolve_template_path(entry, candidate_templates, image_dir):
    value = str(entry).strip()
    if not value:
        return ""

    if os.path.isabs(value) or any(sep in value for sep in ("/", "\\")):
        return _normalize_template_path(value)

    for candidate in candidate_templates:
        if os.path.basename(candidate) == value:
            return candidate

    return _normalize_template_path(os.path.join(image_dir, value))


def _normalize_config(raw, log):
    merged = dict(DEFAULT_CONFIG)
    merged.update(raw or {})

    image_dir = str(merged.get("image_dir", DEFAULT_CONFIG["image_dir"])).strip() or DEFAULT_CONFIG["image_dir"]
    merged["image_dir"] = image_dir

    button_names = merged.get("button_names", [])
    if not isinstance(button_names, list):
        log("[警告] button_names が不正です。空配列として扱います。")
        button_names = []
    button_names = [str(name).strip() for name in button_names if str(name).strip()]

    templates = merged.get("templates", [])
    if not isinstance(templates, list):
        log("[警告] templates が不正です。空配列として扱います。")
        templates = []
    templates = [str(t).strip() for t in templates if str(t).strip()]

    candidate_templates = merged.get("candidate_templates", [])
    if not isinstance(candidate_templates, list):
        log("[警告] candidate_templates が不正です。空配列として扱います。")
        candidate_templates = []
    candidate_templates = [_normalize_template_path(t) for t in candidate_templates if str(t).strip()]

    template_paths = [_normalize_template_path(os.path.join(image_dir, f"{name}.png")) for name in button_names]
    template_paths.extend(_resolve_template_path(t, candidate_templates, image_dir) for t in templates)
    template_paths = [path for path in template_paths if path]

    if not template_paths:
        log("[警告] button_names / templates が空です。デフォルト設定を使用します。")
        default_paths = [_normalize_template_path(os.path.join(DEFAULT_CONFIG["image_dir"], f"{name}.png")) for name in DEFAULT_CONFIG["button_names"]]
        template_paths = default_paths

    # 重複除去（順序維持）
    merged["template_paths"] = list(dict.fromkeys(template_paths))
    merged["button_names"] = button_names
    merged["templates"] = templates
    merged["candidate_templates"] = list(dict.fromkeys(candidate_templates))

    merged["threshold"] = max(0.0, min(1.0, float(merged.get("threshold", DEFAULT_CONFIG["threshold"]))))
    merged["click_interval"] = max(0.0, float(merged.get("click_interval", DEFAULT_CONFIG["click_interval"])))
    merged["set_interval"] = max(0.0, float(merged.get("set_interval", DEFAULT_CONFIG["set_interval"])))
    merged["min_template_size"] = max(1, int(merged.get("min_template_size", DEFAULT_CONFIG["min_template_size"])))
    merged["scales"] = _build_scales(merged)
    return merged


def reload_config(log):
    global _config, _config_version
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        normalized = _normalize_config(raw, log)
    except FileNotFoundError:
        log(f"[警告] 設定ファイルが見つかりません: {CONFIG_PATH}")
        normalized = _normalize_config({}, log)
    except Exception as e:
        log(f"[警告] 設定ファイルの読み込みに失敗: {e}")
        normalized = _normalize_config({}, log)

    with _config_lock:
        _config = normalized
        _config_version += 1

    log(f"設定を読み込みました (ver={_config_version})")


def get_config_snapshot():
    with _config_lock:
        return dict(_config), _config_version


def load_templates(template_paths, log):
    loaded = []
    for path in template_paths:
        template = cv2.imread(path, 0)
        if template is None:
            log(f"{path} が見つかりません。")
            return None
        loaded.append((path, template, template.shape[::-1]))
        log(f"{path} 読み込み完了")
    return loaded


def run(is_running, log):
    """
    is_running: callable -> bool  (Trueの間ループ継続)
    log: callable(str)
    """
    reload_config(log)
    active_version = -1
    loaded_templates = None
    threshold = DEFAULT_CONFIG["threshold"]
    click_interval = DEFAULT_CONFIG["click_interval"]
    set_interval = DEFAULT_CONFIG["set_interval"]
    min_template_size = DEFAULT_CONFIG["min_template_size"]
    scales = _build_scales(DEFAULT_CONFIG)

    log("クリックループ開始")
    while is_running():
        try:
            runtime_config, runtime_version = get_config_snapshot()
            if runtime_version != active_version:
                loaded_templates = load_templates(runtime_config["template_paths"], log)
                if loaded_templates is None:
                    log("[エラー] テンプレート読み込み失敗。設定を見直してください")
                    time.sleep(1)
                    active_version = runtime_version
                    continue

                threshold = runtime_config["threshold"]
                click_interval = runtime_config["click_interval"]
                set_interval = runtime_config["set_interval"]
                min_template_size = runtime_config["min_template_size"]
                scales = runtime_config["scales"]
                active_version = runtime_version

            screenshot = pyautogui.screenshot()
            screenshot = np.array(screenshot)
            gray = cv2.cvtColor(screenshot, cv2.COLOR_RGB2GRAY)

            for name, template, (w, h) in loaded_templates:
                candidates = []

                for scale in scales:
                    scaled_w = int(w * scale)
                    scaled_h = int(h * scale)
                    if scaled_w < min_template_size or scaled_h < min_template_size:
                        continue
                    resized = cv2.resize(template, (scaled_w, scaled_h))
                    if resized.shape[0] > gray.shape[0] or resized.shape[1] > gray.shape[1]:
                        continue
                    result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
                    locations = np.where(result >= threshold)
                    for pt in zip(*locations[::-1]):
                        cx = pt[0] + scaled_w // 2
                        cy = pt[1] + scaled_h // 2
                        val = result[pt[1], pt[0]]
                        candidates.append((val, cx, cy, scaled_w, scaled_h))

                candidates.sort(reverse=True)
                clicked_positions = []
                click_count = 0
                for val, cx, cy, cw, ch in candidates:
                    if not is_running():
                        break
                    too_close = any(
                        abs(cx - px) < max(cw, pw) // 2 and abs(cy - py) < max(ch, ph) // 2
                        for px, py, pw, ph in clicked_positions
                    )
                    if too_close:
                        continue
                    clicked_positions.append((cx, cy, cw, ch))
                    pyautogui.moveTo(cx, cy)
                    pyautogui.mouseDown()
                    pyautogui.mouseUp()
                    click_count += 1
                    if click_interval > 0:
                        time.sleep(click_interval)

                if click_count > 0:
                    log(f"{name}: {click_count}箇所クリック")
                else:
                    log(f"{name}: 見つからず")

            if set_interval > 0:
                time.sleep(set_interval)

        except Exception as e:
            log(f"[エラー] {e}")
            time.sleep(1)

    log("クリックループ終了")
