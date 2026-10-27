#!/usr/bin/env python3
"""Expose Mem0's memory search as one local coding-agent tool."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import local_backend
import telemetry
from memory_core import (
    CODING_MEMORY_CATEGORY_NAMES,
    PLUGIN_VERSION,
    SEARCH_SCOPES,
    format_search_result,
    resolve_repo,
    search_memories,
)

PROTOCOL_VERSION = "2024-11-05"
TOOL_NAME = "search_memories"
TOOL_DESCRIPTION = (
    "Search memories from earlier work in this repository. Use it before "
    "repeating investigation or when earlier decisions, fixes, commands, or "
    "results may help."
)
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "A direct question about earlier work in this repository.",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Maximum memories to return. Uses Mem0's configured default when omitted.",
        },
        "category": {
            "type": "string",
            "enum": list(CODING_MEMORY_CATEGORY_NAMES),
            "description": "Optional memory category. Omit to search every category.",
        },
        "scope": {
            "type": "string",
            "enum": list(SEARCH_SCOPES),
            "description": (
                "Which memories to search. 'repo' (default) is the whole repository's "
                "shared memory plus your own preferences, 'dir' narrows the shared "
                "part to the current directory, 'mine' is your preferences alone."
            ),
        },
        "run_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional coding-agent session ID. With any scope, restricts results to memories "
                "saved in that session. Omit to recall memories across sessions."
            ),
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}
REMEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 4096},
        "kind": {"type": "string", "enum": ["trap", "rule", "state", "preference"]},
        "scope": {"type": "string", "enum": ["project", "personal"]},
        "triggers": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "string"},
        "verify": {"type": "string"},
        "verified_at": {"type": "string"},
        "review_after": {"type": "string"},
    },
    "required": ["text", "kind", "scope", "triggers"],
    "additionalProperties": False,
}
SUPERSEDE_SCHEMA = {
    "type": "object",
    "properties": {
        "old_id": {"type": "string", "minLength": 1},
        "new_id": {"type": "string", "minLength": 1},
    },
    "required": ["old_id", "new_id"],
    "additionalProperties": False,
}


class ToolInputError(ValueError):
    pass


def _validate_arguments(
    arguments: Any,
) -> tuple[str, int | None, str | None, str | None, str | None]:
    if not isinstance(arguments, dict):
        raise ToolInputError("Search arguments must be an object.")

    unknown = set(arguments) - {"query", "top_k", "category", "scope", "run_id"}
    if unknown:
        raise ToolInputError(f"Unknown search argument: {sorted(unknown)[0]}")

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolInputError("query must be a non-empty string.")
    query = query.strip()
    if len(query) > 2000:
        raise ToolInputError("query must be at most 2,000 characters.")

    top_k = arguments.get("top_k")
    if top_k is not None and (
        isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20
    ):
        raise ToolInputError("top_k must be an integer from 1 to 20.")

    category = arguments.get("category")
    if category is not None and category not in CODING_MEMORY_CATEGORY_NAMES:
        raise ToolInputError("category must be one of Mem0's supported categories.")

    scope = arguments.get("scope")
    if scope is not None and scope not in SEARCH_SCOPES:
        raise ToolInputError(f"scope must be one of {list(SEARCH_SCOPES)}.")

    run_id = arguments.get("run_id")
    if run_id is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ToolInputError("run_id must be a non-empty string.")
        run_id = run_id.strip()

    return query, top_k, category, scope, run_id


def call_search_memories(arguments: Any, cwd: str | None = None) -> str:
    query, top_k, category, scope, run_id = _validate_arguments(arguments)
    repo = resolve_repo(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    result = search_memories(
        None,
        repo,
        None,
        query,
        top_k=top_k,
        category=category,
        scope=scope,
        run_id=run_id,
        operation="mcp-search",
    )
    return format_search_result(result)


def _local_tool(name: str, arguments: Any, cwd: str | None) -> str:
    backend, config = local_backend.selection()
    if backend != "local":
        raise ToolInputError("Local memory tools are unavailable.")
    schema = REMEMBER_SCHEMA if name == "remember_memory" else SUPERSEDE_SCHEMA
    if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]):
        raise ToolInputError("Invalid memory arguments.")
    if set(schema["required"]) - set(arguments):
        raise ToolInputError("Missing required memory arguments.")
    if name == "remember_memory":
        text = arguments["text"]
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 4096:
            raise ToolInputError("text must be non-empty and at most 4 KB.")
        if arguments["kind"] not in {"trap", "rule", "state", "preference"}:
            raise ToolInputError("Invalid memory kind.")
        if arguments["scope"] not in {"project", "personal"}:
            raise ToolInputError("Invalid memory scope.")
        if not isinstance(arguments["triggers"], list) or not all(
            isinstance(value, str) and value.strip() for value in arguments["triggers"]
        ):
            raise ToolInputError("triggers must be a list of non-empty strings.")
        for optional in ("evidence", "verify", "verified_at", "review_after"):
            if optional in arguments and not isinstance(arguments[optional], str):
                raise ToolInputError(f"{optional} must be a string.")
    elif not all(isinstance(arguments[key], str) and arguments[key].strip() for key in ("old_id", "new_id")):
        raise ToolInputError("old_id and new_id must be non-empty strings.")
    repo = resolve_repo(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    payload = {**local_backend.identity(config, repo), **arguments}
    response, _, _ = local_backend.request(
        config, "/v1/remember" if name == "remember_memory" else "/v1/supersede",
        payload, timeout=120,
    )
    return json.dumps(response, ensure_ascii=False)


def _workspace_cwd(params: dict[str, Any]) -> str | None:
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None
    metadata = meta.get("x-codex-turn-metadata")
    if not isinstance(metadata, dict):
        return None
    workspaces = metadata.get("workspaces") or {}
    if isinstance(workspaces, dict):
        return next((path for path in workspaces if isinstance(path, str) and path), None)
    return None


def _tool_response(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def handle_request(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    request_id = message.get("id")
    method = message.get("method")

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mem0", "version": PLUGIN_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        tools = [
            {
                "name": TOOL_NAME, "description": TOOL_DESCRIPTION, "inputSchema": TOOL_SCHEMA,
                "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True},
            }
        ]
        try:
            backend, _ = local_backend.selection()
        except local_backend.LocalBackendError:
            backend = "local"
        if backend == "local":
            tools.extend([
                {"name": "remember_memory", "description": "Store an explicit local memory.",
                 "inputSchema": REMEMBER_SCHEMA},
                {"name": "supersede_memory", "description": "Supersede a local memory by ID.",
                 "inputSchema": SUPERSEDE_SCHEMA},
            ])
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": tools
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        if name not in {TOOL_NAME, "remember_memory", "supersede_memory"}:
            result = _tool_response("Unknown Mem0 tool.", is_error=True)
        else:
            try:
                result = _tool_response(
                    call_search_memories(params.get("arguments"), _workspace_cwd(params))
                    if name == TOOL_NAME else _local_tool(name, params.get("arguments"), _workspace_cwd(params))
                )
            except ToolInputError as exc:
                result = _tool_response(str(exc), is_error=True)
            except Exception:
                result = _tool_response("Memory search failed." if name == TOOL_NAME else "Memory request failed.", is_error=True)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def main() -> int:
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
            response = handle_request(message)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        except Exception:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": "Internal error"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    telemetry.spawn_flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
