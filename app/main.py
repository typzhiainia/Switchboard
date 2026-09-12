# Switchboard 主程序：管理 API + /v1 代理出口 + 管理界面
# 启动: python -m app.main [--host 127.0.0.1] [--port 8688]
import argparse
import asyncio
import json
import secrets
import sys
import threading
import time
import webbrowser
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, proxy, schemas, updater

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "app" / "static"
if not STATIC_DIR.exists():
    STATIC_DIR = BASE_DIR / "static"

OPENAI_PATHS = {"chat/completions", "completions", "embeddings", "images/generations",
                "models", "responses"}
RATE_WINDOW = defaultdict(deque)  # key_id -> deque[timestamp]
_STATE = {"admin_token": ""}
_SERVER = None  # uvicorn.Server 实例（用于界面优雅停止服务）


# ---- helpers ----

def require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token", "")
    if not _STATE["admin_token"] or token != _STATE["admin_token"]:
        raise HTTPException(status_code=401, detail="管理令牌无效或未提供")


def client_key_of(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Api-Key", "").strip()


def check_rate(key_id: int, rpm: int) -> bool:
    if not rpm:
        return True
    now = time.time()
    dq = RATE_WINDOW[key_id]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= rpm:
        return False
    dq.append(now)
    return True


def mask_key(k: str) -> str:
    return k if len(k) <= 10 else k[:6] + "..." + k[-4:]


# ---- lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    token = db.get_setting("admin_token")
    if not token:
        token = "swb-" + secrets.token_urlsafe(24)
        db.set_setting("admin_token", token)
    _STATE["admin_token"] = token
    if not db.list_providers():
        seed_providers()
    if db.get_setting("first_run", "1") == "1":
        print("\n" + "=" * 56)
        print("  Switchboard 管理界面:  http://127.0.0.1:%s/" % db.get_setting("listen_port", "8688"))
        print("  管理令牌(Admin Token):  %s" % token)
        print("  请妥善保存管理令牌，客户端只需使用本机 API 密钥。")
        print("=" * 56 + "\n")
        db.set_setting("first_run", "0")

    async def janitor():
        while True:
            days = int(db.get_setting("log_retention_days", "30") or 30)
            try:
                db.cleanup_old_logs(max(days, 1))
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(3600)

    task = asyncio.create_task(janitor())
    yield
    task.cancel()


def seed_providers() -> None:
    # 首次启动预置常见上游模板（默认禁用，用户填 key 后启用）
    templates = [
        {"name": "OpenAI", "base_url": "https://api.openai.com",
         "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"]},
        {"name": "DeepSeek", "base_url": "https://api.deepseek.com",
         "models": ["deepseek-chat", "deepseek-reasoner"]},
        {"name": "Moonshot AI", "base_url": "https://api.moonshot.cn",
         "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"]},
        {"name": "Zhipu GLM", "base_url": "https://open.bigmodel.cn/api/paas",
         "models": ["glm-4", "glm-4-plus", "glm-4-flash"]},
        {"name": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
         "models": ["qwen-max", "qwen-plus", "qwen-turbo"]},
    ]
    for t in templates:
        t.update({"enabled": False, "api_key": "", "priority": 0, "max_retries": 1})
        db.create_provider(t)


app = FastAPI(title="Switchboard", version=__version__, lifespan=lifespan)


# ---- admin api ----

@app.get("/api/status")
def api_status(_=Depends(require_admin)):
    return {"version": __version__, "data_dir": str(db.data_dir())}


@app.get("/api/providers")
def api_list_providers(_=Depends(require_admin)):
    out = db.list_providers()
    for p in out:
        p["circuit"] = proxy.CIRCUIT.status_of(p["id"])
    return out


@app.post("/api/providers/{pid}/circuit/reset")
def api_reset_circuit(pid: int, _=Depends(require_admin)):
    if not db.get_provider(pid):
        raise HTTPException(404, "上游不存在")
    proxy.CIRCUIT.record_success(pid)
    return {"ok": True, "circuit": proxy.CIRCUIT.status_of(pid)}


