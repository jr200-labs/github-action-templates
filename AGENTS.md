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
ref: shared-v0.1.0
workflows:
  - hygiene        # conventional commits + lint-pr-metadata + drift-check + renovate + version downgrade guard
  - go             # ci-go
  - docker         # build-docker-image
  - release        # release-please
```

`ref` pins the canonical consumer files to a `github-action-templates` tag. Tags for this surface use `shared-vX.Y.Z`; consumers should move between them through Renovate PRs, not by tracking `master`. `SYNC_REF=<branch-or-tag> ./scripts/sync-shared` is only for one-off diagnostics or bootstrapping.

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
| `hygiene` | lint-pr-metadata, commitlint (Cocogitto conventional-commit check), drift-check, renovate, lint-version-downgrades | every repo that wants any CI |
| `python` | ci-python | repo has `pyproject.toml` |
| `node` | ci-node | repo has `package.json` |
| `node-size-limit` | ci-node-size-limit | repo has `package.json`, a `size` script, and wants size-limit PR comments |
| `node-bench` | node-bench | repo has a Node/pnpm benchmark workflow and wants the shared manual benchmark caller |
| `npm-package` | publish-npm-package | repo publishes a Node package to npmjs.com on release (needs `secrets.NPMJS_API_TOKEN`) |
| `go` | ci-go | repo has `go.mod` |
| `swift` | ci-swift | repo is a native Swift/Xcode app, including macOS app development |
| `docker` | build-docker-image | repo publishes a docker image to ghcr.io |
| `quarto-docs` | publish-quarto-docs | repo publishes a Quarto site from `docs` to `gh-pages` |
| `helm-chart` | build-helm-chart | repo publishes a Helm chart (needs `vars.HELM_CHART_REPO` + `secrets.CHARTS_WRITE_TOKEN`) |
| `wheel` | publish-wheel | repo publishes a wheel to PyPI (needs `secrets.PYPI_API_TOKEN`) |
| `rust-crate` | publish-crate | repo publishes a Rust crate to crates.io (needs `secrets.CRATES_IO_TOKEN`) |
| `release` | release-please | repo cuts versioned releases |
| `oci-artifact` | publish-oci-artifact | repo publishes a generic OCI artifact bundle to GHCR on release |
| `artifact-renovate` | renovate-artifact-published | repo runs targeted Renovate when a configured package or artifact is published |
| `package-access` | package-access-drift | repo consumes private GitHub packages with granular Actions access |
| `drift-check-rulesets` | drift-check-rulesets | one consumer per org watches its own ruleset state |

## Package access drift

Repositories that consume private Container, npm, or NuGet packages can opt
into the `package-access` group and declare the required relationships in
`.github/package-access.yaml`:

```yaml
version: 1
packages:
  - owner: example
    owner_kind: organization
    package_type: container
    name: runtime
    repository: example/application
    required_access: read
    visibility: private
```

The shared workflow queries package metadata with the consuming repository's
own `GITHUB_TOKEN`. This proves that the repository has effective read access
and that package visibility matches the declaration. The group-managed
`scripts/github-package-access-wizard` dispatches that check and waits for its
result. It exits without opening package settings when access is already in
sync, and presents the manual repair steps only when the repository token
detects drift. (`gh auth login` is required to dispatch and observe the check.)
`sync-shared` copies the wizard into opted-in repositories and checks it for
drift alongside the canonical workflow. Maven/Gradle and RubyGems packages
inherit source-repository permissions and use a different access model.

## Publication-triggered Renovate

Publishing repositories may opt into targeted downstream Renovate runs with a
repo-owned `.github/artifacts.yaml`. The file is configuration, not a generated
workflow, and is therefore outside shared workflow drift checks.

```yaml
version: 1
artifacts:
  - component: api
    publisher: docker
    type: docker
    name: ghcr.io/example/api
    renovate:
      repository: example/infrastructure
      dependencies: [example/api]
