# Personal development memory

This fork adds a local memory service for Codex and Claude Code. A single
process owns persistent Qdrant and SQLite storage; native plugins connect to
it over authenticated loopback HTTP. Existing Mem0 Platform integrations
remain the default when no local configuration is present.

The service uses AIHubMix for extraction (`gpt-6.1-terra`) and embeddings
(`embed-v-4-0`, 1536 dimensions). Memory files stay on this computer. Captured
text and search queries are sent to AIHubMix for inference; this is not an
offline model setup.

## Setup

Use a Hatch environment containing the root Python SDK dependencies.
`AIHUBMIX_API_KEY` must already be exported in your shell. No Mem0 Platform
account or key is required.

From the repository root:

```sh
hatch -e local-memory run python integrations/local-memory/manage.py init
hatch -e local-memory run python integrations/local-memory/manage.py serve
```

`init` records the current Python executable and creates:

- `~/.config/mem0-local/config.json`: backend, loopback URL, user ID, data
  directory, Python executable, and token-file path.
- `~/.config/mem0-local/token`: a random local-service credential, mode `0600`.
- `~/.local/share/mem0-local/`: persistent memory and service files.

The config contains no model API key. Set `MEM0_LOCAL_CONFIG` to use another
configuration path. `MEM0_CODE_BACKEND=platform` explicitly selects the
original hosted backend; local configuration errors never silently switch
to the hosted backend.

On macOS, start a login service instead of leaving a terminal open:

```sh
hatch -e local-memory run python integrations/local-memory/manage.py start
hatch -e local-memory run python integrations/local-memory/manage.py status
```

The LaunchAgent runs a login shell so it can use your existing exported
`AIHUBMIX_API_KEY`. It does not copy that key into its plist. Service logs are
under the data directory. The service listens only on `127.0.0.1:8769`;
initialize with `--port` to use another available port.

## Install the local plugins

Generate installable packages and a separate local marketplace:

```sh
hatch -e local-memory run python integrations/local-memory/manage.py prepare-plugins
codex plugin marketplace add ~/.local/share/mem0-local/plugin-marketplace --json
codex plugin add mem0-local@0genlab-memory --json
claude plugin marketplace add ~/.local/share/mem0-local/plugin-marketplace --json
claude plugin install mem0-local@0genlab-memory
```

Restart the coding clients after installation. This plugin is named
`mem0-local`; it does not replace global rules or hooks. Avoid running the
hosted Mem0 capture plugin alongside it for the same task unless you intend
to capture into both backends.

After changing shared Python runtime code, regenerate packages with
`prepare-plugins` and refresh the installed local plugins through their host.
The regular repository builder remains the source for native plugin bundles.

## Development workflow

Before investigation, search for a concrete symptom, error identifier, or
earlier project decision. Native hooks capture completed work into candidate
lessons for later sessions. Automatically extracted lessons are unverified;
successful extraction is not proof that a remembered conclusion is correct.

The local MCP also exposes `remember_memory` for an explicit structured
lesson and `supersede_memory` to replace an old conclusion while retaining
its history. A record includes:

- `kind`: `trap`, `rule`, `state`, or `preference`.
- `scope`: project or personal, always within the current user and project.
- `triggers`: symptoms or identifiers that should recall the lesson.
- `evidence`, `verify`, and `verified_at`: actual evidence, a way to verify it,
  and when that verification occurred.
- `review_after`: a date after which recall marks the lesson as stale.

Explicit lesson text is limited to 4096 UTF-8 bytes. A verification timestamp
requires evidence and a verification method. Replaced lessons are excluded
from normal recall. Returned context is bounded and includes provenance and
verification/stale markers.

Search scopes retain the native plugin's `repo`, `dir`, and `mine` names.
Local mode always restricts results to the requesting user and project;
`mine` selects personal records, and `dir` narrows project records to the
current directory and descendants while retaining personal records.
This is a personal service, not a multi-user authorization system.

Local `forget` commands are refused without changing data: service memory
deletion is not implemented. Use `supersede_memory` to replace a conclusion;
this hides the old lesson from recall while retaining its history.

No historical private memory files are imported automatically. Existing
`AGENTS.md` and `CLAUDE.md` remain the authority for development instructions.

## Diagnostics

```sh
hatch -e local-memory run python integrations/local-memory/manage.py status
launchctl print gui/$(id -u)/com.0genlab.mem0-local
```

Restart the service after updating its code:

```sh
hatch -e local-memory run python integrations/local-memory/manage.py start
```

Local-service requests use a separate token and bypass HTTP proxies. The
service rejects unauthenticated requests and does not enable browser CORS.
Failed capture remains retryable in the native plugin spool; a coding task
must remain usable while memory is unavailable.

## Tests

```sh
hatch -e local-memory run python -m pytest integrations/local-memory/tests \
  integrations/agent-plugin-core/tests/test_local_backend.py
```

Offline tests use stubbed model/storage clients. Actual AIHubMix calls,
cross-session recall, restart persistence, and installed-host loading require
separate live checks; passing unit tests alone does not establish them.
