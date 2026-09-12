# Rulesets

Source of truth for GitHub branch-protection rulesets. Reconciled by `scripts/apply-rulesets.sh`.

## Layout

```
rulesets/
├── trunk-protect.json                   # canonical ruleset body (verbatim API payload)
├── shared-workflow-ref-automerge.json   # exact PR policy for shared-ref auto-merge queueing
├── targets.yaml                         # which ruleset → which org → org-scope or per-repo-scope
└── README.md                            # this file
```

## Run

```bash
scripts/apply-rulesets.sh             # reconcile
scripts/apply-rulesets.sh --dry-run   # show what would change
scripts/apply-rulesets.sh --org jr200-labs --dry-run
scripts/apply-rulesets.sh --repo jr200-labs/example-repository --ruleset trunk-protect
scripts/apply-rulesets.sh --targets-file ../consumer/ruleset-targets.yaml --org example-org
```

Requires `gh`, `jq`, `yq` and a `gh auth login` with admin on every targeted org. `gh` token plan must support requested scope — org-level rulesets need GitHub Team; free orgs fall back to per-repo. Private repositories on plans without branch protection/rulesets support will fail with GitHub's "Upgrade to GitHub Pro or make this repository public" error until the repo is public or the org has a paid plan.

The generated drift workflow reads its target map from
`.github/ruleset-targets.yaml` in the consumer repository. Set the
`RULESET_TARGETS_FILE` repository or organization variable to use another
path. An optional `.github/ruleset-automerge.json` supplies consumer-owned App
identities and path policy; override its path with
`RULESET_AUTOMERGE_CONFIG_FILE`.

## What `trunk-protect` does

- Targets default branch (`~DEFAULT_BRANCH`) of every covered repo.
- `pull_request` rule: require PRs and allow squash merges only.
- `required_linear_history`: aligns with squash-only merge policy.
- `non_fast_forward`: blocks force-push.
- `deletion`: blocks branch deletion.
- `bypass_actors: []` — no admin override.

Adjacent repo settings are also reconciled on every targeted repo:

- `allow_auto_merge=false` by default. `shared-workflow-ref-automerge.json` keeps the exact title, branch, allowed GitHub App author login, and allowed generated-file globs needed if shared-ref auto-merge is explicitly enabled later.
- `delete_branch_on_merge=true` so merged PR branches are cleaned up automatically.
- `allow_update_branch=true` so PRs can use GitHub's `Update branch` button when the base branch moves ahead.

## Adding a target

Edit `targets.yaml`:

```yaml
trunk-protect:
  jr200-labs: repo
  example-org: org
```

Keep consumer-specific targets outside this repository and pass them with
`--targets-file`. The checked-in file contains only this repository owner's
defaults. The command is idempotent: existing rulesets get PUT in place and
new ones get POSTed.

## Onboarding a new repository

Use the repo filter to apply existing canonical rulesets to one new repo without touching the rest of an org:

```bash
scripts/apply-rulesets.sh --repo jr200-labs/example-repository --ruleset trunk-protect
scripts/apply-rulesets.sh --targets-file ../consumer/ruleset-targets.yaml \
  --org example-org --repo example-org/example-repository --ruleset trunk-protect
```

Useful flags for staged rollout:

- `--repo ORG/REPO` (repeatable): target specific repos only.
- `--org ORG`: narrow to one organization configured in the selected targets file and prompt `Y/n` per repository before applying repository-scoped changes. For organization-scoped rulesets, the script also prompts once before applying the organization-wide rule.
- `--ruleset NAME`: apply one canonical ruleset only.
- `--targets-file PATH`: load organization and scope mappings from an external YAML file.
- `--automerge-config PATH`: load allowed automation identities and paths from an external JSON file.
- `--skip-auto-merge`: skip repo-level merge-setting patches (`allow_auto_merge`, `delete_branch_on_merge`, `allow_update_branch`) and shared workflow ref PR auto-merge queueing if you only want ruleset reconciliation.

## Adding a new ruleset

1. Land the body at `rulesets/<name>.json` (verbatim GitHub API payload).
2. Add a section under `<name>:` in `targets.yaml` listing each org's scope.
3. Run `scripts/apply-rulesets.sh`.

## Drift

The script is reconcile-only — running it brings live state to match canonical. To detect drift on a schedule, wrap it in a workflow that runs `--dry-run` and opens a tracking issue if any line shows `DRY:`. (Not yet implemented; same pattern as `drift_check_merge_settings.yaml`.)
