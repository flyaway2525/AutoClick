import time
import pyautogui
import cv2
import numpy as np

# ===== 設定 =====
# templates = ["images/button_1.png"]
# templates = ["images/test-left-button.png", "images/test-right-button.png"]
templates = ["images/button_1.png","images/test-left-button.png", "images/test-right-button.png"]
threshold = 0.7 # マッチングの閾値（0.0～1.0）: 値が高いほど厳密に一致する箇所のみを検出
click_interval = 0.2   # クリック間隔（秒）: 各ボタンをクリックした後の待機時間
set_interval = 0.2     # セット間隔（秒）: 全ボタンを1巡した後の待機時間
# ================

pyautogui.PAUSE = 0


def load_templates(log):
    loaded = []
    for path in templates:
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
    loaded_templates = load_templates(log)
    if loaded_templates is None:
        log("[エラー] テンプレート読み込み失敗、クリック処理を開始できません")
        return

    log("クリックループ開始")
    while is_running():
        try:
            screenshot = pyautogui.screenshot()
            screenshot = np.array(screenshot)
            gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

            for name, template, (w, h) in loaded_templates:
                candidates = []

                for scale in np.arange(0.8, 1.21, 0.05):
                    scaled_w = int(w * scale)
                    scaled_h = int(h * scale)
                    if scaled_w < 10 or scaled_h < 10:
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
                        candidates.append((val, cx, cy))

                candidates.sort(reverse=True)
                clicked_positions = []
                click_count = 0
                for val, cx, cy in candidates:
                    if not is_running():
                        break
                    too_close = any(
                        abs(cx - px) < w // 2 and abs(cy - py) < h // 2
                        for px, py in clicked_positions
                    )
                    if too_close:
                        continue
                    clicked_positions.append((cx, cy))
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
