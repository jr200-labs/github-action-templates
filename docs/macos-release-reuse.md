# macOS verification, packaging and publication

The shared workflow runs portable checks on the default fleet, native verification
on macOS, packaging on macOS, and publication on the default fleet. Dependencies
block packaging after failed verification and publication after failed packaging.
PR runs stop after packaging and receive no release signing key.

Configure `.github/macos-app.json` with separate `verify` and `build` argv arrays.
`verify` runs required tests; `build` creates the signed app. Existing consumers
whose build script owns its tests can retain that command, but separating the
commands gives failures distinct verification and packaging jobs. Sparkle feed
signing remains in packaging because the generator is a macOS executable.
Only the publisher gets contents-write permission; it never executes repository
build commands, native probes, simulator operations or appcast generation.

## Evidence between jobs

A separate verifier declares its full external verification inputs through the
argv in `.github/macos-verification-inputs.json`, and its packaging inputs through
`.github/macos-package-inputs.json`. Each command prints a nonempty JSON object
within 60 seconds and leaves tracked/nonignored source unchanged. Verification
inputs include all SDKs and simulator runtimes used by tests. Packaging inputs
include only the compiler, SDK, runtime, signing policy and environment used to
build the shipped app; this probe must not select, boot or lease a simulator.
Use public identities or hashes and exclude credentials.

The successful verification receipt binds repository, source commit, release tag,
provider/configuration, verification inputs, packaging inputs, run ID and consumed
execution time. Packaging checks that receipt, its checkout and its own declared
build inputs. It does not rerun verification probes. Verification and package
commands share 1200 seconds of execution time; cooperative native queue waits
remain separate. The consumer fast verification budget is unchanged.

Packaging validates version/codesign and creates the ZIP/checksum, optionally
reusing an exact verified archive from the trusted release cache. PRs never access
that cache. Cached bytes still require the current workflow's successful
verification receipt. Source, declared verification inputs and weekly epoch bind
the cache key. Corrupt or mismatched entries fail closed.

The final bundle contains ZIP/checksum, optional signed appcast and
`publication.json`. Its receipt binds every file digest, repository, exact source,
tag, verification key and run. The bundle is uploaded as a workflow artifact with
five-day retention. The publisher downloads it from the same workflow run,
checks its complete file set, provenance and hashes, and publishes to the immutable
release ID resolved before verification. Unexpected or conflicting release assets
are never replaced. An already published release succeeds on retry only when its
entire expected asset set matches the verified bytes; it is not modified.

## Retry publication

Use GitHub's **Re-run failed jobs** when only publication failed. The completed
verification and packaging jobs stay completed; publication downloads the same
bundle and repeats only validation/upload/publication. It requires neither a Mac
slot nor simulator access. A partial upload resumes only with matching digests.
A missing/expired artifact fails clearly and never triggers an implicit rebuild.
Re-running all jobs deliberately starts a new verification/package pipeline.
