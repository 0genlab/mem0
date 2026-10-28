from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import local_backend  # noqa: E402
import mcp_server  # noqa: E402
import memory_core  # noqa: E402


@pytest.fixture
def banks(tmp_path, monkeypatch):
    work_token = tmp_path / "work-token"
    personal_token = tmp_path / "personal-token"
    work_token.write_text("work-secret")
    personal_token.write_text("personal-secret")
    personal_path = tmp_path / "personal.json"
    personal = {
        "backend": "local",
        "url": "http://127.0.0.1:8770",
        "token_file": str(personal_token),
        "user_id": "test-user",
        "data_dir": str(tmp_path / "personal-data"),
    }
    personal_path.write_text(json.dumps(personal))
    work_path = tmp_path / "work.json"
    work = {
        "backend": "local",
        "url": "http://127.0.0.1:8769",
        "token_file": str(work_token),
        "user_id": "test-user",
        "data_dir": str(tmp_path / "work-data"),
        "memory_banks": {
            "personal": {
                "config": str(personal_path),
                "project_id": "personal-profile",
                "app_id": "personal-profile",
            },
            "work-profile": {
                "project_id": "work-profile",
                "app_id": "work-profile",
            },
            "investigate": {
                "project_id": "investigate-cases",
                "app_id": "investigate-cases",
            },
        },
    }
    work_path.write_text(json.dumps(work))
    monkeypatch.setenv("MEM0_LOCAL_CONFIG", str(work_path))
    monkeypatch.setenv("MEM0_CODE_DATA_DIR", str(tmp_path / "spool"))
    monkeypatch.delenv("MEM0_CODE_BACKEND", raising=False)
    return work_path, work, personal_path, personal, tmp_path / "spool"


def call(name, arguments, cwd=None):
    return (
        mcp_server.handle_request({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})[
            "result"
        ]
        if cwd is None
        else (
            mcp_server.call_search_memories(arguments, cwd)
            if name == "search_memories"
            else mcp_server._local_tool(name, arguments, cwd)
        )
    )


def test_schema_and_project_default_are_compatible(banks, monkeypatch):
    tools = mcp_server.handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]
    assert len(tools) == 3
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema["properties"]["bank"]["enum"] == ["project", "personal", "work-profile", "investigate"]
        assert "bank" not in schema["required"]
        assert schema["additionalProperties"] is False

    repo = memory_core.RepoContext("/tmp/repo/sub", "/tmp/repo", "local:/tmp/repo", "repo", "main", "", "p", "sub")
    monkeypatch.setattr(mcp_server, "resolve_repo", lambda cwd: repo)
    search = Mock(return_value=memory_core.MemorySearchResult(True, 0, 0, []))
    monkeypatch.setattr(mcp_server, "search_memories", search)
    assert call("search_memories", {"query": "question"})["isError"] is False
    assert search.call_args.args[1] is repo
    assert search.call_args.kwargs["scope"] is None

    seen = []
    monkeypatch.setattr(
        local_backend,
        "request",
        lambda config, endpoint, payload, **kw: (seen.append((config, endpoint, payload)) or {"id": "1"}, 0, 0),
    )
    assert (
        call(
            "remember_memory",
            {
                "text": "lesson",
                "kind": "rule",
                "scope": "project",
                "triggers": [],
            },
        )["isError"]
        is False
    )
    assert seen[-1][2]["project_id"] == "p"
    assert seen[-1][2]["directory"] == "/tmp/repo/sub"
    assert "bank" not in seen[-1][2]


def test_profiles_route_without_cwd_or_spool(banks, monkeypatch):
    _, _, _, _, spool = banks
    seen = []

    def request(config, endpoint, payload, **kwargs):
        seen.append((config.url, endpoint, payload))
        return (
            (
                {"results": [{"id": "1", "memory": "lesson", "metadata": {}}]}
                if endpoint == "/v1/search"
                else {"id": "2"}
            ),
            0,
            0,
        )

    monkeypatch.setattr(local_backend, "request", request)
    monkeypatch.setattr(mcp_server, "resolve_repo", lambda cwd: pytest.fail("profile resolved cwd"))
    for bank, project, url in (
        ("personal", "personal-profile", "http://127.0.0.1:8770"),
        ("work-profile", "work-profile", "http://127.0.0.1:8769"),
        ("investigate", "investigate-cases", "http://127.0.0.1:8769"),
    ):
        for cwd in ("/tmp/repo/sub", "/tmp/another"):
            assert "lesson" in call("search_memories", {"query": "lesson", "bank": bank}, cwd)
            assert call(
                "remember_memory",
                {
                    "text": "lesson",
                    "kind": "rule",
                    "scope": "personal" if bank == "personal" else "project",
                    "triggers": [],
                    "bank": bank,
                },
                cwd,
            )
            assert call("supersede_memory", {"old_id": "1", "new_id": "2", "bank": bank}, cwd)
        entries = [item for item in seen if item[2]["project_id"] == project]
        assert len(entries) == 6
        assert all(item[0] == url for item in entries)
        assert all(item[2]["app_id"] == project and item[2]["user_id"] == "test-user" for item in entries)
        assert all("directory" not in item[2] and "bank" not in item[2] for item in entries)
        assert entries[0][2]["scope"] == "repo"
    assert not spool.exists()


