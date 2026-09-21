# BOLT in TensorRT-LLM CI

[LLVM BOLT](https://github.com/llvm/llvm-project/tree/main/bolt) reorders the code layout of
an already-compiled binary using a profile of how that binary actually executes. No recompile,
no source change: `llvm-bolt` rewrites the ELF in place using `.fdata`/`.yaml` collected from a
real workload.

This directory holds the toolkit. The purpose of this file is to record **what is optimized
today and what is not**, because the answer is not uniform across artifacts, architectures, or
pipelines, and reading it off the Jenkinsfiles takes a while.

## The model: one producer, several consumers

```
  post-merge SBSA build
          |
          v
  BoltProfileGen (aarch64 only)                         <- PRODUCER
    fan out perf-sanity workloads under instrumented libs
    merge the .fdata, package a bundle
          |
          +--> promote  bolt-profiles/<branch>/<triple>/latest.tar.gz
          |
          +--> publish  bolted-TensorRT-LLM-GH200.tar.gz at the run's artifactPath
                          |
  ------------------------+--------------------------------------------
          |               |                    |
          v               v                    v
   build tarball    release wheel        release image          <- CONSUMERS
```

A consumer never generates a profile. It pulls the branch's promoted bundle (or the run's
`bolted-` tarball) and applies it. The producer runs post-merge only: profiling is three
multi-hour GPU workloads, and the build it profiles has to be un-BOLTed or the profiles are
circular.

Everything is keyed by triple. `BoltProfileGen` is only ever launched with
`targetArch: aarch64-linux-gnu`, so **aarch64 is the only architecture with a promoted bundle.**

### Files

| Path | Role |
| --- | --- |
| `apply_bolt.py` | Apply a bundle to a release tarball (`--tarball`) or a standalone wheel (`--wheel`). Regenerates the wheel `RECORD`. |
| `internal/apply_latest.sh` | Pull the branch's `latest` bundle, then apply it. Exit 3 = nothing promoted, 2 = apply failed. |
| `internal/artifactory.sh` | `package` / `promote` / `pull-latest` for bundles. |
| `internal/slurm_merge.sh` | Cluster-side merge + package (+ apply, under `BOLT_APPLY=1`). |
| `internal/perf_instrument_hook.sh` | `POST_INSTALL_HOOK` that swaps in instrumented libs so a workload emits `.fdata`. |
| `internal/llvm_bolt_version.sh` | The pinned `llvm-bolt` release. Single source of truth — every step must run the same version. |
| `bolt_lib.sh`, `manifest.py`, `run_local.sh`, `setup_env.sh` | Local/manual driving and bundle metadata. |

## Coverage

What is actually BOLT-optimized, by artifact and pipeline. "n/a" means the artifact is not
produced on that path.

| Artifact | pre-merge `main` | post-merge `main` | nightly `main` | GA `release/*` | x86_64 |
| --- | --- | --- | --- | --- | --- |
| Build tarball (`TensorRT-LLM-GH200.tar.gz`) | yes | no (by design) | yes | **no** | **no** |
| `bolted-` tarball | n/a | yes | no | no | **no** |
| Standalone release wheel (`<arch>/*.whl`) | yes | no | yes | **no** | **no** |
| SBSA release image (installed binaries) | n/a | yes¹ | yes¹ | yes¹ | **no** |
| Image profile bundle (`Dockerfile.bolt`) | yes | yes | yes | yes | yes² |

¹ Only where the image installs a downloaded wheel rather than compiling one, which is
governed by `useWheelFromBuildStage` — a Jenkins **job-config** parameter, not a repo value.
See gap 3.
² Baked in on every arch by design, but inert where no matching profiles exist.

The gate behind each row:

| Consumer | Applied by | Gate | Profiles from |
| --- | --- | --- | --- |
| Build tarball | `Build.groovy::applyLatestBolt` | `globalVars.bolt_consume_build` | last promote |
| `bolted-` tarball | `BoltProfileGen::publishBoltedTarball` | `boltPublishBolted` | this run |
| Release wheel | `L0_Test.groovy::applyLatestBoltToWheel` | `globalVars.bolt_consume_build` | last promote |
| SBSA release image | `get_wheel_from_package.py --bolt-branch` | `boltOptimizeWheel` | last promote |
| Image profile bundle | `BuildDockerImage.groovy::overlayBoltBundle` | `boltOverlayEnabled` | last promote |

Only the `bolted-` tarball is same-commit, and only because the stages that consume it — the
post-merge SBSA tests — already run after the producer in the same branch, so it is free
there. Everywhere else the build would have to *wait* for the producer, and the last promoted
bundle is used instead. `-infer-stale-profile` means that drift costs optimization quality,
never correctness.