@app.post("/api/providers", status_code=201)
async def api_create_provider(p: schemas.ProviderIn, _=Depends(require_admin)):
    if db.get_provider_by_name(p.name):
        raise HTTPException(409, "同名上游已存在")
    pid = db.create_provider(p.model_dump())
    return db.get_provider(pid)


@app.put("/api/providers/{pid}")
def api_update_provider(pid: int, p: schemas.ProviderIn, _=Depends(require_admin)):
    if not db.get_provider(pid):
        raise HTTPException(404, "上游不存在")
    db.update_provider(pid, p.model_dump())
    return db.get_provider(pid)


@app.post("/api/providers/{pid}/toggle")
def api_toggle_provider(pid: int, _=Depends(require_admin)):
    p = db.get_provider(pid)
    if not p:
        raise HTTPException(404, "上游不存在")
    db.set_provider_enabled(pid, not p["enabled"])
    return db.get_provider(pid)


@app.delete("/api/providers/{pid}")
def api_delete_provider(pid: int, _=Depends(require_admin)):
    if not db.get_provider(pid):
        raise HTTPException(404, "上游不存在")
    db.delete_provider(pid)
    return {"ok": True}


@app.post("/api/providers/{pid}/check")
async def api_check_provider(pid: int, _=Depends(require_admin)):
    p = db.get_provider(pid)
    if not p:
        raise HTTPException(404, "上游不存在")
    return await proxy.check_provider(p)


# ---- api keys ----

@app.get("/api/keys")
def api_list_keys(_=Depends(require_admin)):
    keys = db.list_api_keys()
    for k in keys:
        k["key_masked"] = mask_key(k["key"])
        k.pop("key", None)
    return keys


@app.post("/api/keys", status_code=201)
def api_create_key(k: schemas.KeyIn, _=Depends(require_admin)):
    value = "sk-" + secrets.token_urlsafe(28)
    kid = db.create_api_key(value, k.name, k.rpm)
    row = db.get_api_key(kid)
    return {"id": kid, "key": value, "name": row["name"], "enabled": True,
            "rpm": row["rpm"], "created_at": row["created_at"], "last_used_at": None}


@app.post("/api/keys/{kid}/toggle")
def api_toggle_key(kid: int, _=Depends(require_admin)):
    k = db.get_api_key(kid)
    if not k:
        raise HTTPException(404, "密钥不存在")
    db.set_api_key_enabled(kid, not k["enabled"])
    k["enabled"] = not k["enabled"]
    k["key_masked"] = mask_key(k["key"])
    k.pop("key", None)
    return k


@app.delete("/api/keys/{kid}")
def api_delete_key(kid: int, _=Depends(require_admin)):
    if not db.get_api_key(kid):
        raise HTTPException(404, "密钥不存在")
    db.delete_api_key(kid)
    return {"ok": True}


# ---- logs & stats ----

@app.get("/api/logs")
def api_logs(limit: int = 100, offset: int = 0, model: str = "", status: int = 0,
             provider_id: int = 0, _=Depends(require_admin)):
    limit = min(max(limit, 1), 500)
    return {"total": db.count_logs(model, status, provider_id),
            "items": db.query_logs(limit, offset, model, status, provider_id)}


@app.get("/api/stats")
def api_stats(days: float = 1, _=Depends(require_admin)):
    days = min(max(days, 0.04), 365)
    return db.stats_summary(days)


@app.post("/api/logs/clear")
def api_clear_logs(_=Depends(require_admin)):
    db.clear_logs()
    return {"ok": True}


# ---- settings ----

