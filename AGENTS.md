# github-action-templates

Reusable GitHub Actions workflows + canonical caller workflows for jr200-labs / consumer orgs.

## Two halves

**`.github/workflows/`** — *reusable* workflows (`workflow_call`). Each implements one piece of CI/release machinery (lint, build, publish). Consumer repos call these via `uses:`. These are the underlying primitives.

**`consumers/`** — *caller* workflows. The one-line files that live in each consuming repo's `.github/workflows/` and just `uses:` a reusable. Consumers don't hand-author these; they're injected by `consumers/scripts/sync-shared` and held identical across repos by a drift-check.

## How a consuming repo uses this

1. Drop `.github/.shared-config.yaml` declaring which **groups** to opt into.
2. Run `./scripts/sync-shared` (or first-run `--bootstrap`) — fetches the canonical caller workflows for each declared group and writes them to `.github/workflows/`.
3. Commit. The `drift-check` workflow runs on every PR thereafter and fails CI if the on-disk files diverge from the canonical.

```yaml
# .github/.shared-config.yaml
workflows:
  - hygiene        # commitlint + lint-pr-metadata + drift-check + renovate
  - go             # ci-go
  - docker         # build-docker-image
  - release        # release-please
```

No string substitution, no archetype — files are verbatim copies. Per-repo divergence is captured by *which groups* the repo declares, not by parameters within a group.

## Groups

`consumers/groups/<name>.yaml` defines a group:

```yaml
includes:
  - lint-pr-metadata
  - commitlint
  - drift-check
  - renovate
```

Each entry names a workflow file in `consumers/workflows/`. Resolution unions all declared groups' `includes` lists and dedupes — so multi-language repos can list `python` and `node` together with overlapping members.

Current groups:

| Group | Pulls in | Use when |
|---|---|---|
| `hygiene` | lint-pr-metadata, commitlint, drift-check, renovate | every repo that wants any CI |
| `python` | ci-python | repo has `pyproject.toml` |
| `node` | ci-node | repo has `package.json` |
| `go` | ci-go | repo has `go.mod` |
| `docker` | build-docker-image | repo publishes a docker image to ghcr.io |
| `release` | release-please | repo cuts versioned releases |

## Trigger model

`release-please.yaml` is the sole release fan-out. On a Release PR merge it cuts a tag + GitHub Release, then fires a single `repository_dispatch: release-published` event. Every artifact-publishing workflow (`build-docker-image.yaml`, etc.) listens on the same event type, so their callers can be verbatim across repos. Repos that don't publish docker simply omit `docker` from their groups; the dispatch fires anyway and goes nowhere.

## Adding a new group

1. Add `consumers/workflows/<workflow-name>.yaml` — the caller file. Must include a top-level `permissions:` block; see GOTCHAS.md #12.
2. Add `consumers/groups/<group-name>.yaml` listing it under `includes:`.
3. Update this doc.
4. Open PR. Merge.
5. Repos opt in by adding `<group-name>` to their `.github/.shared-config.yaml` and running `sync-shared`.

## Updating an existing canonical workflow

Edit `consumers/workflows/<name>.yaml` and merge to `master`. Every consuming repo's `drift-check` job goes red on its next CI run until the maintainer runs `sync-shared` and PRs the result. The drift is the migration trigger — no batch updates from outside.

## Load-bearing properties

The canonical caller workflows encode invariants that are easy to break by hand and have bitten us before:

- **Top-level `permissions:`** — only the caller's *top-level* permissions cascade into reusable workflows. Job-level permissions on the caller are silently ignored when the caller invokes a reusable. Missing this on `build_docker_image.yaml` was the keymint v1.0.0 silent build failure.
- **Secret name match** — the reusable declares a secret name; the caller must pass it under exactly that name. `app_private_key` vs `INTEGRATION_APP_PRIVATE_KEY` is a one-character bug that fails the run at startup.
- **Runner forwarding** — every reusable that runs jobs takes a `runner:` input parameterised via `vars.RUNNER_PROFILES[vars.RUNNER_PROFILE].default`. Hard-coded `ubuntu-latest` is forbidden.

## Tests: unit vs integration

The canonical `ci-python` / `ci-go` / `ci-node` callers run **lint + unit tests only**. Integration tests — anything that needs external infrastructure (database, message bus, S3, etc.) — stay **bespoke per repo** in a separate `integration-tests.yaml` workflow that owns its own service setup, fixtures, and secrets.

Why: each repo's external deps are different, so there's no canonical infra setup that fits every consumer. Pushing infra into the canonical CI would either reintroduce per-repo substitution (rejected — see "How a consuming repo uses this" above) or impose a one-size-fits-none stack on every Python/Go/Node repo.

How: canonical CI calls `pytest` (or `go test`, `vitest`) with the reusable's default flags. Each repo's test config (e.g. `pyproject.toml`'s `[tool.pytest.ini_options].addopts = "-m 'not integration'"`) excludes the integration subset from default collection. Tests carrying the marker (e.g. `@pytest.mark.integration`) only run when a bespoke workflow opts them in.

Repos with infra-dependent tests that can't (yet) be split: stay on a fully bespoke `ci.yaml` and omit the language group from `.shared-config.yaml`. Drift-check warns on the bespoke file (`stale-or-bespoke: ...`) but doesn't fail. Track adoption per repo under JRL-33.

## Lint configs

Separate from the workflow injection: `shared/sync.sh` syncs canonical lint configs (ruff.toml, eslint.config.mjs, .golangci.yml, etc.) into `.shared/` in consumer repos. That mechanism predates `consumers/` and is unrelated; see `shared/MANIFEST.json`.
