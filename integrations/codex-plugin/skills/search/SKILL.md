---
name: search
description: Search memories from earlier Codex sessions in this repository. Use it when earlier work may already explain the code, error, decision, or command you need, so you can avoid repeating file reads, searches, or experiments.
argument-hint: "[question] [--top-k number] [--category category-name] [--scope repo|dir|mine] [--run-id session-id] [--bank project|personal|work-profile|investigate]"
disable-model-invocation: true
---

# Search memories

Call `search_memories` with the user's question. Treat `--top-k`, `--category`,
`--scope`, and `--run-id` as tool arguments instead of including them in the
query.

When the local tool offers `bank`, pass `--bank` as that argument. Omit it
for the current project's work memories. Use `personal` only when the user
asks for personal background or preferences; use `work-profile` for saved
work summaries and `investigate` for past production investigation cases.
These profiles require local configuration and are never
searched automatically. Do not retry an unavailable bank against another
bank or the hosted backend. Profile searches do not support `scope=dir`.

Omit `top_k` to use Mem0's configured default. Omit `category` to search every
category. Omit `scope` to use the configured default, normally `repo`: this
repository's shared memory, which everyone who works in it contributes to,
plus your own preferences.

Pass `scope` when the question needs something else: `dir` to narrow the
shared memory to the directory you are working in (a package inside a
monorepo), `mine` for your own preferences alone.

Pass `run_id` with any scope to retrieve memories saved in a specific coding-agent
session. Omit `run_id` to search across sessions. It filters the memories returned;
it does not identify the session making the search request. Use a known session ID,
never invent one. Return the tool's result directly.
