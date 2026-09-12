# 在线更新：以 CNB Release 为更新源，检查、下载、校验并在本地应用更新后重启服务
# 更新源配置（设置页填写，存于本地数据库 settings 表）：
#   update_repo  - CNB 仓库名，格式 "组织/仓库"，如 "my-org/switchboard"
#   update_token - CNB 访问令牌（Open API 调用必需，https://cnb.cool 个人设置中创建）
# Release 约定：
#   tag 形如 v1.2.3（版本号去掉 v 与本地 __version__ 比较）
#   附件命名 switchboard-<版本>-src.zip（源码运行）/ switchboard-<版本>-win.zip（Windows 打包）
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx

from . import __version__, db

API_BASE = "https://api.cnb.cool"
HTTP_TIMEOUT = 20.0
DOWNLOAD_TIMEOUT = 120.0
NOTES_MAX = 4000


class UpdateError(Exception):
    pass


def version_tuple(v: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", v or "")]
    return tuple(nums) if nums else (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def install_mode() -> str:
    # windows = PyInstaller 打包运行；source = 源码运行（python -m app.main）
    return "windows" if getattr(sys, "frozen", False) else "source"


def _repo_and_token() -> tuple:
    repo = db.get_setting("update_repo", "").strip().strip("/").removesuffix(".git")
    token = db.get_setting("update_token", "").strip()
    return repo, token


def _auth_headers(token: str) -> dict:
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _pick_asset(assets: list, mode: str) -> dict | None:
    zips = [a for a in assets if (a.get("name") or "").lower().endswith(".zip")]
    if not zips:
        return None
    want = "-win" if mode == "windows" else "-src"
    for a in zips:  # 优先匹配当前安装方式的附件
        if want in (a.get("name") or "").lower():
            return _asset_info(a)
    return _asset_info(zips[0])


def _asset_info(a: dict) -> dict:
    algo = (a.get("hash_algo") or "").lower()
    return {
        "name": a.get("name") or "",
        "size": a.get("size") or 0,
        "url": a.get("browser_download_url") or a.get("url") or "",
        "sha256": (a.get("hash_value") or "").lower() if algo in ("sha256", "") else "",
    }


async def check_update() -> dict:
    repo, token = _repo_and_token()
    out = {"current_version": __version__, "mode": install_mode(),
           "configured": bool(repo), "update_available": False,
           "latest": None, "error": None}
    if not repo:
        out["error"] = "尚未配置更新仓库"
        return out
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as cli:
            r = await cli.get(f"{API_BASE}/{repo}/-/releases/latest", headers=_auth_headers(token))
    except httpx.HTTPError as e:
        out["error"] = f"无法连接 CNB：{e}"
        return out
    if r.status_code == 404:
        out["error"] = "该仓库还没有发布过 Release"
        return out
    if r.status_code in (401, 403):
        out["error"] = "访问被拒绝，请检查更新仓库名与访问令牌（令牌在 cnb.cool 个人设置中创建）"
        return out
    if r.status_code != 200:
        out["error"] = f"CNB 接口返回 HTTP {r.status_code}"
        return out

    rel = r.json()
    tag = (rel.get("tag_name") or "").strip()
    version = tag.lstrip("vV") or (rel.get("name") or "").strip()
    asset = _pick_asset(rel.get("assets") or [], out["mode"])
    out["latest"] = {
        "version": version, "tag": tag,
        "name": rel.get("name") or "",
        "notes": (rel.get("body") or "")[:NOTES_MAX],
        "published_at": rel.get("published_at") or rel.get("created_at") or "",
        "asset": asset,
    }
    out["update_available"] = bool(asset) and is_newer(version, __version__)
    if not asset:
        out["error"] = "最新 Release 中没有可用的 zip 附件（应为 -src.zip 或 -win.zip）"
    return out


def updates_dir() -> Path:
    d = db.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def download_asset(asset: dict) -> Path:
    if not asset.get("url"):
        raise UpdateError("更新包缺少下载地址")
    upd = updates_dir()
    for old in upd.glob("*.zip"):  # 清理旧下载
        old.unlink(missing_ok=True)
    target = upd / Path(asset["name"] or "update.zip").name  # 仅取文件名，防路径逃逸
    sha = hashlib.sha256()
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as cli:
            async with cli.stream("GET", asset["url"]) as r:
                if r.status_code != 200:
                    raise UpdateError(f"下载更新包失败：HTTP {r.status_code}")
                with open(target, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        f.write(chunk)
                        sha.update(chunk)
    except httpx.HTTPError as e:
        target.unlink(missing_ok=True)
        raise UpdateError(f"下载更新包失败：{e}")
    if asset.get("sha256") and sha.hexdigest() != asset["sha256"]:
        target.unlink(missing_ok=True)
        raise UpdateError("更新包 SHA256 校验不一致，已放弃本次更新")
    return target


def extract_package(zip_path: Path) -> Path:
    staging = zip_path.parent / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)  # zipfile 会过滤绝对路径与 .. 成员
    except zipfile.BadZipFile:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("更新包不是合法的 zip 文件")
    if not any(staging.iterdir()):
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError("更新包内容为空")
    return staging


def target_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent  # dist/Switchboard 或安装目录
    return Path(__file__).resolve().parent.parent      # 源码安装：app/ 的上一级


def _probe_writable(d: Path) -> bool:
    try:
        p = d / ".swb_update_probe"
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return True
    except OSError:
        return False


def _relaunch_cmd(target: Path) -> list:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    return [sys.executable, "-m", "app.main"]


def _write_helper(helper: Path, staging: Path, target: Path, relaunch: list) -> None:
    pid = os.getpid()
    if os.name == "nt":
        # 等原进程退出 -> 覆盖文件 -> 清理 -> 重启 -> 自删除
        bat = "\r\n".join([
            "@echo off",
            "chcp 65001 >nul",
            "title Switchboard Updater",
            f':wait',
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
            "if not errorlevel 1 (timeout /t 1 >nul & goto wait)",
            "timeout /t 1 >nul",
            f'xcopy /E /I /Y /Q "{staging}\\*" "{target}\\"',
            "if errorlevel 1 (",
            '  echo [更新] 文件替换失败，请手动将解压后的文件复制到安装目录。',
            "  pause",
            "  exit /b 1",
            ")",
        ])
        if getattr(sys, "frozen", False):
            bat += f'\r\nstart "" "{relaunch[0]}"'
        else:
            bat += f'\r\nstart "Switchboard" /d "{target}" "{relaunch[0]}" -m app.main'
        bat += '\r\ndel "%~f0"\r\nexit /b 0\r\n'
        helper.write_text(bat, encoding="utf-8")
    else:
        lines = [
            "#!/bin/sh",
            f"while kill -0 {pid} 2>/dev/null; do sleep 1; done",
            "sleep 1",
            f'cp -rf "{staging}/." "{target}/"',
            f'rm -rf "{staging.parent}"',
        ]
        if getattr(sys, "frozen", False):
            lines.append(f'"{relaunch[0]}" &')
        else:
            lines.append(f'cd "{target}" && nohup "{relaunch[0]}" -m app.main >/dev/null 2>&1 &')
        lines.append(f'rm -f "{helper}"')
        helper.write_text("\n".join(lines) + "\n", encoding="utf-8")
        helper.chmod(0o755)


def _spawn_helper(helper: Path) -> None:
    try:
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_CONSOLE  # 独立窗口，用户可见更新进度
            subprocess.Popen(["cmd", "/c", str(helper)], cwd=str(helper.parent),
                             creationflags=flags, close_fds=True)
        else:
            subprocess.Popen(["/bin/sh", str(helper)], cwd=str(helper.parent),
                             start_new_session=True, close_fds=True)
    except OSError as e:
        raise UpdateError(f"无法启动更新助手进程：{e}")


async def apply_update() -> dict:
    info = await check_update()
    if not info.get("update_available"):
        raise UpdateError(info.get("error") or "当前已是最新版本，无需更新")

    upd = updates_dir()
    shutil.rmtree(upd / "staging", ignore_errors=True)  # 清理上次更新的残留
    for h in (upd / "update.bat", upd / "update.sh"):
        h.unlink(missing_ok=True)

    staging = extract_package(await download_asset(info["latest"]["asset"]))

    target = target_dir()
    if not _probe_writable(target):
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"安装目录不可写：{target}（若安装在 Program Files，请改用安装包升级）")

    helper = updates_dir() / ("update.bat" if os.name == "nt" else "update.sh")
    _write_helper(helper, staging, target, _relaunch_cmd(target))
    _spawn_helper(helper)
    return {"ok": True, "version": info["latest"]["version"],
            "message": "更新包已就绪，服务正在重启以完成升级"}
