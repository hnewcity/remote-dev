# remote-dev

For installation, language switching, examples, and the full reference, see the [project README](https://github.com/hnewcity/remote-dev#readme).

This folder is the self-contained skill package. Keep `scripts/`, `references/`, `assets/`, and `agents/` with `SKILL.md` when installing it.

Remote Dev guides SSH host enrollment and moves suitable builds, tests, and analysis commands to approved development machines. No host is enabled by default. Personal authorization lives separately at `~/.config/agent-remote/hosts.json`.

Requirements: macOS/Linux, Python 3.9+, OpenSSH and Git locally; macOS/Linux and Python 3.9+ on targets. The Python scripts use only the standard library.

```bash
python3 scripts/remote_dev.py --help
python3 scripts/remote_dev.py init
python3 scripts/remote_dev.py discover
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```

Discovery does not grant execution permission. Use the guided setup in `SKILL.md` before enabling hosts or transferring source.
