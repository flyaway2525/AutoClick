import json
import os
import sys
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

# 設定・画像・ログを置くフォルダ。exe 版では exe のあるフォルダ、
# Python から動かす時はこのファイルのあるフォルダ。
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "autoclick_config.json")
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
    # ===== クッキング自動（順番クリック）モード =====
    # 上から順に押す。文字列か {"template": ..., "threshold": ..., "retry": ..., "shared": ..., "all": ..., "repeat": ...}
    #   shared=true : コンロの上ではなく画面に1つだけ出るボタン（クッキング開始など）
    #   all=true    : 見つかったものを全部押す（雪マークなど）
    #   repeat=true : 次の shared 手順が終わったあと、まだ見えていればもう一度押す（雪玉の操作など）
    "cooking_steps": [
        "images/cook_1_pan.png",
        {"template": "images/cook_2_start.png", "shared": True},
        "images/flai_spatula.png",
        "images/cook_4_mitten.png",
    ],
    # クリックしたのにまだ見えている時、もう一度クリックするまでの秒数
    "cooking_retry_delay": 1.0,
    # コンロのボタンがこの秒数まったく見えなければ、そのコンロを忘れる
    "cooking_lost_timeout": 30.0,
    # アイコンの中心がこの距離（px）以内なら同じコンロとみなす
    "cooking_stove_radius": 60,
    # ===== 雪だるま自動 =====（手順の書き方は cooking_steps と同じ。時間の設定は cooking_* を共用）
    "snowman_steps": [
        "images/snow_1_place.png",
        {"template": "images/snow_2_operate.png", "repeat": True},
        {"template": "images/snow_3_flake.png", "shared": True, "all": True, "retry": 0.15},
        "images/snow_4_collect.png",
    ],
    # 雪玉が大きくなるとアイコンの位置が上にずれるので広めにする
    "snowman_stove_radius": 150,
    # 1回の画面チェックごとの待機秒数
    "cooking_interval": 0.05,
}

CLICK_MODES = ("repeat", "once")

# 順番クリックのモード。設定は <name>_steps / <name>_stove_radius
SEQUENCES = {
    "cooking": {"title": "クッキング自動", "place": "コンロ", "work": "料理"},
    "snowman": {"title": "雪だるま自動", "place": "雪台", "work": "雪だるま"},
}
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


def portable_path(path):
    """アプリのフォルダ内なら "images/xxx.png" のような相対パスにする（フォルダごと移動しても動く）。"""
    value = os.path.normpath(str(path).strip())
    try:
        rel = os.path.relpath(value, APP_DIR)
    except ValueError:  # 別ドライブ
        return value
    if rel.startswith(".."):
        return value
    return rel.replace(os.sep, "/")


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
    for name in SEQUENCES:
        merged[f"{name}_steps"] = _normalize_steps(merged, name, image_dir, candidate_templates, log)
        key = f"{name}_stove_radius"
        merged[key] = max(1.0, float(merged.get(key, DEFAULT_CONFIG[key])))
    merged["cooking_retry_delay"] = max(0.0, float(merged.get("cooking_retry_delay", DEFAULT_CONFIG["cooking_retry_delay"])))
    merged["cooking_lost_timeout"] = max(1.0, float(merged.get("cooking_lost_timeout", DEFAULT_CONFIG["cooking_lost_timeout"])))
    merged["cooking_interval"] = max(0.0, float(merged.get("cooking_interval", DEFAULT_CONFIG["cooking_interval"])))
    return merged


def _normalize_steps(merged, name, image_dir, candidate_templates, log):
    """<name>_steps を [{"path", "threshold", "retry", "shared", "all", "repeat"}] に揃える。"""
    key = f"{name}_steps"
    raw_steps = merged.get(key, DEFAULT_CONFIG[key])
    if not isinstance(raw_steps, list):
        log(f"[警告] {key} が不正です。デフォルトの手順を使います。")
        raw_steps = DEFAULT_CONFIG[key]
    steps = []
    for entry in raw_steps:
        options = entry if isinstance(entry, dict) else {"template": entry}
        path = _resolve_template_path(options.get("template", ""), candidate_templates, image_dir)
        if not path:
            continue
        steps.append({
            "path": path,
            "threshold": options.get("threshold"),
            "retry": options.get("retry"),
            "shared": bool(options.get("shared", False)),
            "all": bool(options.get("all", False)),
            "repeat": bool(options.get("repeat", False)),
        })
    return steps


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

def find_matches(gray, tpl, threshold, small_cache=None, scale_range=None):
    """gray 内で tpl に一致する位置を (score, cx, cy, w, h) のリストで返す。

    縮小画像でマスクなしの粗い検索をして候補を絞り、候補の周辺だけを
    元解像度（マスクがあればマスク付き）で精査する。
    scale_range=(lo, hi) を渡すと、その範囲の倍率だけを調べる。
    """
    if small_cache is None:
        small_cache = {}
    found = []
    for v in tpl.variants:
        if scale_range is not None and not (scale_range[0] <= v.scale <= scale_range[1]):
            continue
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


# ===== クッキング自動（順番クリック） =====

