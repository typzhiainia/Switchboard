# 转发引擎：健康检查、负载均衡、熔断、SSE 流式透传
import json
import time

import httpx

from . import db

USER_AGENT = "LLM-Gateway/1.0"
OPENAI_ENDPOINTS = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
    "/v1/images/generations",
    "/v1/models",
    "/v1/responses",
)

_client: httpx.AsyncClient | None = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
    return _client


def auth_headers(provider: dict, extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {provider.get('api_key', '')}", "User-Agent": USER_AGENT}
    for k, v in (extra or {}).items():
        if k.lower() not in ("host", "content-length", "connection", "authorization"):
            h[k] = v
    return h


def join_url(base: str, endpoint: str) -> str:
    # base 可能带 /v1 结尾（如 token.sensenova.cn/v1），避免拼出 /v1/v1/...
    base = base.rstrip("/")
    if base.endswith("/v1") and endpoint.startswith("/v1"):
        return base + endpoint[3:]
    return base + endpoint


class CircuitBreaker:
    # 连续失败达到阈值就熔断该上游，冷却期内不参与选路，
    # 冷却后自动半开试探，成功即恢复。阈值/秒数读设置，改完即生效。

    def __init__(self):
        self._state: dict[int, dict] = {}

    @staticmethod
    def _config() -> tuple[int, float]:
        try:
            threshold = int(db.get_setting("circuit_threshold", "3") or 3)
            cooldown = float(db.get_setting("circuit_cooldown", "60") or 60)
        except ValueError:
            threshold, cooldown = 3, 60.0
        return max(1, threshold), max(1.0, cooldown)

    def is_open(self, pid: int) -> bool:
        st = self._state.get(pid)
        return bool(st and st["open_until"] > time.time())

    def record_success(self, pid: int) -> None:
        self._state.pop(pid, None)

    def record_failure(self, pid: int) -> None:
        threshold, cooldown = self._config()
        st = self._state.setdefault(pid, {"fails": 0, "open_until": 0.0})
        st["fails"] += 1
        if st["fails"] >= threshold:
            st["open_until"] = time.time() + cooldown
            st["fails"] = 0

    def status_of(self, pid: int) -> dict:
        st = self._state.get(pid) or {"fails": 0, "open_until": 0.0}
        remaining = max(0, round(st["open_until"] - time.time()))
        return {"open": remaining > 0, "fails": st["fails"], "remaining": remaining}


CIRCUIT = CircuitBreaker()


class WeightedPicker:
    # 平滑加权轮询：权重 2:1 时调度序列是 A B A，不会连续打到同一台

    def __init__(self):
        self._current: dict[int, int] = {}

    def order(self, providers: list[dict]) -> list[dict]:
        # 首位是本轮选中的，其余按权重降序做故障转移候补
        if not providers:
            return []
        total = sum(max(p.get("weight") or 1, 1) for p in providers)
        best, best_cw = None, None
        for p in providers:
            w = max(p.get("weight") or 1, 1)
            cw = self._current.get(p["id"], 0) + w
            self._current[p["id"]] = cw
            if best is None or cw > best_cw:
                best, best_cw = p, cw
        self._current[best["id"]] = best_cw - total
        rest = [p for p in providers if p is not best]
        rest.sort(key=lambda p: -(p.get("weight") or 1))
        return [best] + rest


PICKER = WeightedPicker()


async def check_provider(provider: dict) -> dict:
    # GET /v1/models 探测，顺便把模型列表带回来
    url = join_url(provider["base_url"], "/v1/models")
    t0 = time.time()
    try:
        r = await http_client().get(
            url, headers=auth_headers(provider),
            timeout=httpx.Timeout(provider.get("timeout") or 15.0, connect=8.0),
        )
        latency = round((time.time() - t0) * 1000, 1)
        if r.status_code < 400:
            models = []
            try:
                data = r.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            except (json.JSONDecodeError, AttributeError):
                pass
            db.set_provider_health(provider["id"], "healthy", latency)
            CIRCUIT.record_success(provider["id"])
            return {"ok": True, "latency_ms": latency, "models": models}
        db.set_provider_health(provider["id"], "unhealthy", latency)
        CIRCUIT.record_failure(provider["id"])
        return {"ok": False, "latency_ms": latency, "error": f"HTTP {r.status_code}"}
    except Exception as e:  # noqa: BLE001
        latency = round((time.time() - t0) * 1000, 1)
        db.set_provider_health(provider["id"], "unhealthy", latency)
        CIRCUIT.record_failure(provider["id"])
        return {"ok": False, "latency_ms": latency, "error": str(e)[:300]}


def _usage_of(payload: dict) -> dict:
    u = (payload or {}).get("usage") or {}
    pt, ct = u.get("prompt_tokens"), u.get("completion_tokens")
    return {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": u.get("total_tokens") or ((pt or 0) + (ct or 0) if pt is not None or ct is not None else None),
    }


async def forward_once(provider: dict, endpoint: str, body: bytes, stream: bool,
                       extra_headers: dict | None = None):
    # 返回 (status, headers, content, meta, error)
    url = join_url(provider["base_url"], endpoint)
    headers = auth_headers(provider, extra_headers)
    t0 = time.time()
    try:
        r = await http_client().post(
            url, content=body, headers=headers,
            timeout=httpx.Timeout(provider.get("timeout") or 60.0, connect=10.0),
        )
    except Exception as e:  # noqa: BLE001
        return None, None, None, None, str(e)[:300]
    latency = round((time.time() - t0) * 1000, 1)
    content = r.content
    usage, payload = {}, None
    if not stream and r.status_code < 400:
        try:
            payload = json.loads(content.decode("utf-8", "replace"))
            usage = _usage_of(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    return r.status_code, dict(r.headers), content, {"latency_ms": latency, **usage}, None


async def forward_stream(provider: dict, endpoint: str, body: bytes,
                         extra_headers: dict | None = None):
    # SSE 透传；每步 yield (status, headers, chunk, meta, error)，五元组里只有一个有值
    url = join_url(provider["base_url"], endpoint)
    headers = auth_headers(provider, extra_headers)
    t0 = time.time()
    usage: dict = {}
    try:
        async with http_client().stream(
            "POST", url, content=body, headers=headers,
            timeout=httpx.Timeout(provider.get("timeout") or 600.0, connect=10.0),
        ) as r:
            if r.status_code >= 400:
                content = await r.aread()
                yield r.status_code, dict(r.headers), bytes(content), {
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                }, content.decode("utf-8", "replace")[:300]
                return
            latency = round((time.time() - t0) * 1000, 1)
            base = {"latency_ms": latency}
            yield r.status_code, dict(r.headers), None, base, None
            async for chunk in r.aiter_bytes():
                yield None, None, chunk, None, None
                if chunk:
                    usage = _extract_usage_from_chunk(chunk, usage)
            final = {**base, **usage}
            yield None, None, None, final, None
    except Exception as e:  # noqa: BLE001
        yield None, None, None, {
            "latency_ms": round((time.time() - t0) * 1000, 1), **usage,
        }, str(e)[:300]


def _extract_usage_from_chunk(chunk: bytes, acc: dict) -> dict:
    # OpenAI 流末尾会带 usage 字段，顺路抠出来
    try:
        text = chunk.decode("utf-8", "replace")
    except UnicodeDecodeError:
        return acc
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        u = obj.get("usage")
        if u:
            pt, ct = u.get("prompt_tokens"), u.get("completion_tokens")
            acc = {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": u.get("total_tokens") or ((pt or 0) + (ct or 0)),
            }
    return acc
