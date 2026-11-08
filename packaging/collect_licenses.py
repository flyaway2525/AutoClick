"""exe に同梱したライブラリのライセンス文書を集める（build_exe.bat から呼ぶ）。

使い方: python packaging/collect_licenses.py <出力フォルダ>
requirements.txt のパッケージと、その依存パッケージの LICENSE 類をコピーし、
一覧を THIRD_PARTY_LICENSES.txt にまとめる。
"""
import os
import re
import shutil
import sys
from importlib import metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LICENSE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
# build_exe.bat で --exclude-module しているもの（exe に入らない）
EXCLUDED = {"pymsgbox", "mouseinfo", "pyperclip"}


def requirement_names():
    names = []
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                names.append(re.split(r"[<>=!~\[ ]", line, maxsplit=1)[0])
    return names


def with_dependencies(names):
    seen, queue = {}, list(names)
    while queue:
        name = queue.pop()
        key = name.lower().replace("_", "-")
        if key in seen or key in EXCLUDED:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        for req in dist.requires or []:
            if "extra ==" in req:
                continue
            if ";" in req and "sys_platform" in req and "win32" not in req:
                continue
            queue.append(re.split(r"[<>=!~\[ ;(]", req.strip(), maxsplit=1)[0])
    return seen


def main(out_dir):
    lic_dir = os.path.join(out_dir, "licenses")
    os.makedirs(lic_dir, exist_ok=True)
    lines = [
        "AutoClick に同梱しているソフトウェアとライセンス",
        "=" * 48,
        "",
        "Python (PSF License) - https://www.python.org/",
        "  Python 本体と Tcl/Tk（tkinter）を含みます。",
        "PyInstaller bootloader (GPL-2.0 with bootloader exception) - https://pyinstaller.org/",
        "",
    ]
    for key, dist in sorted(with_dependencies(requirement_names()).items()):
        meta = dist.metadata
        name, version = meta["Name"], dist.version
        license_ = meta.get("License-Expression") or meta.get("License") or ""
        if not license_ or len(license_) > 80:
            classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                           if c.startswith("License ::")]
            license_ = ", ".join(classifiers) or "（同梱のライセンス文書を参照）"
        url = meta.get("Home-page") or ""
        for item in meta.get_all("Project-URL") or []:
            if not url and "," in item:
                url = item.split(",", 1)[1].strip()
        copied = []
        for f in dist.files or []:
            rel = str(f).replace("\\", "/")
            if LICENSE_NAME.search(os.path.basename(rel)) and ".dist-info" in rel:
                src = dist.locate_file(f)
                if os.path.isfile(src):
                    # dist-info 以下のサブフォルダ名も残して、同名ファイルの上書きを防ぐ
                    inner = rel.split(".dist-info/", 1)[1].replace("/", "_")
                    dst_name = f"{name}-{inner}"
                    if dst_name not in copied:
                        shutil.copyfile(src, os.path.join(lic_dir, dst_name))
                        copied.append(dst_name)
        lines.append(f"{name} {version} ({license_}) - {url}")
        for c in copied:
            lines.append(f"  licenses/{c}")
    lines += [
        "",
        "opencv-python の動画用ライブラリ（FFmpeg）は使わないため、exe から取り除いています。",
    ]
    with open(os.path.join(out_dir, "THIRD_PARTY_LICENSES.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"ライセンス文書を集めました: {lic_dir}")


if __name__ == "__main__":
    main(sys.argv[1])
