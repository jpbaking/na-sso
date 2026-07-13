---
name: compose-helper
description: >-
  Use when creating or modifying docker-compose.yaml / docker-compose.yml in a
  project that contains a compose-helper script (compose-helper.sh or
  compose-helper.ps1), or when building, starting, stopping, or debugging services in
  such a project. Covers the "--profile build" convention for services that
  build local images, exact script command semantics, env-file discovery, and
  a safe edit-build-verify workflow. Trigger on: docker-compose edits, adding
  a service, adding a Dockerfile/build block, "image not found" errors after
  start, or any docker compose lifecycle task in a compose-helper project.
---

# compose-helper: compose file authoring and the `build` profile

compose-helper is a thin wrapper around `docker compose` that lives next to
`docker-compose.yaml`. Every invocation it makes is:

```
docker compose -p <project> -f <compose-file> [--env-file .env|.config/.env] <args>
```

- `<project>` = the script's directory name, unless overridden by
  `DCH_PROJECT_NAME` in `compose-helper.env`.
- Compose file = `docker-compose.yaml`, falling back to `docker-compose.yml`.
- Env file = `.env` if present, else `.config/.env`, else none.

Always go through the script (never raw `docker compose`) so the project
name stays consistent. Any call with 2+ arguments passes straight through to
`docker compose` with those pinned options — that is the escape hatch for
`config`, `ps`, `exec`, non-following `logs`, etc.

## The `--profile build` convention (IMPORTANT)

The `build`, `rebuild`, and `up` commands run:

```
docker compose ... --profile build build --pull
```

while `start`/`restart` run plain `up -d` / `down` + `up -d` **without** the
build profile. This means the compose file must separate "services that
produce an image" from "services that run":

```yaml
services:
  # BUILDER: exists only to produce a local image. Never runs.
  my-app-builder:
    profiles: ["build"]          # only visible to `--profile build build`
    build:
      context: ./my-app          # dir containing the Dockerfile
    image: my-app:local          # REQUIRED: the tag the build produces

  # RUNTIME: consumes the image. No build block.
  my-app:
    image: my-app:local          # must match the builder's image: tag
    restart: unless-stopped
    env_file: .env               # optional; see env section below
    ports:
      - "8080:8080"

  # Pulled images need no profile and no builder.
  postgres:
    image: postgres:16
    restart: unless-stopped
    volumes:
      - pg-data:/var/lib/postgresql/data

volumes:
  pg-data:
```

### Rules when creating or editing docker-compose.yaml

1. **Every service with a `build:` block gets `profiles: ["build"]` AND an
   explicit `image: <name>:local` tag.** Without `image:`, compose derives a
   tag from the project name and the runtime service can't find it.
2. **Runtime services never have a `build:` block.** They reference the
   builder's `image:` tag exactly. If you add a `build:` block to an
   unprofiled service, `start` may trigger an implicit build and break the
   build/run separation.
3. **Never `depends_on` a builder service.** Builder services are only ever
   built, never started; a runtime service depending on one will fail to
   start (the dependency never becomes "started").
4. **The profile name is exactly `build`** — the scripts hard-code
   `--profile build`. Do not invent other profile names for builders.
5. One builder can feed multiple runtime services: give them all the same
   `image:` tag.
6. If nothing is built locally (all images pulled), skip the convention
   entirely — `--profile build` targeting zero services is harmless.
7. Suffix builder service names with `-builder` (convention, not enforced)
   so intent is obvious.

### Why this pattern exists

- `start`/`restart` are guaranteed fast and side-effect-free: they never
  compile anything or touch the network for builds.
- `build --pull` refreshes base images instead of serving stale layer-cache
  parents, and only touches services that opted into the `build` profile.
- Runtime config stays declarative: every runtime service is just
  `image: + settings`, identical in shape whether the image is local or
  pulled.

## Env files — two separate concerns

| File | Purpose |
|------|---------|
| `.env` (or `.config/.env` fallback) | `${VAR}` substitution inside docker-compose.yaml; auto-passed as `--env-file` |
| `compose-helper.env` | Configures the script itself: `DCH_PROJECT_NAME`, `DCH_STOP_TIMEOUT` (default 30), `DCH_LOGS_TAIL` (default 10) |

When you reference `${VAR}` in the compose file, define it in `.env` (or
`.config/.env`), never in `compose-helper.env`. To pass variables into a
container's environment, additionally use `environment:` or `env_file:` on
that service — `--env-file` alone only does substitution.

## Edit → build → verify workflow

After creating or modifying docker-compose.yaml (Linux/macOS shown; use
`.\compose-helper.ps1` on Windows — both scripts are feature-equivalent):

```sh
# 1. Validate syntax, including build-profile services (pass-through form):
./compose-helper.sh --profile build config --quiet

# 2. Build local images (only build-profile services are targeted):
./compose-helper.sh build

# 3. Start runtime services detached:
./compose-helper.sh start

# 4. Verify state and read bounded logs (pass-through; no -f, returns):
./compose-helper.sh ps
./compose-helper.sh logs --tail=100
./compose-helper.sh logs --tail=100 <service>
```

Steps 2+3 can be combined as `./compose-helper.sh rebuild`.

### Pitfalls

- **Do not run `./compose-helper.sh up` or `... logs` (single-arg) in an
  automated session** — both end in `logs -f` and block forever. Use
  `rebuild` + pass-through `logs --tail=N`.
- **`down` deletes named volumes.** Only use it when the user explicitly
  wants a data wipe; otherwise `stop`.
- **"pull access denied" / "image not found" on `start`** usually means a
  builder's image was never built or its `image:` tag doesn't match the
  runtime service — run `./compose-helper.sh build` and diff the two tags.
- **Changes to Dockerfiles or build contexts do nothing until `build` or
  `rebuild`** — `start`/`restart` never rebuild.
- Two-or-more arguments always bypass the shorthand commands
  (`up -d` ≠ the `up` shorthand). Single unknown arguments (like `ps`) also
  pass through.
