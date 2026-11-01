# Execution workflow

Examples use `python3 <skill>/scripts/remote_dev.py`, with `<skill>` resolved from the installed skill path. Global `--config PATH` goes before the subcommand.

## Select

Read repository instructions and identify command, repo, task type, OS/architecture, runtime versions, peak memory and scratch space. Estimate from the actual project; do not invent measurements.

```sh
python3 <skill>/scripts/remote_dev.py select --task test --os Linux --arch x86_64 --tools python3,node --memory-gib 4 --disk-gib 12
```

Only enabled, allowed, source-sync-approved hosts are considered. Selection returns compatible hosts and rejection reasons. It does not run the workload. Agent judgment must still assess version compatibility, source/data restrictions, package access and transfer cost. No suitable host means explain the missing requirement, not silently enable/install/use a heavy local fallback.

## Snapshot

```sh
python3 <skill>/scripts/remote_dev.py snapshot --repo /path/to/repo --output /path/to/local-task/input.tar.gz
```

Read the adjacent manifest before transfer. It records included/omitted relative paths, Git HEAD, dirty state, sizes and SHA-256. Current modified tracked files and nonignored untracked files are included. Deleted files remain absent. `.git`, common caches, credential names/extensions, private-key headers and symlinks are excluded. These filters are not a comprehensive secret detector. Submodules and Git LFS pointers fail explicitly; agree on a narrow materialization/transfer plan rather than quietly omit necessary inputs. There is no blanket include-secrets switch.

## Run

Put exact task commands in a local UTF-8 file. They run with `/bin/sh -eu` in a fresh remote job directory. Use project-local environments. Example command file:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest --junitxml=results.xml
```

Package installation executes project code and uses the network; the enrolled constraints and current task must permit it.

```sh
python3 <skill>/scripts/remote_dev.py run --host linux-builder --task test --os Linux --arch x86_64 --tools python3 --memory-gib 4 --disk-gib 12 --snapshot /path/to/local-task/input.tar.gz --command-file /path/to/local-task/commands.sh --timeout 1200 --jobs 2
```

The client rechecks policy/binding/resources and the snapshot digest. The worker rechecks admission resources, reserves a shared host slot, creates a marked job directory, verifies/extracts plain files, runs the command and saves logs/status. Timeout kills the spawned process group. `--jobs` supplies common build parallelism hints; it is not a CPU quota.

Foreground workloads only: no daemons, detached processes, deployments or non-idempotent administration. Slots coordinate jobs sharing a workspace root, not external processes. A connection failure after submission is ambiguous. Use `status --host ALIAS --job JOB_ID` before retrying; do not launch a duplicate running job. A leftover slot is not proof its owner died; automatic stale-lock eviction is deliberately absent.

## Fetch and cleanup

```sh
python3 <skill>/scripts/remote_dev.py fetch --host linux-builder --job JOB_ID --paths results.xml,stdout.log,stderr.log --output /path/to/local-task/results
python3 <skill>/scripts/remote_dev.py cleanup --host linux-builder --job JOB_ID
```

Fetch accepts exact relative file paths, rejects traversal/symlinks and creates a new local output directory. It never overwrites source or existing files. Retrieve only necessary results. Cleanup validates ownership and refuses running/uncertain jobs; do not bypass it with arbitrary removal commands.

Report nonzero test exits as test failures, preserving logs. Report actual remote commands, artifacts and cleanup status. For remote source edits, fetch a patch and review/apply it separately.
