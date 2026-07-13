# compose-helper usage rules

This project manages containers through a **compose-helper** script that wraps
`docker compose`. When one of these files exists next to `docker-compose.yaml`
(or `docker-compose.yml`), you MUST use it for every compose operation instead
of calling `docker compose` or `docker-compose` directly:

| Platform | Script | How to run |
|----------|--------|------------|
| Linux / macOS | `compose-helper.sh` | `./compose-helper.sh <command>` |
| Windows | `compose-helper.ps1` | `.\compose-helper.ps1 <command>` |

Why: the script pins the compose project name, auto-selects the compose file,
and passes the right `--env-file`. Calling `docker compose` directly can
silently create a **duplicate project** with different settings.

## Commands (single argument only)

| Command | Effect |
|---------|--------|
| `build` | `docker compose --profile build build --pull` — rebuild images only |
| `rebuild` | Rebuild images, then start detached |
| `start` | Start detached — **no build, no pull** |
| `restart` | Stop, then start detached — **no build, no pull** |
| `stop` | Stop containers, remove orphans — **data volumes preserved** |
| `down` | Stop and **DELETE NAMED VOLUMES** |
| `up` | Rebuild, start, then **follow logs forever (blocks)** |
| `logs` | **Follow logs forever (blocks)** |
| `pull` | Pull images |

## Hard rules

1. **NEVER run `down` unless the user explicitly asks to wipe/reset data.**
   `down` removes named volumes (databases, caches — everything). To stop
   containers, use `stop`.
2. **Do not run `up` or `logs` as an agent** — both end in `logs -f`, which
   never exits and will hang your terminal. Instead:
   - to build + start: use `rebuild`
   - to read logs: use the pass-through form, e.g.
     `./compose-helper.sh logs --tail=100` (returns immediately, no `-f`)
3. **Passing 2 or more arguments bypasses ALL shorthand commands** and routes
   straight to `docker compose` (still with the pinned project name, compose
   file, and env file). So `./compose-helper.sh up -d` is raw
   `docker compose up -d`, NOT the `up` shorthand. Use this deliberately for
   anything not in the table, e.g.:
   - `./compose-helper.sh ps` (single unknown arg also passes through)
   - `./compose-helper.sh config --quiet` (validate the compose file)
   - `./compose-helper.sh exec <service> sh`
   - `./compose-helper.sh logs --tail=100 <service>`
4. `start` and `restart` never rebuild. After changing a Dockerfile, build
   context, or the compose file itself, run `rebuild` (or `build` then
   `start`).
5. Run the script from its own directory context as-is — it resolves its own
   location and always operates on the project folder it lives in. Do not
   `cd` first or pass `-f`/`-p` yourself.

## Env files (two different things)

- `.env` or `.config/.env` — variable substitution inside
  `docker-compose.yaml`; auto-passed to docker compose as `--env-file`.
- `compose-helper.env` — configures the script itself
  (`DCH_PROJECT_NAME`, `DCH_STOP_TIMEOUT`, `DCH_LOGS_TAIL`). Never put
  container variables here.

## Editing docker-compose.yaml

Services that build local images must follow the `--profile build` convention
(builder services under `profiles: ["build"]`, runtime services consume the
tagged image). Before creating or modifying `docker-compose.yaml`, load the
**compose-helper** skill for the full pattern and checklist.