@dataclass
class _Press:
    """1つのボタンの押し方の状態（出てきたら押す・見えたままなら retry 秒ごとに押し直す）。"""
    armed: bool = True        # 新しく出てきた（まだ押していない）
    clicked: bool = False     # 1回以上押した
    last_click: float = 0.0
    last_seen: float = 0.0

    def want_click(self, now, retry):
        return self.armed or now - self.last_click >= retry

    def did_click(self, now):
        self.armed, self.clicked, self.last_click = False, True, now

    def unseen(self, now, rearm_delay):
        if now - self.last_seen >= rearm_delay:
            self.armed = True


@dataclass
class _Stove:
    id: int
    x: int
    y: int
    step: int                  # 今の手順（steps のインデックス、コンロ上の手順のみ）
    press: _Press = field(default_factory=_Press)
    shared_done: bool = False  # 待っている共通ボタン（クッキング開始など）を押した
    done: bool = False         # 最後の手順を押し終えた
    last_seen: float = 0.0     # 今か次の手順のボタンを最後に見た時刻


@dataclass
class _Step:
    tpl: _Template
    threshold: float
    retry: float
    shared: bool
    label: str
    all: bool = False
    repeat: bool = False


def _load_sequence_steps(cfg, name, log):
    key = f"{name}_steps"
    step_cfgs = cfg[key]
    if not step_cfgs:
        log(f"[エラー] {key} が空です。設定を見直してください")
        return None
    if all(st["shared"] for st in step_cfgs):
        log(f"[エラー] {key} に shared でない手順が1つもありません")
        return None
    tpls = load_templates(
        [st["path"] for st in step_cfgs], log,
        scales=cfg["scales"],
        min_template_size=cfg["min_template_size"],
        downscale=cfg["search_downscale"],
    )
    if tpls is None:
        log("[エラー] 手順の画像を読み込めません。設定を見直してください")
        return None
    steps = []
    for st, tpl in zip(step_cfgs, tpls):
        th = st["threshold"] if st["threshold"] is not None else cfg["threshold"]
        retry = st["retry"] if st["retry"] is not None else cfg["cooking_retry_delay"]
        steps.append(_Step(tpl, float(th), float(retry), st["shared"], os.path.basename(tpl.path),
                           st["all"], st["repeat"]))
    return steps


