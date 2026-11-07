import json
import os
import threading
import time
from dataclasses import dataclass, field

import pyautogui
import cv2
import numpy as np

try:
    import mss  # 高速なスクリーンショット（全モニター対応）
except ImportError:  # 未インストールなら pyautogui で撮影（メインモニターのみ）
    mss = None

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
    # "repeat": 見えている間は毎周クリック / "once": 出現ごとに1回だけクリック
    "click_mode": "repeat",
    # click_mode=once のとき、対象が消えてからこの秒数たつと再びクリック可能
    "rearm_delay": 0.3,
    # 粗い検索で画面を縮小する倍率（1.0 で縮小なし）。小さいほど速いが見逃しやすい
    "search_downscale": 0.5,
}

CLICK_MODES = ("repeat", "once")
COARSE_MARGIN = 0.25      # 粗い検索の候補下限 = threshold - COARSE_MARGIN
COARSE_MIN_SIZE = 16      # 縮小後のテンプレートがこれより小さくなる場合は縮小しない
MAX_CANDIDATES = 50       # 1テンプレート・1倍率あたりの精査候補数の上限
MASK_SUFFIX = ".mask.png"  # 例: images/foo.png に対する images/foo.mask.png

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
    click_mode = str(merged.get("click_mode", DEFAULT_CONFIG["click_mode"])).strip()
    if click_mode not in CLICK_MODES:
        log(f"[警告] click_mode '{click_mode}' は不正です。repeat として扱います。")
        click_mode = "repeat"
    merged["click_mode"] = click_mode
    merged["rearm_delay"] = max(0.0, float(merged.get("rearm_delay", DEFAULT_CONFIG["rearm_delay"])))
    merged["search_downscale"] = max(0.2, min(1.0, float(merged.get("search_downscale", DEFAULT_CONFIG["search_downscale"]))))
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


# ===== テンプレート読み込み =====

