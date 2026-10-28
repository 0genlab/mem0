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


PROFILE_BANKS = ("personal", "work-profile")


def _config_path() -> Path:
    explicit = os.environ.get("MEM0_LOCAL_CONFIG")
    return Path(explicit).expanduser() if explicit else Path.home() / ".config/mem0-local/config.json"


def _load_config(path: Path) -> tuple[dict[str, Any], LocalConfig]:
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
        return raw, LocalConfig(url.rstrip("/"), Path(token_file).expanduser(), user.strip())
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, LocalBackendError):
            raise
        raise LocalBackendError("Invalid local backend config") from None


def selection() -> tuple[str, LocalConfig | None]:
    override = os.environ.get("MEM0_CODE_BACKEND", "").strip().lower()
    if override and override not in {"local", "platform"}:
        raise LocalBackendError("Unknown MEM0_CODE_BACKEND")
    if override == "platform":
        return "platform", None
    explicit = os.environ.get("MEM0_LOCAL_CONFIG")
    path = _config_path()
    if not path.is_file() and not explicit and override != "local":
        return "platform", None
    return "local", _load_config(path)[1]


def _token(config: LocalConfig) -> str:
    try:
        token = config.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise LocalBackendError("Local token file unavailable") from None
    if not token or "\n" in token or "\r" in token:
        raise LocalBackendError("Invalid local token file")
    return token


def _profile_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "*" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value.encode("utf-8")) > 256
    ):
        raise LocalBackendError("Invalid profile bank identity")
    return value


def _data_dir(raw: dict[str, Any]) -> Path:
    value = raw.get("data_dir")
    if not isinstance(value, str) or not value.strip():
        raise LocalBackendError("Profile data_dir is required")
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise LocalBackendError("Profile data_dir must be an absolute path")
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, LocalBackendError):
            raise
        raise LocalBackendError("Invalid profile data_dir") from None


def profile_route(bank: str) -> tuple[LocalConfig, dict[str, str]]:
    if bank not in PROFILE_BANKS:
        raise LocalBackendError("Unknown memory bank")
    backend, work = selection()
    if backend != "local" or work is None:
        raise LocalBackendError("Profile banks require a local backend")
    raw, _ = _load_config(_config_path())
    banks = raw.get("memory_banks")
    entry = banks.get(bank) if isinstance(banks, dict) else None
    keys = {"project_id", "app_id", "config"} if bank == "personal" else {"project_id", "app_id"}
    if not isinstance(entry, dict) or set(entry) != keys:
        raise LocalBackendError("Profile bank is not configured")
    project_id = _profile_id(entry["project_id"])
    app_id = _profile_id(entry["app_id"])
    personal_entry = banks.get("personal") if isinstance(banks, dict) else None
    personal_path = personal_entry.get("config") if isinstance(personal_entry, dict) else None
    if not isinstance(personal_path, str) or not Path(personal_path).is_absolute():
        raise LocalBackendError("Personal config must be an absolute path")
    personal_raw, personal = _load_config(Path(personal_path))
    if _data_dir(raw) == _data_dir(personal_raw):
        raise LocalBackendError("Profile banks must have distinct data directories")
    config = work
    if bank == "personal":
        config = personal
        if (
            config.url == work.url
            or config.token_file.resolve() == work.token_file.resolve()
            or _token(config) == _token(work)
        ):
            raise LocalBackendError("Personal service must have a distinct URL and token")
    return config, {"user_id": config.user_id, "project_id": project_id, "app_id": app_id}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def request(
    config: LocalConfig, endpoint: str, payload: dict[str, Any] | None = None, *, timeout: float = 10
) -> tuple[dict[str, Any], int, int]:
    if endpoint not in {"/health", "/v1/capture", "/v1/search", "/v1/remember", "/v1/supersede"}:
        raise LocalBackendError("Unsupported local endpoint")
    token = _token(config)
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