@app.get("/api/settings")
def api_get_settings(_=Depends(require_admin)):
    return {
        "listen_host": db.get_setting("listen_host", "127.0.0.1"),
        "listen_port": int(db.get_setting("listen_port", "8688")),
        "log_retention_days": int(db.get_setting("log_retention_days", "30")),
        "open_browser_on_start": db.get_setting("open_browser_on_start", "1") == "1",
        "circuit_threshold": int(db.get_setting("circuit_threshold", "3")),
        "circuit_cooldown": float(db.get_setting("circuit_cooldown", "60")),
        "update_repo": db.get_setting("update_repo", ""),
        "update_token_set": bool(db.get_setting("update_token", "")),
        "admin_token": _STATE["admin_token"],
    }


@app.put("/api/settings")
def api_put_settings(s: schemas.SettingsIn, _=Depends(require_admin)):
    d = s.model_dump(exclude_none=True)
    if "listen_port" in d and not (1 <= d["listen_port"] <= 65535):
        raise HTTPException(400, "端口无效")
    if "update_repo" in d:
        repo = d["update_repo"].strip().strip("/").removesuffix(".git")
        if repo and not repo.count("/") == 1:
            raise HTTPException(400, '更新仓库格式应为 "组织/仓库"，如 my-org/switchboard')
        d["update_repo"] = repo
    for k, v in d.items():
        db.set_setting(k, "1" if v is True else "0" if v is False else str(v))
    return api_get_settings()


@app.get("/api/update/check")
async def api_update_check(_=Depends(require_admin)):
    return await updater.check_update()


@app.post("/api/update/apply")
async def api_update_apply(_=Depends(require_admin)):
    try:
        result = await updater.apply_update()
    except updater.UpdateError as e:
        raise HTTPException(400, str(e))
    # 先回响应再停机，由更新助手进程替换文件并重新启动服务
    asyncio.create_task(_shutdown_later(0.5))
    return result


async def _shutdown_later(delay: float) -> None:
    await asyncio.sleep(delay)
    if _SERVER:
        _SERVER.should_exit = True


@app.post("/api/shutdown")
async def api_shutdown(_=Depends(require_admin)):
    # 先回响应再关，不然客户端收不到结果
    asyncio.create_task(_shutdown_later(0.5))
    return {"ok": True, "message": "服务正在停止"}


# ---- model test ----

