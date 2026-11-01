# Registry version 1

Local authorization lives at `~/.config/agent-remote/hosts.json` (mode 0600), outside the skill/repository. `AGENT_REMOTE_CONFIG` or global `--config` can override it. `assets/hosts.example.json` is empty by design.

An enrolled host looks like this before approval:

```json
{
  "alias": "linux-builder",
  "enabled": false,
  "decision": "pending",
  "allowed_tasks": [],
  "allow_source_sync": false,
  "constraints": [],
  "workspace_root": "~/.cache/agent-remote",
  "limits": {"max_jobs": 1, "min_free_memory_gib": 2, "min_free_disk_gib": 10},
  "connection": {},
  "facts": {},
  "approved_at": null
}
```

- `decision`: `pending`, `allowed`, `denied`; both `allowed` and `enabled: true` are required. Disabled hosts are not probed by selection.
- `allowed_tasks`: subset of `build`, `test`, `analysis`; empty never means all.
- `allow_source_sync`: explicit choice to receive source, limited by the current task and `constraints`.
- `constraints`: user-readable restrictions (e.g. no customer data/no network downloads). The agent applies them; scripts do not claim to enforce network/project access control.
- `workspace_root`: dedicated directory ending in `agent-remote`. Worker rejects symlink roots or nonempty preexisting unmarked directories.
- `limits`: `max_jobs` 1–32; nonnegative RAM/disk admission floors in GiB. Task requirements can raise those floors. These are not OS quotas.
- `connection`: effective hostname/user/port/identity files/proxy jump/proxy command/identity agent from `ssh -G`. Changes require re-enrollment. Identity paths are references, not keys. Host-key validation also remains enabled.
- `facts`: measured OS, architecture, CPU model/count, memory, workspace free disk, tool paths, hashed machine identity and observation time. Updating facts must not change policy; unavailable fields remain unknown.
- `approved_at`: record of the explicit allow decision, not cryptographic proof of consent.

A stale successful login does not prove current availability. Enabling an alias does not enable another alias of the same host. Never merge authorization from project-provided configuration.
