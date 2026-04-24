# Shared syncpack config

Canonical syncpack baseline for jr200-labs node repos. Consumed by
`shared/sync.sh` in `merged` mode — the base file here merges with an
optional per-repo `.syncpackrc.local.yaml` to produce the final
`.syncpackrc.yaml` that syncpack reads.

## Files

- `.syncpackrc.base.yaml` — canonical, synced verbatim into each repo.
  **Do not edit in consumer repos** — edits are overwritten on next sync.

## Consumer repo layout

```
<repo>/
  .syncpackrc.base.yaml    # synced (tracked, drift-checked)
  .syncpackrc.local.yaml   # optional, repo-owned (tracked)
  .syncpackrc.yaml         # merge output (tracked, drift-checked)
```

## Merge semantics

`sync.sh` runs:

```
yq ea '. as $i ireduce ({}; . *+ $i)' .syncpackrc.local.yaml .syncpackrc.base.yaml \
  > .syncpackrc.yaml
```

Deep merge, arrays append. **Local groups come first, base groups last.**
Syncpack evaluates `semverGroups` / `versionGroups` first-match-wins, so
the base caret catch-all must stay last — repo-specific pins must live
in `.syncpackrc.local.yaml`.

If `.syncpackrc.local.yaml` is absent, the base is copied to the target
unchanged.

## Peer-ignore pattern (opt-in, JRL-29)

Libs that expose WASM or native-binding packages as `peerDependencies`
with ranges deliberately broader than the dev install pin should add:

```yaml
# .syncpackrc.local.yaml
versionGroups:
  - label: "Peer ranges broader than install pins — see JRL-29"
    dependencyTypes: ["peer"]
    dependencies:
      - "@duckdb/duckdb-wasm"
      - "apache-arrow"
    isIgnored: true
```

Without this, syncpack fails with `DiffersToHighestOrLowestSemver` when
the peer range is `>=X <Y` and the dev install is `^X`.

See [JRL-29](https://linear.app/jr200-labs/issue/JRL-29) for the full
rationale (duckdb-wasm dev45 transitive drag-in, peerDependencies as
structural fix).

## Syncpack version pinning

Shared config tracks a specific syncpack major. Syncpack v14 broke v13
config shape (`dependencyTypes` value changed from `peerDependencies` to
`peer`, among others). Each consumer repo should pin `syncpack` in
`devDependencies` at the version this base was written for. Renovate
handles the bump; a base update lands in `github-action-templates` first,
then the syncpack pin bump propagates per-repo.

Current base tracks: **syncpack ^14**.

## Offline tolerance

`sync.sh` is offline-tolerant by design. If `yq` is absent or the merge
fails, the script warns and preserves the last-good `.syncpackrc.yaml`
(never fails the sync, never blocks the commit). Consumer repos should
install `yq` in CI via their shared lint setup.
