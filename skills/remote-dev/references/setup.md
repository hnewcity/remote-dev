# Guided enrollment

1. Run `python3 <skill>/scripts/remote_dev.py init`. It creates an empty private registry only if absent and preserves existing decisions.
2. Run `... discover`. Explicit SSH aliases and recursive Include files are read, then `ssh -G` resolves effective settings. Wildcards are not hosts. This does not connect, scan subnets, or mine shell history.
3. Present alias/address/user/key-path/jump-host candidates. For setup, bounded `probe --host ALIAS` checks can read OS, architecture, CPU allocation, RAM, workspace free disk, tool paths, and an opaque machine fingerprint. They do not install tools or write remote files. External identity agents are supported by normal SSH; probe success is stronger evidence than key-file presence.
4. Ask which hosts may be used and which must not be used. Obtain allowed purposes (`build`, `test`, `analysis`), permission to receive project source, project/data restrictions and resource limits. Offer one concurrent job, 2 GiB available RAM and 10 GiB free disk as conservative defaults. Present the concrete proposed choices before persisting them; respect authorization already supplied in the conversation.
5. Record explicit decisions:

```sh
python3 <skill>/scripts/remote_dev.py allow --host linux-builder --tasks build,test --source-sync --constraints 'Personal repositories only; no customer data'
python3 <skill>/scripts/remote_dev.py deny --host production --reason 'Production host'
```

`allow` persists a user decision and binds effective SSH settings; invoking it is not itself evidence of consent. Never call it without an explicit human choice. `deny` is remembered, including on later discovery. Defaults remain disabled.

6. Run `... list` and show the saved choices. A remote smoke test may run only after enrollment. Installation with no enabled hosts is complete and awaiting setup, not a failure.

## Existing inventories and failures

- Import old inventory only as candidates/facts: `enabled: false`, `decision: pending`, `allowed_tasks: []`. A CSV or JSON with reachable hosts is not approval.
- `refresh --host ALIAS` updates facts, preserving policy, only for enabled hosts. Setup `probe` is separate and does not enroll.
- Compare opaque machine fingerprints to identify aliases of one host. Keep LAN/WAN aliases if useful, but give them the same workspace root and avoid duplicate scheduling.
- `select` does not probe disabled/denied hosts. Do not keep suggesting a denied host without a user-requested reconfiguration.
- Unknown/changed host keys require the user's normal SSH fingerprint-verification workflow. Do not repair with `StrictHostKeyChecking=no`.
- Python/SSH/runtime failures remain explicit unknowns; do not install dependencies during discovery.

## Distribution

Copy the `remote-dev` folder to the target agent's configured personal skill directory. Codex commonly discovers `~/.agents/skills`; existing installations may use `~/.codex/skills`. Claude Code uses `~/.claude/skills`. Preserve the user's established discovery path and avoid duplicates. Core files use the open `SKILL.md` layout; verify discovery in the intended application. The reusable archive must exclude user registries, inventories, keys, job data and logs. A plugin package can be added later for catalog distribution.

Run offline behavioral checks with `python3 -m unittest discover -s <skill>/scripts -p 'test_*.py'`. Tests use temporary local repositories/workspaces and never contact real SSH hosts.
