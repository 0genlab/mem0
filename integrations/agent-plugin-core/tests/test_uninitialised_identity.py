"""Core identity behaviour with NO configure_harness(), in a real subprocess.

Each built host bundle must know what it is from its generated ``_harness_id``
alone, and the platform headers must carry that identity. These tests run in a
fresh interpreter with no conftest, against a built host bundle, which is the
only arrangement that catches an entrypoint forgetting to configure itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = CORE_ROOT.parents[1]
HOSTS = {
    "claude-code": ("claude-code-plugin", "CLAUDE_CODE_PLUGIN"),
    "cursor": ("cursor-plugin", "CURSOR_PLUGIN"),
    "codex": ("codex-plugin", "CODEX_PLUGIN"),
    "kimi": ("kimi-plugin", "KIMI_PLUGIN"),
    "antigravity": ("antigravity-plugin", "ANTIGRAVITY_PLUGIN"),
    "coding-agent": ("mem0-agent-plugin", "CODING_AGENT_PLUGIN"),
}


def _core_dir(directory: str) -> Path:
    return REPOSITORY_ROOT / "integrations" / directory / "core"


def _run(core: Path, data_dir: Path, body: str) -> str:
    """Execute `body` in a fresh interpreter with only the host's core on sys.path."""
    script = f"import sys; sys.path.insert(0, {str(core)!r})\n{body}"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={
            "MEM0_CODE_DATA_DIR": str(data_dir),
            "PATH": "/usr/bin:/bin",
            "HOME": str(data_dir),
        },
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("harness,spec", sorted(HOSTS.items()))
def test_identity_resolves_without_configuration(harness, spec):
    """Every built host knows what it is with no configuration call at all."""
    directory, source_tag = spec
    core = _core_dir(directory)
    if not core.exists():
        pytest.skip(f"{directory} is not built in this tree")

    with tempfile.TemporaryDirectory() as tmp:
        out = _run(
            core,
            Path(tmp),
            "import _harness_id; print(_harness_id.HARNESS_ID, _harness_id.SOURCE_TAG)",
        )
    assert out == f"{harness} {source_tag}"


def test_the_plugin_declares_its_surface_in_the_body_and_the_headers():
    """Body and headers both, because only the body works on every backend."""
    core = _core_dir("claude-code-plugin")
    if not core.exists():
        pytest.skip("claude-code-plugin is not built in this tree")

    with tempfile.TemporaryDirectory() as tmp:
        out = _run(
            core,
            Path(tmp),
            "import json, memory_core\n"
            "h = memory_core.platform_headers('k')\n"
            "print(json.dumps({'source': h.get('X-Mem0-Source'),"
            " 'app': h.get('X-Application'),"
            " 'client': h.get('X-Mem0-Client'),"
            " 'auth': h.get('Authorization'),"
            " 'ctype': h.get('Content-Type')}))",
        )
    headers = json.loads(out)
    assert headers["source"] == "MEM0_PLUGIN"
    assert headers["app"] == "claude-code"
    assert headers["client"].startswith("mem0-plugin/")
    # The transport headers the three call sites relied on must survive.
    assert headers["auth"] == "Token k"
    assert headers["ctype"] == "application/json"