def test_profile_identity_comes_from_trusted_config_across_cwd(banks, monkeypatch):
    work_path, work, _, _, _ = banks
    work["memory_banks"]["personal"]["project_id"] = "configured-personal"
    work["memory_banks"]["personal"]["app_id"] = "configured-personal-app"
    work["memory_banks"]["work-profile"]["project_id"] = "configured-work"
    work["memory_banks"]["work-profile"]["app_id"] = "configured-work-app"
    work_path.write_text(json.dumps(work))
    seen = []
    monkeypatch.setattr(
        local_backend,
        "request",
        lambda config, endpoint, payload, **kw: (seen.append(payload) or {"results": []}, 0, 0),
    )
    monkeypatch.setattr(mcp_server, "resolve_repo", lambda cwd: pytest.fail("profile resolved cwd"))
    for bank, project, app in (
        ("personal", "configured-personal", "configured-personal-app"),
        ("work-profile", "configured-work", "configured-work-app"),
    ):
        for cwd in ("/tmp/one", "/tmp/two/sub"):
            assert call("search_memories", {"query": "q", "bank": bank}, cwd) == "No matching memories found."
        assert [payload["project_id"] for payload in seen[-2:]] == [project, project]
        assert [payload["app_id"] for payload in seen[-2:]] == [app, app]
        assert all("directory" not in payload for payload in seen[-2:])


def test_profile_search_filters_and_scope_validation(banks, monkeypatch):
    seen = []
    monkeypatch.setattr(
        local_backend,
        "request",
        lambda config, endpoint, payload, **kw: (seen.append(payload) or {"results": []}, 0, 0),
    )
    assert (
        call(
            "search_memories",
            {
                "query": "q",
                "bank": "personal",
                "scope": "mine",
                "top_k": 4,
                "category": "workflows",
                "run_id": "session",
            },
        )["isError"]
        is False
    )
    assert seen[-1]["scope"] == "mine"
    assert seen[-1]["top_k"] == 4
    assert seen[-1]["run_id"] == "session"
    assert seen[-1]["category"] == "workflows"
    assert call("search_memories", {"query": "q", "bank": "personal", "scope": "dir"})["isError"]
    assert call(
        "remember_memory",
        {
            "text": "q",
            "bank": "personal",
            "kind": "rule",
            "scope": "project",
            "triggers": [],
        },
    )["isError"]
    assert len(seen) == 1


@pytest.mark.parametrize("bad", [None, "", "PERSONAL", "other", 1, {}, ["project"]])
def test_invalid_bank_never_routes(banks, monkeypatch, bad):
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: pytest.fail("project search called"))
    for name, args in (
        ("search_memories", {"query": "q"}),
        ("remember_memory", {"text": "q", "kind": "rule", "scope": "personal", "triggers": []}),
        ("supersede_memory", {"old_id": "1", "new_id": "2"}),
    ):
        assert call(name, {**args, "bank": bad})["isError"]
    assert call("search_memories", {"query": "q", "bank": "personal", "url": "http://127.0.0.1:9999"})["isError"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "invalid-id",
        "relative-path",
        "invalid-config",
        "same-url",
        "same-token-path",
        "same-token",
    ],
)
def test_personal_config_fails_closed(banks, monkeypatch, mutation):
    work_path, work, personal_path, personal, _ = banks
    if mutation == "missing":
        del work["memory_banks"]["personal"]
    elif mutation == "invalid-id":
        work["memory_banks"]["personal"]["project_id"] = "*"
    elif mutation == "relative-path":
        work["memory_banks"]["personal"]["config"] = "personal.json"
    elif mutation == "invalid-config":
        personal_path.write_text("{invalid")
    elif mutation == "same-url":
        personal["url"] = work["url"]
    elif mutation == "same-token-path":
        personal["token_file"] = work["token_file"]
    elif mutation == "same-token":
        Path(personal["token_file"]).write_text("work-secret")
    work_path.write_text(json.dumps(work))
    if mutation in {"same-url", "same-token-path"}:
        personal_path.write_text(json.dumps(personal))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: pytest.fail("project fallback"))
    assert call("search_memories", {"query": "q", "bank": "personal"})["isError"]
    assert call("supersede_memory", {"old_id": "1", "new_id": "2", "bank": "personal"})["isError"]