@app.post("/api/test")
async def api_test_model(t: schemas.TestIn, _=Depends(require_admin)):
    # 管理员直接用上游密钥测模型，不过本地密钥和配额
    p = db.get_provider(t.provider_id)
    if not p:
        raise HTTPException(404, "上游不存在")
    messages = []
    if t.system.strip():
        messages.append({"role": "system", "content": t.system})
    messages.append({"role": "user", "content": t.message})
    body = json.dumps({"model": t.model, "messages": messages,
                       "stream": t.stream}).encode()
    if t.stream:
        return StreamingResponse(_test_stream(p, body), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
    status, _h, content, meta, err = await proxy.forward_once(
        p, "/v1/chat/completions", body, False, {"Content-Type": "application/json"})
    if err:
        return JSONResponse(status_code=502,
                            content={"error": {"message": err, "type": "upstream_error"}})
    if status >= 400:
        return Response(content=content, status_code=status,
                        media_type="application/json")
    reply = ""
    try:
        payload = json.loads(content.decode("utf-8", "replace"))
        reply = payload["choices"][0]["message"].get("content") or ""
    except Exception:  # noqa: BLE001
        reply = content.decode("utf-8", "replace")
    return {"reply": reply,
            "prompt_tokens": meta.get("prompt_tokens"),
            "completion_tokens": meta.get("completion_tokens"),
            "total_tokens": meta.get("total_tokens"),
            "latency_ms": meta.get("latency_ms")}


async def _test_stream(provider, body):
    async for st, headers, chunk, meta, err in proxy.forward_stream(
            provider, "/v1/chat/completions", body):
        if st is not None:
            if st >= 400:
                yield chunk or json.dumps(
                    {"error": {"message": f"HTTP {st}"}}, ensure_ascii=False).encode()
                return
            continue
        if chunk:
            yield chunk
        if err:
            yield ("data: " + json.dumps(
                {"error": {"message": err, "type": "upstream_error"}},
                ensure_ascii=False) + "\n\n").encode()


# ---- proxy ----

def _extract_model(body: bytes) -> str:
    try:
        return str(json.loads(body.decode("utf-8", "replace")).get("model", ""))[:200]
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""


def _json_error(status: int, msg: str, etype: str = "gateway_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": msg, "type": etype, "code": status}},
    )


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def openai_proxy(request: Request, path: str):
    if path not in OPENAI_PATHS and not path.startswith("models"):
        return _json_error(404, f"不支持的端点: /v1/{path}", "invalid_request_error")

    key_value = client_key_of(request)
    if not key_value:
        return _json_error(401, "缺少 API 密钥，请设置 Authorization: Bearer 或 X-Api-Key",
                           "authentication_error")
    key = db.get_api_key_by_value(key_value)
    if not key or not key["enabled"]:
        return _json_error(401, "API 密钥无效或已停用", "authentication_error")
    if not check_rate(key["id"], key.get("rpm") or 0):
        return _json_error(429, "请求过于频繁，请稍后重试", "rate_limit_exceeded")

    endpoint = "/v1/" + path
    body = await request.body()
    if request.method == "GET":
        body = b"" if path == "models" else body

    want_stream = False
    model = ""
    if body:
        try:
            payload = json.loads(body.decode("utf-8", "replace") or "{}")
            want_stream = bool(payload.get("stream"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json_error(400, "请求体不是合法的 JSON", "invalid_request_error")
        model = _extract_model(body)
    if path == "models":
        model = ""

    db.touch_api_key(key["id"])

    if path == "models" and request.method == "GET":
        return await _aggregate_models()

    candidates = _ordered_candidates(model)
    if not candidates:
        return _json_error(
            503,
            f"没有可用上游支持模型 '{model or '(未指定)'}'，请在上游管理中配置",
            "service_unavailable",
        )

    attempts = max(1, min(candidates[0].get("max_retries", 1) + 1, len(candidates)))
    last_error = "上游不可用"
    for provider in candidates[:attempts]:
        if want_stream:
            result = await _do_stream(provider, endpoint, body, key, model)
            if result is not None:
                return result
        else:
            status, headers, content, meta, err = await proxy.forward_once(
                provider, endpoint, body, False, _pass_headers(request))
            if err is None and status is not None and status < 500:
                proxy.CIRCUIT.record_success(provider["id"])
                _write_log(provider, key, model, endpoint, status, meta, stream=False)
                return _downstream_response(status, headers, content)
            proxy.CIRCUIT.record_failure(provider["id"])
            last_error = err or f"HTTP {status}"
        _write_log(provider, key, model, endpoint, 0,
                   {"error": last_error}, stream=want_stream)
    return _json_error(502, f"所有上游均失败: {last_error}", "upstream_error")


def _ordered_candidates(model: str) -> list:
    # 优先级分组 -> 组内过滤熔断的上游再做加权轮询；
    # 整组都熔断时照样返回该组，相当于半开兜底
    cands = db.select_providers_for_model(model)
    groups: dict[int, list] = {}
    for p in cands:
        groups.setdefault(p["priority"], []).append(p)
    ordered = []
    for prio in sorted(groups):
        group = [p for p in groups[prio] if not proxy.CIRCUIT.is_open(p["id"])]
        if not group:
            group = groups[prio]
        ordered.extend(proxy.PICKER.order(group))
    return ordered


def _pass_headers(request: Request) -> dict:
    keep = ("x-request-id", "accept", "content-type", "accept-encoding")
    return {k: v for k, v in request.headers.items() if k.lower() in keep}


def _downstream_response(status: int, headers: dict, content: bytes) -> Response:
    drop = ("content-encoding", "transfer-encoding", "connection",
            "content-length", "authorization")
    h = {k: v for k, v in headers.items() if k.lower() not in drop}
    return Response(content=content, status_code=status, headers=h,
                    media_type=h.get("content-type", "application/json"))


def _write_log(provider, key, model, endpoint, status, meta, stream, err_override=None):
    try:
        db.insert_log({
            "provider_id": provider["id"] if provider else None,
            "provider_name": provider["name"] if provider else None,
            "model": model, "endpoint": endpoint, "status": status,
            "latency_ms": (meta or {}).get("latency_ms"),
            "prompt_tokens": (meta or {}).get("prompt_tokens"),
            "completion_tokens": (meta or {}).get("completion_tokens"),
            "total_tokens": (meta or {}).get("total_tokens"),
            "error": err_override or (meta or {}).get("error"),
            "api_key_id": key["id"] if key else None,
            "api_key_name": key.get("name") if key else None,
            "stream": stream,
        })
    except Exception:  # noqa: BLE001 日志挂了不影响请求
        pass


async def _do_stream(provider, endpoint, body, key, model):
    # 流式转发；返回 None 表示该上游连不上，换下一个

    async def gen():
        sent = False
        logged = False
        final_meta: dict = {}
        async for st, headers, chunk, meta, err in proxy.forward_stream(
                provider, endpoint, body):
            if st is not None:  # 首元信息
                if st >= 400:
                    proxy.CIRCUIT.record_failure(provider["id"])
                    payload = (chunk or b"") or json.dumps(
                        {"error": {"message": err or f"HTTP {st}", "code": st}}).encode()
                    yield _downstream_response(st, headers or {}, payload).body
                    _write_log(provider, key, model, endpoint, st,
                               {"latency_ms": (meta or {}).get("latency_ms")},
                               stream=True, err_override=err)
                    return
                proxy.CIRCUIT.record_success(provider["id"])
                continue
            if chunk:
                sent = True
                yield chunk
            if err:
                proxy.CIRCUIT.record_failure(provider["id"])
                _write_log(provider, key, model, endpoint, 0, meta or {},
                           stream=True, err_override=err)
                logged = True
                if not sent:
                    yield ("data: " + json.dumps(
                        {"error": {"message": err, "type": "upstream_error"}},
                        ensure_ascii=False) + "\n\n").encode()
                    sent = True
            elif meta is not None:
                final_meta = meta
        if not logged:
            _write_log(provider, key, model, endpoint, 200, final_meta, stream=True)
        if not sent:
            yield b'data: [DONE]\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def _aggregate_models():
    # 聚合所有启用上游的模型，OpenAI 格式
    seen, data = set(), []
    for p in db.list_providers(only_enabled=True):
        r = await proxy.check_provider(p)
        if not r.get("ok"):
            continue
        for m in r.get("models") or []:
            if m not in seen:
                seen.add(m)
                data.append({"id": m, "object": "model", "created": 0, "owned_by": p["name"]})
    if not data:
        for p in db.list_providers(only_enabled=True):
            for m in p.get("models") or []:
                if m != "*" and m not in seen:
                    seen.add(m)
                    data.append({"id": m, "object": "model", "created": 0, "owned_by": p["name"]})
    return {"object": "list", "data": data}


# ---- static ----

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---- entry ----

def main() -> None:
    parser = argparse.ArgumentParser(description="Switchboard 本地大模型 API 网关")
    parser.add_argument("--host", default=None, help="监听地址(默认取配置或 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="监听端口(默认取配置或 8688)")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args()

    db.init_db()
    host = args.host or db.get_setting("listen_host", "127.0.0.1")
    port = args.port or int(db.get_setting("listen_port", "8688"))
    db.set_setting("listen_host", host)
    db.set_setting("listen_port", str(port))

    open_browser = (not args.no_browser
                    and db.get_setting("open_browser_on_start", "1") == "1")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}/")).start()

    global _SERVER
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    _SERVER = uvicorn.Server(config)
    _SERVER.run()


if __name__ == "__main__":
    main()
