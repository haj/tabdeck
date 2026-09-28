# TabDeck

Voice-first control of always-on coding-agent sessions (**Claude Code** or **OpenCode**) from your phone, a web
page and your Mac. Sessions live in tmux on a server, keep running when your laptop sleeps, and appear in iTerm2 as
normal tabs. An assistant with a wake word ("Jarvis, what's going on?") tells you what each session is doing, reads
replies aloud, and approves, replies to or starts sessions.

TabDeck can use your own model and speech services, for example an [ODS](https://github.com/Osmantic/ODS) server
for the language model, Whisper and Kokoro.

## Quickstart
**You need:**
- a **Mac** with macOS 14+, iTerm2 (Settings → General → Magic → *Enable Python API*), Xcode Command Line Tools
  (`xcode-select --install`) and [uv](https://docs.astral.sh/uv/);
- a **Linux server** for the hub, with ssh from the Mac by key, `tmux`, and Claude Code or OpenCode installed and
  logged in;
- a **private network** between them and your phone, such as NetBird, Tailscale or WireGuard.

```sh
git clone https://github.com/haj/tabdeck.git && cd tabdeck
uv sync
uv run tabdeck setup        # asks 3–4 questions, deploys the hub, installs the Mac agent and the widget
```

Then say **"Jarvis, what's going on?"**. `setup` ends by pairing this Mac's browser and showing a QR code: scan it
with your phone, then add the page to the Home Screen. To pair more devices later, run `tabdeck pair` or use the
widget menu → **Pair a phone…**. Everything is configurable later from the widget (menu bar icon →
right-click → **Settings…**), and **Help…** in the same menu explains the rest.

**Prebuilt widget.** Each [release](../../releases) has `DeckWidget.zip`. Unzip it into `~/Applications`, then
right-click → *Open* the first time, because it is not notarized. `tabdeck setup` builds the widget from source
anyway.

## Pieces
- **Hub** (`tabdeck serve`, Linux server, systemd user service). Serves the web page and API. Each agent session
  is a window of one tmux session (default `deck`); the tmux server has its own service, so deploys never end
  sessions. `deck-save.sh` (cron, every minute) and `deck-start.sh` (at boot) bring sessions back after a reboot,
  resuming each folder's last conversation.
- **Status.** Claude Code reports through its hooks (`hooks/tabdeck-hook.sh`); OpenCode through a plugin
  (`opencode/plugin/tabdeck.js`) that posts the same events. Both give working / needs you / done, the last reply,
  spoken summaries and read-aloud.
- **Mac agent** (`tabdeck agent`, launchd). Keeps an iTerm tmux-integration (`tmux -CC`) connection to every
  reachable server, so each session window is an iTerm tab. It can also report the Mac's own iTerm tabs to the hub.
- **Widget** (`widget/`, SwiftUI). A floating, always-listening assistant with a wake word, voice activity
  detection and neural text-to-speech.
- **Phone.** The web page, added to the Home Screen.

## Configuration
Everything that differs between setups is in `<data dir>/settings.json`. The data dir is `~/.tabdeck`, or
`~/.tabdeck-<name>` with `TABDECK_INSTANCE=<name>`, so two setups can run side by side with separate ports, launchd
jobs, cron lines and tmux sessions.

| Setting | Default | Meaning |
|---|---|---|
| `agent` | `claude` | `claude` or `opencode` |
| `assistant_name` / `wake_word` | `Jarvis` / `jarvis` | e.g. `ODS` / `hey ods`, or any word |
| `port` | `8765` | hub and Mac agent port |
| `tmux_session` | `deck` | tmux session on servers |
| `llm_api`, `llm_url`/`ollama_url`, `intent_model`, `llm_key` | Ollama on localhost | the assistant's model; `openai` for OpenAI-compatible servers (e.g. ODS LiteLLM) |
| `stt_url` | (empty: Whisper on the Mac) | OpenAI-compatible speech-to-text, e.g. ODS Whisper |
| `tts_url`, `tts_voice` | (empty: system voice) | OpenAI-compatible text-to-speech (Kokoro) |
| `mac_tabs` | `true` | the Mac agent also reports the Mac's own iTerm tabs |

## Setup
You need a private network between the hub, the Mac and the phone (NetBird, Tailscale, WireGuard): TabDeck is
not designed to be exposed to the internet (see `SECURITY.md`). You also need ssh from the Mac to the hub with a
key.

```sh
make install-deps     # uv sync
uv run tabdeck setup  # asks a few questions, detects the rest over ssh, then deploys and installs
```

`tabdeck setup` does the following:
- asks for the hub's ssh target;
- detects its private-network IP (from NetBird, if it runs there), its name, and whether ODS is installed;
- suggests the agent, assistant name, wake word and port;
- writes the untracked `deploy.env` and this Mac's `settings.json` and `servers.json`;
- offers to deploy the hub, install the Mac agent and build the widget.

Every answer is also a flag (`tabdeck setup --help`). For example, a second setup next to the first:
```sh
uv run tabdeck setup --instance ods --host me@ods-server --agent opencode --ods   # "Hey ODS", port 8766
```
Pair phones and browsers with `tabdeck pair` (a QR code and link) or the widget menu → **Pair a phone…**.

## Using it
Say **"Jarvis, what's going on?"**, **"Jarvis, start Claude on server in myproject"**, **"tell api to run the
tests"**, **"approve"** or **"catch me up"**. Everything also works from the web page. See `docs/server-sessions.md`.

## Development
`make test` (pytest; the tmux script tests need tmux). The widget: `cd widget && swift build && swift test`.

**Releases.** Push a tag such as `v0.2.0`; only repository admins can, because of the "release tags" ruleset.
`.github/workflows/release.yml` then runs three jobs:
1. **test** (no secrets): runs every test.
2. **sign** (environment `release`, usable only by `v*` tags): builds the widget app, signs it and notarizes it.
3. **publish** (no secrets): creates the GitHub Release with `DeckWidget.zip` and its SHA-256.

The signing secrets live only in the `release` environment (Settings → Environments → release):
- `MACOS_CERT_P12` (base64 Developer ID Application certificate), `MACOS_CERT_PASSWORD`;
- `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`.

Without them the app is ad-hoc signed. Actions are pinned to commits, only GitHub's own actions and `setup-uv` may
run, and workflow tokens are read-only by default.
