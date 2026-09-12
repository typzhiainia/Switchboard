# 生成在线更新包（供 CNB Release 附件上传）：
#   dist\update\switchboard-<版本>-src.zip   源码运行方式的更新包
#   dist\update\switchboard-<版本>-win.zip   Windows 打包版的更新包（需先 build.bat）
# 用法: python make_update.py
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "dist" / "update"

SRC_EXCLUDE_DIRS = {".git", ".codebuddy", "dist", "build", "installer-output",
                    "__pycache__", ".venv", "venv", "node_modules"}
SRC_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".db", ".zip"}


def current_version() -> str:
    text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)', text)
    return m.group(1) if m else "0.0.0"


def zip_dir(zf: zipfile.ZipFile, base: Path, exclude_top: set | None = None) -> int:
    n = 0
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(base)
        if exclude_top is not None and rel.parts[0] in exclude_top:
            continue
        if p.suffix.lower() in SRC_EXCLUDE_SUFFIX:
            continue
        zf.write(p, rel.as_posix())
        n += 1
    return n


def make_zip(name: str, base: Path, exclude_top: set | None = None) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / name
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        n = zip_dir(zf, base, exclude_top)
    if n == 0:
        target.unlink(missing_ok=True)
        raise SystemExit(f"[错误] {base} 中没有可打包的文件")
    return target


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ver = current_version()
    print(f"Switchboard v{ver} 更新包生成中...\n")

    src_zip = make_zip(f"switchboard-{ver}-src.zip", ROOT, SRC_EXCLUDE_DIRS)
    print(f"[源码更新包] {src_zip.relative_to(ROOT)}  "
          f"{src_zip.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  SHA256: {sha256(src_zip)}\n")

    win_dir = ROOT / "dist" / "Switchboard"
    if win_dir.is_dir():
        win_zip = make_zip(f"switchboard-{ver}-win.zip", win_dir)
        print(f"[Windows 更新包] {win_zip.relative_to(ROOT)}  "
              f"{win_zip.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"  SHA256: {sha256(win_zip)}\n")
    else:
        print("[Windows 更新包] 跳过：未找到 dist\\Switchboard（先运行 build.bat 打包）\n")

    print("下一步：在 CNB 仓库创建 Release，tag 为 v" + ver + "，")
    print("并将上述 zip 作为附件上传（命名保持 -src.zip / -win.zip 后缀）。")


if __name__ == "__main__":
    sys.exit(main())