`bolt_consume_build` is resolved once, by `L0_MergeRequest.groovy::resolveBoltConsume()`, and
carries two restrictions: not post-merge (the post-merge tarball is the producer's input), and
`main` only.

## Known gaps

**1. x86_64 is entirely unoptimized.** No x86 bundle is ever promoted, so every x86 consumer
takes `apply_latest.sh`'s documented "nothing promoted" exit and stays un-BOLTed. This is a
graceful skip, not an error. Closing it means running the producer for `x86_64-linux-gnu` —
workloads, cluster capacity, and a second promote path.

**2. Release branches get nothing.** `resolveBoltConsume()` is `main`-only, so a GA release cut
from `release/x.y` BOLTs neither the tarball nor the wheel. The mechanism to fix it mostly
exists: bundles are already promoted per-branch under `bolt-profiles/<branch>/<triple>`, and
`applyLatestBoltToWheel` already falls back to `main` when the branch has none. What is missing
is relaxing the `main`-only gate deliberately, with a decision about whether a release branch
should consume its own profiles or main's.

**3. An image that compiles its own wheel is never optimized.** `boltOptimizeWheel` works on
the wheel unpacked from the build tarball. When the image compiles in-container instead —
`useWheelFromBuildStage` off — there is no such wheel and the flag is inert. Nightly builds
its image through `plcContainerScanningJob`, which does not set that parameter at all, so the
answer comes from the Jenkins job config rather than this repo. Worth confirming before
trusting the nightly/GA row above.

**4. Post-merge sanity-check wheels are un-BOLTed.** A side effect of `resolveBoltConsume()`'s
post-merge exclusion, which exists for the tarball (producer input) and does not apply to this
wheel. Harmless today — those wheels are not released — but the reason is unrelated to the
effect, so it is a trap for whoever reads it next.

**5. `Dockerfile.bolt` copies profiles, it does not apply them.** The overlay bakes the bundle
into every image so a from-source rebuild can be reproduced offline. It does not optimize the
binaries the image already installed; that is the job of the wheel the image is built from.

## What is left to do

Roughly in dependency order. Each is independent of the others unless noted.

1. **Run the producer standalone.** `BoltProfileGen` is launched from the post-merge SBSA
   branch, and that single fact causes most of the restrictions above. Because the post-merge
   build is the producer's own input, `resolveBoltConsume()` has to exclude post-merge, which
   is why gap 4 exists. Because the producer only runs on post-merge, nothing promotes for a
   branch that has no post-merge cadence, which is most of why gap 2 exists. And because it
   profiles the build from the run it is attached to, every consumer in that run either waits
   hours or accepts drift. Detaching it — its own schedule, its own input selection — makes
   the branch and pipeline gates deliberate choices instead of consequences.

2. **Relax the `main`-only gate (gap 2).** Bundles are already promoted per-branch and the
   consumers already fall back to `main`, so the missing piece is a decision: should a release
   branch consume its own profiles or main's? Blocked on nothing, but much easier to reason
   about after item 1.

3. **Confirm `useWheelFromBuildStage` for the nightly/PLC image path (gap 3),** and set it
   explicitly in `L0_MergeRequest.groovy` rather than inheriting it from job config, so the
   answer lives in the repo.

4. **Decide whether x86_64 is in scope (gap 1).** This is a capacity question — workloads and
   cluster time for a second triple — not a plumbing one. Everything downstream already keys
   by triple and skips cleanly when a bundle is absent.

5. **Drop the duplicated llvm-bolt staging in `Build.groovy` and `L0_Test.groovy`.**
   `apply_latest.sh` now stages the toolchain itself via `stage_llvm_bolt.sh`; the two inline
   copies are harmless (the staging is a no-op when `llvm-bolt` is already on `PATH`) but they
   are dead weight.

6. **Reconsider the post-merge exclusion for sanity-check wheels (gap 4)** once item 1 lands,
   since the reason for it disappears with it.

## Design note: why the optimized tarball has its own name

`BoltProfileGen` publishes `bolted-<tarball>` alongside an untouched canonical one rather than
overwriting the canonical name. Overwriting cannot be made race-free: the canonical object is
downloadable the moment the build stage finishes, and image builds run in parallel with the
SBSA branch, so a consumer can fetch it hours before BOLT could replace it and keep the
unoptimized bytes forever.

A distinct name has no such window. It appears atomically and only on success, so its existence
*is* the proof that what you are fetching is optimized. Consumers that require optimization name
it explicitly and fail when it is absent; consumers that do not care keep reading the canonical
object, which never changes under them.

## Operational notes

**Bumping `llvm-bolt`.** Edit `internal/llvm_bolt_version.sh` and nothing else. Every step —
instrumentation, merge, apply, and the Jenkins build pods — sources it. They must agree:
`.fdata`/`.yaml` formats are not guaranteed stable across releases, so a split version produces
a bad or empty profile rather than an error. `LLVM_BOLT_VERSION` in the environment overrides
the pin for a one-off run.

**Reproducing a BOLTed build from an image.** The bundle is baked at
`$TRTLLM_BOLT_PROFILES`; see `docker/README.md`.

**Applying a bundle by hand.**

```bash
# tarball
python3 scripts/bolt/apply_bolt.py --tarball TensorRT-LLM-GH200.tar.gz \
    --profiles /path/to/bundle --output bolted-TensorRT-LLM-GH200.tar.gz

# standalone wheel (RECORD is regenerated)
python3 scripts/bolt/apply_bolt.py --wheel tensorrt_llm-*.whl \
    --profiles /path/to/bundle --output bolted.whl
```

`--dry-run` lists what would be optimized without running `llvm-bolt`.
