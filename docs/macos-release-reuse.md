# Verified macOS release archives

The macOS provider can promote an immutable archive from a successful full
release build when publishing the same release again. It still generates the
signed appcast and verifies every uploaded release asset. This avoids repeating
acceptance after a feed-signing or upload failure; a first build of changed
release inputs still runs the complete configured build/test command.

Opt in with `.github/macos-verification-inputs.json`, an argv array for a
repository-owned command that prints a nonempty JSON object within 60 seconds:

```json
["python3", "scripts/release-inputs.py"]
```

The command must describe all external build and verification inputs: actual
runtime and installed dependencies, compiler/SDK builds, simulator runtime,
host OS/architecture, environment settings and signing policy/identities.
Return public identities or hashes; never print credentials. It must not mutate
tracked source or create nonignored inputs. Source files, lockfiles and the
declaration itself are already bound by the exact checked-out tag commit.
Repositories without a declaration retain fresh release builds.

The exact cache key binds repository, source/tag, app configuration, declaration,
declared inputs, host platform, provider implementation and a weekly verification
epoch. There are no prefix restores. The checkout must be clean and equal the
release tag. Only release workflow events invoke reuse; PR validation skips it.

On a miss the provider runs the repository build/tests, validates the app version
and codesign signature, creates the ZIP/checksum, then atomically prepares a
receipt containing the input key, tag, archive SHA-256, originating workflow
run ID and verification time. It rechecks source/toolchain inputs after the
build before saving.
It saves that archive before appcast generation and upload. Failed verification
never saves a receipt. A hit verifies the complete file set, receipt age, key,
tag and checksums, and promotes the original ZIP bytes into a new output
directory. Signing does not mutate cached executables or archives.

The store lives outside the checkout and job temporary directory at
`~/.cache/github-action-templates/macos-verified-release/<repository-hash>/<key>`.
Runner slots sharing the same Mac account can reuse it. A retry on another Mac
or account builds afresh. No release archive is downloaded from or uploaded to
GitHub Actions cache storage. Dependency and compiler caches are independent.

Directories are private to the runner account. This is a local performance store
within that account's trust boundary, not Actions ref-isolated storage. Entries
are atomically published and read under a nonblocking per-repository file lock;
a busy store falls back to fresh verification. Entries expire after seven days,
and each repository retains at most 512 MiB, evicting the oldest entries first.
Abandoned partial saves are removed on the next store access.
Loss, eviction or storage failure never blocks a fresh build or publication.

The weekly epoch bounds verification age to seven days. Cache eviction is safe
and causes full verification. Corrupt or incompatible entries are discarded
and require full verification before publication. Release retries recheck immutable
draft identity and uploaded asset digests; they never replace conflicting assets.
The restore/input-probing steps are timed separately from repository build
phases, and do not change existing verification or packaging budgets.