```

`component` matches the Release Please component. `publisher` is a stable name
for the workflow that proves publication completed. The canonical lanes use
`docker`, `npm`, `pypi`, `cargo`, `helm`, `oci`, and `release`; bespoke package
publishers may use another stable value without changing the resolver. `type`
describes the artifact in the event payload and is likewise extensible. Use
`release` for Go modules because their Git tag is the published artifact. A
registry-backed publisher must notify only after its upload succeeds.

The target repository opts into the `artifact-renovate` group. Its generated
workflow listens for the `artifact-published` `repository_dispatch`, validates
the package names, and passes only those exact names to the reusable Renovate
workflow. This receiver is independent of artifact type, so the same path
handles container images, language packages, charts, modules, and bespoke
registries. Same-org dispatches can use the normal integration App. Cross-org
targets require
`RENOVATE_DISPATCH_CLIENT_ID` and `RENOVATE_DISPATCH_APP_PRIVATE_KEY` for an App
installed on the target repository.

A bespoke publisher calls `notify_artifact_published.yaml` after its publish
job succeeds and supplies the same `publisher` value used in its catalog. New
package ecosystems therefore need a publishing workflow and catalog entry,
but no changes to the event contract, resolver, or Renovate receiver.

For the `swift` group, keep the consumer caller generic. The reusable workflow
auto-detects a single `.xcodeproj` and then chooses the matching project-named
scheme, or the only shared scheme. Repos with multiple Xcode projects or
schemes should pass explicit `project` / `scheme` inputs from a bespoke caller
workflow rather than hard-coding one app's name into the canonical shared
workflow.

## Trigger model

`release-please.yaml` is the sole release fan-out. On a Release PR merge it cuts a tag + GitHub Release, then fires a single `repository_dispatch: release-published` event. Every artifact-publishing workflow (`build-docker-image.yaml`, etc.) listens on the same event type, so their callers can be verbatim across repos. Repos that don't publish docker simply omit `docker` from their groups; the dispatch fires anyway and goes nowhere.

## Adding a new group

1. Add `consumers/workflows/<workflow-name>.yaml` — the caller file. Must include a top-level `permissions:` block; see GOTCHAS.md #12.
2. Add `consumers/groups/<group-name>.yaml` listing it under `includes:`. Optional supporting files live under `consumers/files/` and use repository-relative paths under the group's `files:` key.
3. Update this doc.
4. Open PR. Merge.
5. The `release-shared-ref` workflow automatically tags the merged commit with the next `shared-vX.Y.Z` ref and publishes a GitHub Release with generated notes.
6. Repos opt in by adding `<group-name>` to their `.github/.shared-config.yaml`, bumping `ref:` to the new shared tag, and running `sync-shared`.

## Updating an existing canonical workflow

Edit `consumers/workflows/<name>.yaml` and merge to `master`. The `release-shared-ref` workflow tags the shared surface with the next `shared-vX.Y.Z` ref and publishes a GitHub Release with generated notes when `consumers/**` or `shared/**` changed. Consumer repos pin `.github/.shared-config.yaml` to one of those tags. Renovate watches that pin and opens a dedicated PR per consumer repo; the generated drift-repair workflow is the fallback if a checked-in caller file falls behind the pinned ref.

## Bespoke workflows

Consumer repos may keep additional non-canonical workflows alongside the
injected set, but they must be named `bespoke_*.yaml`. Drift-check ignores that
prefix when reporting workflows outside the resolved shared set. Any other
extra workflow filename is still surfaced as `stale-or-bespoke` so accidental
drift stays visible.

## Load-bearing properties

The canonical caller workflows encode invariants that are easy to break by hand and have bitten us before:

- **Top-level `permissions:`** — only the caller's *top-level* permissions cascade into reusable workflows. Job-level permissions on the caller are silently ignored when the caller invokes a reusable. Missing this on a Docker caller can cause a silent build failure.
- **Secret name match** — the reusable declares a secret name; the caller must pass it under exactly that name. `app_private_key` vs `INTEGRATION_APP_PRIVATE_KEY` is a one-character bug that fails the run at startup.
- **Runner forwarding** — every reusable that runs jobs takes a `runner:` input parameterised via `vars.RUNNER_PROFILES[vars.RUNNER_PROFILE].<role>`. Linux CI generally uses `default`; native Swift/Xcode macOS app CI uses `macos`. Hard-coded labels such as `ubuntu-latest` or `macos-latest` are forbidden.

## Tests: unit vs integration

The canonical `ci-python` / `ci-go` / `ci-node` / `ci-swift` callers run **lint/build + unit tests only**. For native Swift/Xcode macOS app development, `ci-swift` builds and tests the app scheme on a macOS runner with code signing disabled. Integration tests — anything that needs external infrastructure (database, message bus, S3, etc.) — stay **bespoke per repo** in a separate `integration-tests.yaml` workflow that owns its own service setup, fixtures, and secrets.

Why: each repo's external deps are different, so there's no canonical infra setup that fits every consumer. Pushing infra into the canonical CI would either reintroduce per-repo substitution (rejected — see "How a consuming repo uses this" above) or impose a one-size-fits-none stack on every Python/Go/Node repo.

How: canonical CI calls `pytest` (or `go test`, `vitest`) with the reusable's default flags. Each repo's test config (e.g. `pyproject.toml`'s `[tool.pytest.ini_options].addopts = "-m 'not integration'"`) excludes the integration subset from default collection. Tests carrying the marker (e.g. `@pytest.mark.integration`) only run when a bespoke workflow opts them in.

Repos with infra-dependent tests that can't (yet) be split: stay on a fully bespoke `ci.yaml` and omit the language group from `.shared-config.yaml`. Drift-check warns on the bespoke file (`stale-or-bespoke: ...`) but doesn't fail. Track adoption per repo under JRL-33.

### Bespoke integration-tests checklist

When you write a repo's `integration-tests.yaml`, follow these structural rules so it shares the load-bearing properties of the canonical callers even though the content is bespoke:

- **Top-level `permissions:`** — minimum `contents: read`. Add `pull-requests: read` if any reusable inside needs it. Job-level perms on the caller don't cascade into reusables.
- **`runs-on:`** — always `${{ fromJSON(vars.RUNNER_PROFILES)[vars.RUNNER_PROFILE].default }}`. Never hard-code `ubuntu-latest`.
- **Private git deps** — mint an installation token via `actions/create-github-app-token@v3.1.1` with `client-id: ${{ vars.INTEGRATION_CLIENT_ID }}` and `private-key: ${{ secrets.INTEGRATION_APP_PRIVATE_KEY }}`, then write `~/.netrc` for `uv sync` / `go mod download` to use.
- **Test selection** — invoke the marked subset only (`pytest -m integration`, `go test -tags integration`, `vitest --include 'tests/integration/**'`). Don't re-run unit tests; canonical CI already does.
- **Service containers** — declared at job level via `services:` with healthchecks. Connect via `localhost:<host-port>` from the runner.
- **Tooling install** — apt packages for client libs, binaries downloaded from GitHub releases, etc. Pin versions where it matters.

Start with a repository-owned workflow containing the required service containers and binaries. Copy and adapt that pattern; do not abstract it until at least three consumers have similar requirements.

Use neutral fictitious identifiers in reusable workflows, tests, comments, and
documentation. Consumer organization, repository, package, and application
names belong in consumer-owned configuration or explicit workflow/script
parameters.

## Lint configs

Separate from the workflow injection: `shared/sync.sh` syncs canonical lint configs (ruff.toml, eslint.config.mjs, .golangci.yml, etc.) into `.shared/` in consumer repos. That mechanism predates `consumers/` and is unrelated; see `shared/MANIFEST.json`.

### Release-please config flow

`release-please-config.json` is a committed merged file in consumer repos.

- `.release-please.local.json` — committed per-repo override source for settings such as `release-type`
- `shared/release-please-config.base.json` — template-owned base config in this repo
- `release-please-config.json` — committed merge output at the consumer repo root, refreshed by `./.shared/sync.sh`

The shared base opens a separate Release Please PR per component. This keeps
every title in `chore(<branch>): release <component> <version>` form; Release
Please's grouped title loses the component and version for non-root-only
releases.

Rules:

- Do not hand-edit `release-please-config.json` in a consumer repo; update `.release-please.local.json` and re-run `./.shared/sync.sh`
- Commit both `.release-please.local.json` and the refreshed `release-please-config.json`
- Release-related CI should run `sync.sh` before linting so the checked-in merged config stays aligned with the committed overlay
