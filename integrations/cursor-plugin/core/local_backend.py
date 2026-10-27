"""Strict loopback transport and configuration for the opt-in local memory service."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LocalBackendError(ValueError):
    pass


@dataclass(frozen=True)
class LocalConfig:
    url: str
    token_file: Path
    user_id: str


def selection() -> tuple[str, LocalConfig | None]:
    override = os.environ.get("MEM0_CODE_BACKEND", "").strip().lower()
    if override and override not in {"local", "platform"}:
        raise LocalBackendError("Unknown MEM0_CODE_BACKEND")
    if override == "platform":
        return "platform", None
    explicit = os.environ.get("MEM0_LOCAL_CONFIG")
    path = Path(explicit).expanduser() if explicit else Path.home() / ".config/mem0-local/config.json"
    if not path.is_file() and not explicit and override != "local":
        return "platform", None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("backend") != "local":
            raise LocalBackendError("Local config must specify backend: local")
        url = raw.get("url")
        if not isinstance(url, str):
            raise LocalBackendError("Local URL is required")
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
        ):
            raise LocalBackendError("Local URL must be plain HTTP loopback with a port")
        token_file = raw.get("token_file")
        user = raw.get("user_id")
        if not isinstance(token_file, str) or not token_file.strip():
            raise LocalBackendError("Local token_file is required")
        if not isinstance(user, str) or not user.strip() or set(user.strip()) == {"*"}:
            raise LocalBackendError("Local user_id is required and cannot be a wildcard")
        return "local", LocalConfig(url.rstrip("/"), Path(token_file).expanduser(), user.strip())
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, LocalBackendError):
            raise
        raise LocalBackendError("Invalid local backend config") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def request(
    config: LocalConfig, endpoint: str, payload: dict[str, Any] | None = None, *, timeout: float = 10
) -> tuple[dict[str, Any], int, int]:
    if endpoint not in {"/health", "/v1/capture", "/v1/search", "/v1/remember", "/v1/supersede"}:
        raise LocalBackendError("Unsupported local endpoint")
    try:
        token = config.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise LocalBackendError("Local token file unavailable") from None
    if not token or "\n" in token or "\r" in token:
        raise LocalBackendError("Invalid local token file")
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        config.url + endpoint,
        data=raw,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if raw is not None else "GET",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(req, timeout=min(max(timeout, 0.1), 120)) as response:
            body = response.read()
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise LocalBackendError("Invalid local service response")
        return parsed, len(raw or b""), len(body)
    except LocalBackendError:
        raise
    except Exception:
        # urllib HTTPError may include the request and Authorization header in its repr.
        raise LocalBackendError("Local service request failed") from None


def identity(config: LocalConfig, repo: Any) -> dict[str, str]:
    explicit = (
        os.environ.get("PLUGIN_OPTION_USER_ID")
        or os.environ.get("CLAUDE_PLUGIN_OPTION_USER_ID")
        or os.environ.get("MEM0_CODE_USER_ID")
        or ""
    ).strip()
    user = explicit or config.user_id
    if not user or set(user) == {"*"} or not repo.project_id or set(repo.project_id) == {"*"}:
        raise LocalBackendError("Invalid local user or project scope")
    return {
        "user_id": user,
        "project_id": repo.project_id,
        "app_id": repo.app_id,
        "directory": repo.cwd,
    }
