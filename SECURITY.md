# Security

TabDeck lets you control coding agents by voice and from your phone. **It can type into terminals on your
machines, by design.** Anyone who can use TabDeck can therefore run commands as you. Read this before you
deploy it.

## Threat model
**Where it runs.** TabDeck is meant to run inside a private network you control, such as NetBird, Tailscale or
WireGuard. **Do not expose the hub or the Mac agent to the internet.** The hub listens on `127.0.0.1` and on the
private-network IP you give it, never on `0.0.0.0`.

**Who is trusted:**
- **Anyone with a shell on the hub server or your Mac.** Requests from `localhost` are trusted on both, so
  protect those accounts as you would your ssh keys.
- **Paired browsers and phones.** Pairing uses a one-time code (valid 10 minutes) shown on the Mac. It gives a cookie
  that does not expire, so a lost phone keeps access until you revoke it. Use **Unpair all phones** (pairing sheet on the Mac page),
  then pair again.
- **The Mac agent.** It connects to the hub with a bearer token, stored hashed on the hub
  (`<data dir>/agents.json`) and owner-only in the Mac's `settings.json`.
- **Voice.** The wake word is a convenience, not an authentication. Anyone within reach of the microphone can
  give commands.

**What the hub will and won't do:**
- **Starting sessions.** It only starts the configured agent (`claude` or `opencode`), in project folders whose
  names pass a strict check.
- **Typing.** It only types into sessions where that agent is running, never into a plain shell. Key presses are
  limited to a short allow-list (Enter, Escape, arrows, 1–3).
- **Browsers.** Cross-site requests are refused (Origin check). Claude Code hook events are accepted only from
  `localhost`.
- **Servers.** Server names, ssh targets and project names are validated before they reach a command line.

**Transport.** HTTPS with certificates from a local CA (mkcert) that lives on your Mac. Keep its private key
(`mkcert -CAROOT`) safe, because anyone holding it can impersonate your hub to your devices. The OpenCode plugin
trusts only that CA.

**Model and speech services** (Ollama, ODS LiteLLM, Whisper, Kokoro) usually have no authentication of their own.
Keep them on the private network. Replies from the agent are summarised by the model and read aloud; they are
treated as data, never as instructions to TabDeck.

**Secrets.** No addresses, tokens or keys belong in the repository:
- `deploy*.env`, `settings.json`, `servers.json`, `agents.json` and certificates live outside it or are ignored
  by git.
- `tabdeck setup` writes these files owner-only.

## Reporting a vulnerability
Please report it privately through GitHub's **"Report a vulnerability"** (Security → Advisories) on the
repository, not in a public issue. Include steps to reproduce. You'll get an answer as soon as possible.