def _imread_gray(path):
    """日本語を含むパスでも読めるように np.fromfile + imdecode を使う。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def mask_path_for(path):
    return os.path.splitext(path)[0] + MASK_SUFFIX


@dataclass
class _Variant:
    scale: float
    full: np.ndarray         # 元解像度のテンプレート
    mask: np.ndarray | None  # 照合に使う部分（白）。None なら全体
    factor: float            # 粗い検索の縮小倍率
    coarse: np.ndarray       # 粗い検索用テンプレート


@dataclass
class _Template:
    path: str
    variants: list = field(default_factory=list)


def load_templates(template_paths, log, scales=None, min_template_size=None, downscale=1.0):
    if scales is None:
        scales = _build_scales(DEFAULT_CONFIG)
    if min_template_size is None:
        min_template_size = DEFAULT_CONFIG["min_template_size"]

    loaded = []
    for path in template_paths:
        template = _imread_gray(path)
        if template is None:
            log(f"{path} が見つかりません。")
            return None

        mask = None
        mpath = mask_path_for(path)
        if os.path.isfile(mpath):
            raw_mask = _imread_gray(mpath)
            if raw_mask is None or raw_mask.shape != template.shape:
                log(f"[警告] マスク画像 {mpath} はテンプレートと同じサイズにしてください。無視します。")
            else:
                mask = ((raw_mask > 127).astype(np.uint8)) * 255

        h, w = template.shape
        tpl = _Template(path)
        for scale in scales:
            sw, sh = int(w * scale), int(h * scale)
            if sw < min_template_size or sh < min_template_size:
                continue
            interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            full = template if (sw, sh) == (w, h) else cv2.resize(template, (sw, sh), interpolation=interp)
            vmask = None
            if mask is not None:
                vmask = mask if (sw, sh) == (w, h) else cv2.resize(mask, (sw, sh), interpolation=cv2.INTER_NEAREST)
            factor = downscale if min(sw, sh) * downscale >= COARSE_MIN_SIZE else 1.0
            if factor == 1.0:
                coarse = full
            else:
                cw, ch = max(1, round(sw * factor)), max(1, round(sh * factor))
                coarse = cv2.resize(full, (cw, ch), interpolation=cv2.INTER_AREA)
            tpl.variants.append(_Variant(float(scale), full, vmask, factor, coarse))

        loaded.append(tpl)
        log(f"{path} 読み込み完了" + ("（マスク使用）" if mask is not None else ""))
    return loaded


# ===== 検出 =====

def find_matches(gray, tpl, threshold, small_cache=None):
    """gray 内で tpl に一致する位置を (score, cx, cy, w, h) のリストで返す。

    縮小画像でマスクなしの粗い検索をして候補を絞り、候補の周辺だけを
    元解像度（マスクがあればマスク付き）で精査する。
    """
    if small_cache is None:
        small_cache = {}
    found = []
    for v in tpl.variants:
        th, tw = v.full.shape
        if th > gray.shape[0] or tw > gray.shape[1]:
            continue

        # 縮小なし・マスクなしなら従来どおり全画面を元解像度で照合
        if v.factor == 1.0 and v.mask is None:
            result = cv2.matchTemplate(gray, v.full, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= threshold)
            found.extend((float(result[y, x]), int(x) + tw // 2, int(y) + th // 2, tw, th)
                         for y, x in zip(ys, xs))
            continue

        small = small_cache.get(v.factor)
        if small is None:
            small = gray if v.factor == 1.0 else cv2.resize(
                gray, None, fx=v.factor, fy=v.factor, interpolation=cv2.INTER_AREA)
            small_cache[v.factor] = small
        ch, cw = v.coarse.shape
        if ch > small.shape[0] or cw > small.shape[1]:
            continue

        result = cv2.matchTemplate(small, v.coarse, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold - COARSE_MARGIN)
        if len(xs) == 0:
            continue
        scores = result[ys, xs]
        if len(scores) > 2000:
            keep = np.argpartition(-scores, 2000)[:2000]
            ys, xs, scores = ys[keep], xs[keep], scores[keep]
        picked = []
        for i in np.argsort(-scores):
            x, y = int(xs[i]), int(ys[i])
            if any(abs(x - px) <= cw // 2 and abs(y - py) <= ch // 2 for px, py in picked):
                continue
            picked.append((x, y))
            if len(picked) >= MAX_CANDIDATES:
                break

        pad = int(round(2 / v.factor)) + 2
        for x, y in picked:
            x0 = max(0, int(x / v.factor) - pad)
            y0 = max(0, int(y / v.factor) - pad)
            x1 = min(gray.shape[1], int(x / v.factor) + tw + pad)
            y1 = min(gray.shape[0], int(y / v.factor) + th + pad)
            roi = gray[y0:y1, x0:x1]
            if roi.shape[0] < th or roi.shape[1] < tw:
                continue
            fine = cv2.matchTemplate(roi, v.full, cv2.TM_CCOEFF_NORMED, mask=v.mask)
            fine = np.nan_to_num(fine, nan=-1.0, posinf=-1.0, neginf=-1.0)
            _, score, _, loc = cv2.minMaxLoc(fine)
            if score >= threshold:
                found.append((float(score), x0 + loc[0] + tw // 2, y0 + loc[1] + th // 2, tw, th))
    return found


def _too_close(cx, cy, cw, ch, px, py, pw, ph):
    return abs(cx - px) < max(cw, pw) // 2 and abs(cy - py) < max(ch, ph) // 2


def dedupe(candidates):
    """スコアの高い順に、近すぎる候補を除いた (score, cx, cy, w, h) を返す。"""
    kept = []
    for cand in sorted(candidates, reverse=True):
        _, cx, cy, cw, ch = cand
        if any(_too_close(cx, cy, cw, ch, k[1], k[2], k[3], k[4]) for k in kept):
            continue
        kept.append(cand)
    return kept


# ===== 画面キャプチャ =====

class ScreenCapture:
    """mss があれば全モニターを、なければ pyautogui でメインモニターを撮影する。
    mss はスレッドごとに作る必要があるので、ワーカースレッド内で with して使う。"""

    def __enter__(self):
        self._sct = None
        if mss is not None:
            factory = getattr(mss, "MSS", None) or mss.mss
            self._sct = factory()
            self._area = self._sct.monitors[0]
        return self

    def __exit__(self, *exc):
        if self._sct is not None:
            self._sct.close()

    def grab(self):
        """(グレースケール画像, 左端のスクリーン座標, 上端のスクリーン座標) を返す。"""
        if self._sct is not None:
            frame = np.array(self._sct.grab(self._area))
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY), self._area["left"], self._area["top"]
        frame = np.array(pyautogui.screenshot())
        return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), 0, 0


# ===== クリックループ =====

@dataclass
class _Track:
    """click_mode=once 用: クリック済みで、まだ画面に出ている対象。"""
    cx: int
    cy: int
    w: int
    h: int
    last_seen: float


def _click(x, y):
    pyautogui.moveTo(x, y)
    pyautogui.mouseDown()
    pyautogui.mouseUp()


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
    click_mode = DEFAULT_CONFIG["click_mode"]
    rearm_delay = DEFAULT_CONFIG["rearm_delay"]
    tracks = {}      # path -> [_Track]   (click_mode=once)
    was_found = {}   # path -> bool       （「見つからず」ログを状態が変わった時だけ出す）

    log("クリックループ開始")
    with ScreenCapture() as screen:
        while is_running():
            try:
                runtime_config, runtime_version = get_config_snapshot()
                if runtime_version != active_version:
                    active_version = runtime_version
                    tracks, was_found = {}, {}
                    loaded_templates = load_templates(
                        runtime_config["template_paths"], log,
                        scales=runtime_config["scales"],
                        min_template_size=runtime_config["min_template_size"],
                        downscale=runtime_config["search_downscale"],
                    )
                    if loaded_templates is None:
                        log("[エラー] テンプレート読み込み失敗。設定を見直してください")
                    else:
                        threshold = runtime_config["threshold"]
                        click_interval = runtime_config["click_interval"]
                        set_interval = runtime_config["set_interval"]
                        click_mode = runtime_config["click_mode"]
                        rearm_delay = runtime_config["rearm_delay"]
                        mode_label = "出現ごとに1回" if click_mode == "once" else "毎周クリック"
                        log(f"クリック方式: {mode_label}")
                if loaded_templates is None:
                    time.sleep(1)
                    continue

                gray, ox, oy = screen.grab()
                now = time.monotonic()
                small_cache = {}

                for tpl in loaded_templates:
                    name = tpl.path
                    detections = dedupe(find_matches(gray, tpl, threshold, small_cache))
                    tpl_tracks = tracks.setdefault(name, [])
                    click_count = 0
                    for score, cx, cy, cw, ch in detections:
                        if not is_running():
                            break
                        if click_mode == "once":
                            track = next((t for t in tpl_tracks
                                          if _too_close(cx, cy, cw, ch, t.cx, t.cy, t.w, t.h)), None)
                            if track is not None:
                                track.cx, track.cy, track.last_seen = cx, cy, now
                                continue
                        _click(ox + cx, oy + cy)
                        click_count += 1
                        if click_mode == "once":
                            tpl_tracks.append(_Track(cx, cy, cw, ch, now))
                        if click_interval > 0:
                            time.sleep(click_interval)

                    # 消えてから rearm_delay たった対象は、次に出たらまたクリックする
                    tpl_tracks[:] = [t for t in tpl_tracks if now - t.last_seen < rearm_delay]

                    if click_count > 0:
                        log(f"{name}: {click_count}箇所クリック (score={detections[0][0]:.3f})")
                    elif not detections and was_found.get(name, True):
                        log(f"{name}: 見つからず")
                    was_found[name] = bool(detections)

                if set_interval > 0:
                    time.sleep(set_interval)

            except pyautogui.FailSafeException:
                log("緊急停止しました（マウスが画面の隅に移動しました）")
                break
            except Exception as e:
                log(f"[エラー] {type(e).__name__}: {e}")
                time.sleep(1)

    log("クリックループ終了")
