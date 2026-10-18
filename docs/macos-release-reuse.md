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
release tag. PR events never read or write this cache; the Actions service's ref
scoping prevents PR caches from supplying trusted default-branch release entries.

On a miss the provider runs the repository build/tests, validates the app version
and codesign signature, creates the ZIP/checksum, then atomically prepares a
receipt containing the input key, tag, archive SHA-256, originating workflow
run ID and verification time. It rechecks source/toolchain inputs after the
build before saving.
It saves that archive before appcast generation and upload. Failed verification
never saves a receipt. A hit verifies the complete file set, receipt age, key,
tag and checksums, and promotes the original ZIP bytes into a new output
directory. Signing does not mutate cached executables or archives.

The weekly epoch bounds verification age to seven days. Cache eviction is safe
and causes full verification. Corrupt or incompatible receipts fail closed
instead of publishing unverified bytes. Release retries recheck immutable draft
identity and uploaded asset digests; they never replace conflicting assets.
The restore/input-probing steps are timed separately from repository build
phases, and do not change existing verification or packaging budgets.
