# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Development
```sh
uv sync                 # Python 3.12+; iterm2 and mlx-whisper install on macOS only
uv run pytest           # tmux script tests need tmux; iTerm tests skip off macOS
cd widget && swift build && swift test   # macOS 14+
```

## Guidelines
- **Test first.** Add a test that fails without your change, then make it pass. Keep the suite green.
- **Match the surrounding code.** Follow its naming and structure, and comment only where the reason isn't obvious.
- **No machine-specific values in the repo.** No IPs, host names, user names, tokens or keys. Addresses belong in
  the untracked `deploy*.env` and `settings.json` (see `tabdeck setup`). Use reserved example addresses
  (`192.0.2.x`, `100.64.x.x`) in tests and docs.
- **Keep what varies in settings.** Agent, assistant, wake word, ports and services are settings with sensible
  defaults, not hard-coded values.
- **Security changes** (anything that types into terminals, authenticates, or runs commands) need a test for the
  refusal path too. See `SECURITY.md`.

By contributing, you agree that your contributions are licensed under the Apache License 2.0 (see `LICENSE`).