def run_sequence(is_running, log, should_finish=lambda: False, name="cooking"):
    """<name>_steps を上から順にクリックする。コンロが複数あっても、コンロごとに順番を守る。

    - コンロの上に出るボタンは、出た位置でコンロを見分けて、コンロごとに
      「今の手順」を押し、「次の手順」が見えたら次へ進む。最後の次は最初に戻る。
    - shared の手順（クッキング開始など）は、それを待っているコンロがあれば押す。
    - should_finish() が True になったら新しい料理は始めず、作りかけの料理を
      最後の手順まで終えてから戻る。
    """
    names = SEQUENCES[name]
    title, place, work = names["title"], names["place"], names["work"]
    reload_config(log)
    active_version = -1
    steps = None
    finish_logged = False
    log(f"{title} 開始")
    with ScreenCapture() as screen:
        while is_running():
            try:
                cfg, version = get_config_snapshot()
                if version != active_version:
                    active_version = version
                    steps = _load_sequence_steps(cfg, name, log)
                    if steps is not None:
                        local = [i for i, st in enumerate(steps) if not st.shared]
                        first, last = local[0], local[-1]
                        stoves, next_id = [], 1
                        ui_scale, last_hit = None, 0.0  # 見つかった倍率（以後はその近くだけ探す）
                        shared_press = {i: _Press() for i, st in enumerate(steps) if st.shared}
                        log("手順: " + " → ".join(f"{i + 1}.{st.label}" for i, st in enumerate(steps)))
                if steps is None:
                    time.sleep(1)
                    continue

                finishing = should_finish()
                if finishing and not finish_logged:
                    log(f"終了待ち: 作りかけの{work}を終えたら止めます")
                    finish_logged = True
                elif not finishing and finish_logged:
                    log("終了待ちを取り消しました")
                    finish_logged = False

                gray, ox, oy = screen.grab()
                now = time.monotonic()
                small_cache = {}
                radius = cfg[f"{name}_stove_radius"]
                rearm = cfg["rearm_delay"]

                def next_local(i):
                    return local[(local.index(i) + 1) % len(local)]

                def shared_between(a, b):
                    """コンロ手順 a と b の間にある shared 手順。"""
                    if b > a:
                        between = range(a + 1, b)
                    else:
                        between = list(range(a + 1, len(steps))) + list(range(0, b))
                    return [i for i in between if steps[i].shared]

                def in_progress(st):
                    if st.done:
                        return False
                    return st.step != first or st.press.clicked

                def waiting_shared(st):
                    """押したあと shared ボタン待ちのコンロか。"""
                    return st.press.clicked and bool(shared_between(st.step, next_local(st.step)))

                scale_range = None
                if ui_scale is not None:
                    span = cfg["scale_step"] * 1.01
                    scale_range = (ui_scale - span, ui_scale + span)
                dets = {i: dedupe(find_matches(gray, st.tpl, st.threshold, small_cache, scale_range))
                        for i, st in enumerate(steps)}
                hits = [(d[0], i, d) for i, ds in dets.items() for d in ds]
                if hits:
                    last_hit = now
                    if ui_scale is None:
                        _, i, d = max(hits)
                        ui_scale = min(steps[i].tpl.variants, key=lambda v: abs(v.full.shape[1] - d[3])).scale
                        log(f"画面の倍率 {ui_scale:.2f} の近くだけを探します")
                elif ui_scale is not None and now - last_hit >= cfg["cooking_lost_timeout"]:
                    ui_scale = None
                    log("しばらく見つからないので、全ての倍率で探し直します")

                # --- コンロ上のボタンを、近いコンロに割り当てる ---
                seen = {}  # stove.id -> {step: det}
                for i in local:
                    for det in dets[i]:
                        _, cx, cy, _, _ = det
                        near = [(abs(cx - st.x) + abs(cy - st.y), st.id, st) for st in stoves
                                if abs(cx - st.x) <= radius and abs(cy - st.y) <= radius]
                        if near:
                            st = min(near)[2]
                        else:
                            if finishing and i == first:
                                continue  # 終了待ち中は新しい料理を始めない
                            st = _Stove(next_id, cx, cy, i, last_seen=now)
                            next_id += 1
                            stoves.append(st)
                            log(f"{place}#{st.id} を見つけました（手順{i + 1} {steps[i].label}）")
                        seen.setdefault(st.id, {}).setdefault(i, det)

                clicks = []
                blocked_new = any(waiting_shared(st) and not st.shared_done
                                  for st in stoves if in_progress(st))

                for st in stoves:
                    found = seen.get(st.id, {})
                    nxt = next_local(st.step)
                    if nxt != st.step and nxt in found and not (finishing and nxt == first):
                        if st.step == last and not st.done:
                            log(f"{place}#{st.id} {work}完了")
                        st.step, st.press, st.shared_done, st.done = nxt, _Press(), False, False
                        log(f"{place}#{st.id} → 手順{nxt + 1} ({steps[nxt].label})")
                    det = found.get(st.step)
                    if det is None:
                        st.press.unseen(now, rearm)
                        if (st.step == last and st.press.clicked and not st.done
                                and now - st.press.last_seen >= rearm):
                            st.done = True
                            log(f"{place}#{st.id} {work}完了")
                        continue
                    st.last_seen = st.press.last_seen = now
                    st.x, st.y = det[1], det[2]
                    if st.done:
                        continue
                    if st.step == first and not st.press.clicked:
                        # 新しい料理を始めるのは、終了待ちでなく、
                        # ほかのコンロが「クッキング開始」待ちでない時だけ
                        if finishing or blocked_new:
                            continue
                        blocked_new = bool(shared_between(st.step, next_local(st.step)))
                    if waiting_shared(st):
                        # フライパンは押したあとも見えたままなので、「クッキング開始」が
                        # 出ている間や押した後に押し直すと、メニューを閉じてしまう。
                        # repeat の手順（雪玉の操作）は、shared 手順が終わったらまた押す。
                        pending = shared_between(st.step, next_local(st.step))
                        if any(dets[g] for g in pending):
                            continue
                        if st.shared_done and not steps[st.step].repeat:
                            continue
                    if st.press.want_click(now, steps[st.step].retry):
                        st.press.did_click(now)
                        st.shared_done = False
                        clicks.append((det, f"{place}#{st.id} 手順{st.step + 1} {steps[st.step].label}"))

                # --- shared ボタン（クッキング開始など） ---
                for g, press in shared_press.items():
                    if not dets[g]:
                        press.unseen(now, rearm)
                        continue
                    press.last_seen = now
                    waiters = [st for st in stoves if in_progress(st) and waiting_shared(st)
                               and g in shared_between(st.step, next_local(st.step))]
                    if not waiters and finishing:
                        continue
                    if press.want_click(now, steps[g].retry):
                        press.did_click(now)
                        for st in waiters:
                            st.shared_done = True
                        targets = dets[g] if steps[g].all else dets[g][:1]
                        clicks.extend((d, f"手順{g + 1} {steps[g].label}") for d in targets)

                for det, label in clicks:
                    if not is_running():
                        break
                    _click(ox + det[1], oy + det[2])
                    log(f"{label} をクリック (score={det[0]:.3f})")
                    time.sleep(0.03)

                # 長く見えないコンロは忘れる（カメラが動いた時など）
                for st in [st for st in stoves if now - st.last_seen >= cfg["cooking_lost_timeout"]]:
                    if in_progress(st):
                        log(f"{place}#{st.id} が見えなくなりました")
                    stoves.remove(st)

                if finishing and not any(in_progress(st) for st in stoves):
                    log(f"作りかけの{work}がないので止めます")
                    break

                time.sleep(cfg["cooking_interval"])

            except pyautogui.FailSafeException:
                log("緊急停止しました（マウスが画面の隅に移動しました）")
                break
            except Exception as e:
                log(f"[エラー] {type(e).__name__}: {e}")
                time.sleep(1)

    log(f"{title} 終了")
