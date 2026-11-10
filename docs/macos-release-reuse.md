# Verify a macOS release once

A Release Please PR runs full verification and creates the signed app archive.
Publication promotes that immutable archive after the release tag is created.
Ordinary code PRs retain focused checks. A manual validation run can prepare an
exact tag when its proof is missing or expired.

## Preparation

The canonical caller recognizes a same-repository Release Please branch owned by
a bot. Preparation checks out that PR's head commit, runs portable checks on the
default fleet, then native verification and packaging on macOS. Native jobs have
contents-read permission and receive no release signing secret. Separate `verify`
and `build` argv in `.github/macos-app.json` keep tests out of packaging.

Verification and packaging share the existing 1200-second execution budget;
cooperative capacity waits remain outside execution time. Consumer fast-lane
budgets are unchanged. Verification inputs come from the argv in
`.github/macos-verification-inputs.json`. Simulator-free packaging inputs come
from `.github/macos-package-inputs.json`. Packaging checks the same source/run
receipt and build inputs without repeating verification.

After successful full verification and packaging, the provider retains the
archive, checksum and `candidate.json` as an immutable workflow artifact for five
days. Its name includes the complete Git tree, run and attempt. The receipt binds
repository, producer source/run, configuration, provider policy, verification
key, app version and both file digests. Focused PRs cannot produce release proof.

## Publication

The release workflow does not run portable checks, native verification or app
compilation. Its default-fleet resolver finds a successful preparation artifact
for the tagged Git tree, checks GitHub producer metadata, and validates the
receipt, archive version and hashes. An equivalent merge commit can reuse proof
when its entire Git tree is identical; no version files or other paths are
excluded from this comparison.

Accepted producers are same-repository release PRs or explicit manual preparation
runs of the canonical workflow using the shared builder. Failed/incomplete runs,
forks, other workflows, incompatible policy/configuration, different trees,
expired artifacts and altered bytes fail validation. API permission failures are
reported directly. Publication never falls back to a build or a new test run.

The resolver promotes the checked bytes into a same-run artifact. If a Sparkle
feed is configured, a contents-read Mac job signs that feed using the archive;
it performs no app compilation, tests, simulator operation or native input
probe. The signing job does not receive the resolver's write-capable token.
The final default-fleet publisher validates source/provenance and file hashes,
including the archive digests captured by the resolver before signing, then
uploads assets and publishes the exact resolved draft release ID.

The canonical caller grants Actions read permission for cross-run artifact
metadata/downloads and Pull requests read permission for commit associations.
Existing native preparation jobs keep
contents-read permission. Release lookup/publication inherits contents-write
permission because private draft access requires it. No new secret is required;
Sparkle signing continues to use the selected-repository organization secret.

## Retries and missing proof

Re-run failed publication jobs to reuse the successful resolver/signing outputs
and immutable bytes. Attempt-specific artifact names are carried through job
outputs, so a failed publisher can download a bundle from a previous attempt.
Re-running the complete publication workflow still promotes existing proof and
never starts verification. Conflicting release assets are never replaced.

A missing/expired artifact fails with an instruction to prepare the release PR
or exact tag first. Preparation is an explicit validation-only workflow run on
that source, with publication disabled. A changed source tree requires fresh
verification; passing checks from a different tree do not certify the release.
