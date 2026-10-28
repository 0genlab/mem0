# Local development memory

This fork adds a local memory service for Codex and Claude Code. A single
process per configuration owns persistent Qdrant and SQLite storage; native
plugins connect to it over authenticated loopback HTTP. Existing Mem0 Platform integrations
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

For separate work and personal banks, keep the existing work configuration,
port `8769`, data directory, token file, and default LaunchAgent label
`com.0genlab.mem0-local` unchanged. Initialize the personal bank in a different
configuration directory with its own token, data directory, port, and label:

```sh
hatch -e local-memory run python integrations/local-memory/manage.py \
  --config ~/.config/mem0-local-personal/config.json init \
  --data-dir ~/.local/share/mem0-local-personal --port 8770 \
  --service-label com.0genlab.mem0-local.personal
hatch -e local-memory run python integrations/local-memory/manage.py \
  --config ~/.config/mem0-local-personal/config.json start
```

The optional `service_label` config field defaults to the legacy label. Only
dot-separated alphanumeric/hyphen label components are accepted. Use `--config`
on subsequent `status` and `start` commands for the personal instance. Both
banks still use AIHubMix inference; the personal bank is not an offline model.
The bank router (`project`, `personal`, `work-profile`) is implemented by the
agent plugin. Automatic capture remains work-only; personal memories require
explicit actions, with no personal capture hooks.

In the work configuration, configure the two explicit profile routes:

```json
{
  "memory_banks": {
    "personal": {
      "config": "/absolute/path/to/mem0-local-personal/config.json",
      "project_id": "personal-profile",
      "app_id": "personal-profile"
    },
    "work-profile": {
      "project_id": "work-profile",
      "app_id": "work-profile"
    }
  }
}
```

These are additional fields, not a replacement for the rest of the work
configuration. Namespace IDs come from this trusted configuration, never
tool input. Search, remember and supersede tools accept `bank=project`
(default), `personal`, or `work-profile`. Profile searches support `repo`
and `mine`, not `dir`. A missing profile fails without falling back to
another bank. Personal writes require `scope=personal`.
The existing work hooks and spool remain unchanged; the personal bank is
accessed directly by explicit tools and has no background capture queue.
Keep client sessions containing only personal material paused for automatic
work capture; selecting a tool bank does not classify the rest of a session.

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

The restricted service endpoint `POST /v1/delete-one` deletes one catalog record
only after checking its `user_id`, `project_id`, `app_id`, `record_id`, and
`expected_sha256` (lowercase SHA-256 of the original UTF-8 text). Set
`dry_run: true` to check without changing data. It returns `record_id` and
`status`, and a dry run also returns `sha256` and `dry_run`. The same request
is idempotent after deletion; wrong identity or hash is rejected. There is no
namespace or bulk deletion endpoint. Deletion hides the record immediately,
then removes its exact vector and SDK history rows by `memory_id`, and finally
retains a text-free catalog tombstone for retries. A failure remains pending
and retries at service startup or on the same request. Use `supersede_memory`
to replace a conclusion while retaining its history.

For example, after calculating the hash of the original record text:

```json
{"user_id":"u","project_id":"p","app_id":"codex","record_id":"record-uuid","expected_sha256":"<64 lowercase hex characters>","dry_run":true}
```

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
