# Contributing

Use fictional SSH aliases and synthetic or temporary repositories in issues, examples, and tests. Never include private keys, real host registries, credentials, or customer source code.

## Development

Python 3.9+ and Git on macOS/Linux are enough; no third-party Python packages are required.

```bash
python3 -m unittest discover -s skills/remote-dev/scripts -p 'test_*.py' -v
```

Keep tests offline. Do not enable a contributor's real host or run remote commands as part of the test suite.

## Changes

- Keep permission decisions separate from capability discovery. Refreshing facts must never enable a host or restore revoked permission.
- Preserve working-tree edits and source-transfer exclusions. Explain changes that affect data sent over SSH.
- Keep job ownership, timeout, artifact-path, and cleanup behavior covered by observable tests.
- Match documentation to supported behavior. Admission checks and concurrency hints must not be described as hard resource limits or a security sandbox.
- Keep the English and Simplified Chinese READMEs consistent. Examples must use fictional hosts and documented CLI options.

When reporting a bug, provide the command, Python/OS versions, expected behavior, and a redacted error. A minimal synthetic repository is preferred to a real project snapshot.