@pytest.mark.parametrize("bank", ["work-profile", "investigate"])
@pytest.mark.parametrize("mutation", ["missing", "invalid-id"])
def test_work_profile_config_fails_closed(banks, monkeypatch, mutation, bank):
    work_path, work, _, _, _ = banks
    if mutation == "missing":
        del work["memory_banks"][bank]
    else:
        work["memory_banks"][bank]["app_id"] = "*"
    work_path.write_text(json.dumps(work))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: pytest.fail("project fallback"))
    assert call("search_memories", {"query": "q", "bank": bank})["isError"]
    assert call(
        "remember_memory",
        {
            "text": "q",
            "kind": "rule",
            "scope": "project",
            "triggers": [],
            "bank": bank,
        },
    )["isError"]


@pytest.mark.parametrize("bank", ["personal", "work-profile", "investigate"])
@pytest.mark.parametrize("invalid", [None, "", "relative/data", "\x00"])
@pytest.mark.parametrize("config_name", ["personal", "work"])
def test_profile_data_dir_required(banks, monkeypatch, bank, config_name, invalid):
    work_path, work, personal_path, personal, _ = banks
    config = personal if config_name == "personal" else work
    if invalid is None:
        del config["data_dir"]
    else:
        config["data_dir"] = invalid
    work_path.write_text(json.dumps(work))
    personal_path.write_text(json.dumps(personal))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    assert call("search_memories", {"query": "q", "bank": bank})["isError"]


@pytest.mark.parametrize("bank", ["personal", "work-profile", "investigate"])
@pytest.mark.parametrize("alias", ["same", "symlink"])
def test_profile_shared_data_dir_fails_closed(banks, monkeypatch, bank, alias):
    work_path, work, personal_path, personal, _ = banks
    if alias == "same":
        personal["data_dir"] = work["data_dir"]
    else:
        work_dir = Path(work["data_dir"])
        work_dir.mkdir()
        link = work_dir.parent / "work-data-link"
        link.symlink_to(work_dir, target_is_directory=True)
        personal["data_dir"] = str(link)
    work_path.write_text(json.dumps(work))
    personal_path.write_text(json.dumps(personal))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    assert call("search_memories", {"query": "q", "bank": bank})["isError"]
    assert call("supersede_memory", {"old_id": "1", "new_id": "2", "bank": bank})["isError"]


@pytest.mark.parametrize("bank", ["personal", "work-profile", "investigate"])
@pytest.mark.parametrize("field", ["project_id", "app_id"])
@pytest.mark.parametrize(
    "invalid", [None, 12, "", " ", "*", "bad*id", " leading", "trailing ", "bad\nid", "x" * 257, "界" * 86]
)
def test_invalid_profile_id_fails_closed(banks, monkeypatch, bank, field, invalid):
    work_path, work, _, _, _ = banks
    work["memory_banks"][bank][field] = invalid
    work_path.write_text(json.dumps(work))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: pytest.fail("project fallback"))
    assert call("search_memories", {"query": "q", "bank": bank})["isError"]
    assert call("supersede_memory", {"old_id": "1", "new_id": "2", "bank": bank})["isError"]


def test_platform_profile_fails_closed(banks, monkeypatch):
    monkeypatch.setenv("MEM0_CODE_BACKEND", "platform")
    monkeypatch.setattr(mcp_server, "search_memories", lambda *a, **k: pytest.fail("platform fallback"))
    monkeypatch.setattr(local_backend, "request", lambda *a, **k: pytest.fail("HTTP called"))
    assert len(mcp_server.handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]) == 1
    assert call("search_memories", {"query": "q", "bank": "personal"})["isError"]
    assert call(
        "remember_memory",
        {
            "text": "q",
            "kind": "rule",
            "scope": "personal",
            "triggers": [],
            "bank": "work-profile",
        },
    )["isError"]
