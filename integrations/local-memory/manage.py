#!/usr/bin/env python3
"""Configure and run the personal, loopback-only memory service."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import plistlib
import re
import secrets
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".config/mem0-local/config.json"
LABEL = "com.0genlab.mem0-local"
MARKETPLACE = "0genlab-memory"
LOCAL_PLUGIN_VERSION = "0.3.5"
LABEL_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9]+(?:[A-Za-z0-9-]*[A-Za-z0-9])?)+")


def service_label(config: dict) -> str:
    label = config.get("service_label", LABEL)
    if not isinstance(label, str) or len(label) > 128 or not LABEL_PATTERN.fullmatch(label):
        raise ValueError("Invalid service_label.")
    return label


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Private storage directories cannot be symlinks.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise ValueError("Private storage must belong to the current user.")
    path.chmod(0o700)


def write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("backend") != "local":
        raise ValueError("Expected a local memory configuration.")
    url = urlsplit(config.get("url", ""))
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("The service URL must be http://127.0.0.1:<port>.")
    for name in ("token_file", "data_dir", "python"):
        if not isinstance(config.get(name), str) or not Path(config[name]).is_absolute():
            raise ValueError(f"Configuration requires an absolute {name} path.")
    service_label(config)
    return config


def initialize(path: Path, data_dir: Path, port: int, label: str | None = None) -> dict:
    if path.exists():
        return read_config(path)
    if label is not None:
        service_label({"service_label": label})
    data_dir = data_dir.expanduser().absolute()
    private_directory(data_dir)
    private_directory(path.parent)
    data_dir = data_dir.resolve()
    token_file = path.parent / "token"
    if not token_file.exists():
        write_private(token_file, secrets.token_urlsafe(32).encode("ascii"))
    config = {
        "backend": "local",
        "url": f"http://127.0.0.1:{port}",
        "token_file": str(token_file),
        "user_id": getpass.getuser(),
        "data_dir": str(data_dir),
        "python": sys.executable,
    }
    if label is not None:
        config["service_label"] = label
    write_private(path, (json.dumps(config, indent=2) + "\n").encode("utf-8"))
    return config


def health(config: dict) -> dict:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    token = Path(config["token_file"]).read_text(encoding="utf-8").strip()
    request = Request(config["url"] + "/health", headers={"Authorization": f"Bearer {token}"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
        return json.load(response)


def serve(config: dict) -> None:
    url = urlsplit(config["url"])
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
        raise ValueError("The service URL must be http://127.0.0.1:<port>.")
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "MEM0_TELEMETRY": "false",
    }
    executable = config["python"]
    arguments = [
        executable,
        str(SERVICE_ROOT / "server.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(url.port),
        "--data-dir",
        config["data_dir"],
        "--token-file",
        config["token_file"],
    ]
    os.execve(executable, arguments, environment)


def install_service(path: Path, config: dict) -> Path:
    if sys.platform != "darwin":
        raise ValueError("Automatic service installation currently requires macOS.")
    logs = Path(config["data_dir"]) / "logs"
    label = service_label(config)
    private_directory(logs)
    # A login shell reads the user's existing environment setup without copying
    # model API credentials into a plist or a generated configuration file.
    command = shlex.join([config["python"], str(SERVICE_ROOT / "manage.py"), "--config", str(path), "serve"])
    content = {
        "Label": label,
        "ProgramArguments": ["/bin/zsh", "-lic", "exec " + command],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 15,
        "Umask": 0o077,
        "StandardOutPath": str(logs / "service.log"),
        "StandardErrorPath": str(logs / "service-error.log"),
    }
    target = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    write_private(target, plistlib.dumps(content))
    return target


def start_service(path: Path, config: dict) -> None:
    target = install_service(path, config)
    domain = f"gui/{os.getuid()}"
    label = service_label(config)
    loaded = (
        subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, check=False).returncode == 0
    )
    if not loaded:
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"], check=True)


def prepare_plugins(path: Path, config: dict) -> Path:
    build_root = ROOT / "integrations/agent-plugin-core/build"
    sys.path.insert(0, str(build_root))
    spec = importlib.util.spec_from_file_location("mem0_local_bundle_builder", build_root / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    marketplace = Path(config["data_dir"]) / "plugin-marketplace"
    for host in ("codex", "claude-code"):
        target = marketplace / "plugins" / host
        module.build(host, "native", target)
        manifest_path = target / (".codex-plugin" if host == "codex" else ".claude-plugin") / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["name"] = "mem0-local"
        manifest["version"] = LOCAL_PLUGIN_VERSION
        manifest["description"] = "Personal development memory stored on this computer."
        if host == "claude-code":
            manifest.get("userConfig", {}).pop("api_key", None)
        write_private(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        mcp_path = target / ".mcp.json"
        mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
        server = mcp["mcpServers"].pop("mem0")
        server.setdefault("env", {}).update({"MEM0_LOCAL_CONFIG": str(path), "MEM0_TELEMETRY": "false"})
        mcp["mcpServers"]["mem0-local"] = server
        write_private(mcp_path, (json.dumps(mcp, indent=2) + "\n").encode("utf-8"))
        hooks_path = target / "hooks/hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        prefix = shlex.join(["env", f"MEM0_LOCAL_CONFIG={path}", "MEM0_TELEMETRY=false"])
        for entries in hooks["hooks"].values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    if hook.get("type") == "command":
                        hook["command"] = prefix + " " + hook["command"]
        write_private(hooks_path, (json.dumps(hooks, indent=2) + "\n").encode("utf-8"))
    codex_marketplace = {
        "name": MARKETPLACE,
        "interface": {"displayName": "0genlab Local Memory"},
        "plugins": [
            {
                "name": "mem0-local",
                "source": {"source": "local", "path": "./plugins/codex"},
                "category": "Productivity",
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            }
        ],
    }
    claude_marketplace = {
        "name": MARKETPLACE,
        "owner": {"name": "0genlab"},
        "plugins": [
            {
                "name": "mem0-local",
                "source": "./plugins/claude-code",
                "description": "Personal local development memory.",
            }
        ],
    }
    for relative, value in (
        (".agents/plugins/marketplace.json", codex_marketplace),
        (".claude-plugin/marketplace.json", claude_marketplace),
    ):
        write_private(marketplace / relative, (json.dumps(value, indent=2) + "\n").encode("utf-8"))
    return marketplace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--data-dir", type=Path, default=Path.home() / ".local/share/mem0-local")
    init.add_argument("--port", type=int, default=8769)
    init.add_argument("--service-label")
    for name in ("serve", "start", "status", "prepare-plugins"):
        commands.add_parser(name)
    args = parser.parse_args()
    path = args.config.expanduser().resolve()
    try:
        if args.command == "init":
            if not 1 <= args.port <= 65535:
                parser.error("--port must be in 1..65535")
            config = initialize(path, args.data_dir, args.port, args.service_label)
            print(json.dumps({"config": str(path), "url": config["url"], "data_dir": config["data_dir"]}))
            return 0
        config = read_config(path)
        if args.command == "serve":
            serve(config)
        elif args.command == "start":
            start_service(path, config)
            print("Local memory service started.")
        elif args.command == "status":
            print(json.dumps(health(config), indent=2))
        elif args.command == "prepare-plugins":
            print(prepare_plugins(path, config))
    except Exception as exc:
        print(f"Local memory setup failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
