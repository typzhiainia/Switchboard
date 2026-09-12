# 请求/响应模型
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1)
    api_key: str = ""
    enabled: bool = True
    priority: int = 0
    timeout: float = 60.0
    max_retries: int = Field(default=1, ge=0, le=5)
    weight: int = Field(default=1, ge=1, le=100)
    models: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)


class ProviderOut(ProviderIn):
    id: int
    created_at: float
    last_check_at: Optional[float] = None
    status: str = "unknown"
    latency_ms: Optional[float] = None


class KeyIn(BaseModel):
    name: str = ""
    rpm: int = Field(default=0, ge=0)  # 0 = 不限速


class TestIn(BaseModel):
    provider_id: int
    model: str = Field(..., min_length=1)
    message: str = ""
    system: str = ""
    stream: bool = False


class SettingsIn(BaseModel):
    listen_host: Optional[str] = None
    listen_port: Optional[int] = None
    log_retention_days: Optional[int] = None
    open_browser_on_start: Optional[bool] = None
    circuit_threshold: Optional[int] = Field(default=None, ge=1, le=100)
    circuit_cooldown: Optional[float] = Field(default=None, ge=1, le=86400)
    update_repo: Optional[str] = Field(default=None, max_length=200)
    update_token: Optional[str] = Field(default=None, max_length=200)


class HealthResult(BaseModel):
    ok: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    models: Optional[list[str]] = None
