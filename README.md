# github-action-templates

Reusable GitHub Actions workflows _and_ canonical caller workflows shared across consumer orgs, including native Swift/Xcode macOS app CI.

## Two halves

- **`.github/workflows/`** — *reusable* workflows (`workflow_call`). Each implements one piece of CI/release machinery. Consumer repos call these via `uses:`.
- **`consumers/`** — *caller* workflows. Each consuming repo declares groups in `.github/.shared-config.yaml`; `scripts/sync-shared` copies the matching caller files verbatim into `.github/workflows/`. A `drift-check` job in the `hygiene` group fails CI on any divergence. **Don't hand-author caller workflows.** See [AGENTS.md](AGENTS.md) for the full pattern.

Consumer repos should pin `.github/.shared-config.yaml` to a `shared-vX.Y.Z`
tag via `ref:`. Renovate tracks that pin and opens dedicated update PRs, so
shared workflow drift does not ride along with unrelated feature changes.
GAT releases the next `shared-vX.Y.Z` tag automatically when canonical
consumer workflows or shared files change on `master`, including a GitHub
Release with generated notes.

## Merge Policy

Both consumer orgs (`whengas/`, `jr200-labs`) enforce default-branch protection centrally: rulesets require PRs, repo settings disable auto-merge by default + enable branch auto-delete, and the `lint-no-auto-merge` workflow in `hygiene` fails CI if any caller workflow invokes `gh pr merge`, `--auto-merge`, or `gh pr review --approve`. Shared-ref auto-merge guardrails live in config for a future opt-in, but the feature is currently disabled.

## MCP protocol probe

`scripts/probe-mcp-server.sh` is a reusable synthetic check for MCP deployments. It
performs `initialize`, `notifications/initialized`, and `tools/list`, then
verifies any required tool names. Transport and protocol failures use stable,
searchable `MCP_PROBE_ERROR` fields; transient transport retries use
`MCP_PROBE_RETRY`. The endpoint is a required secret because an MCP URL may
contain credentials.

Callers choose a runner with network access to the target service and keep the
workflow bespoke because deployment topology and credential provisioning vary:

```yaml
- name: Download the MCP protocol probe
  run: |
    curl -fsSL \
      https://raw.githubusercontent.com/jr200-labs/github-action-templates/master/scripts/probe-mcp-server.sh \
      -o probe-mcp-server.sh
- name: Probe MCP initialization and tools
  env:
    MCP_ENDPOINT: ${{ secrets.MCP_PROBE_ENDPOINT }}
    MCP_REQUIRED_TOOLS_JSON: '["read_topic", "attach_repository"]'
  run: bash probe-mcp-server.sh
```

## Important

**Read [GOTCHAS.md](GOTCHAS.md) before wiring up a new consumer.** It
covers cross-org secrets, GitHub Free plan limitations, App token quirks,
and other issues that are poorly documented upstream.

## References

- [AGENTS.md](AGENTS.md) — Consumer pattern, group catalogue, how to add new groups
- [GOTCHAS.md](GOTCHAS.md) — Cross-org reusable workflow & GitHub App gotchas
- [GitHub: Reusing Workflows](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
- [GitHub: Accessing Workflows](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#allowing-access-to-components-in-a-private-repository)
