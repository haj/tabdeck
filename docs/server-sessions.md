# Server sessions: always-on agents, auto-attached in iTerm

Agent sessions (Claude Code or OpenCode) can run on servers instead of your Mac. They keep running when the Mac
sleeps, and each one appears in iTerm as a normal tab without you doing anything.

## How it works
- **One tmux session per server.** Each server runs one tmux session (default `deck`, setting `tmux_session`),
  with one window per agent session, named after the project (`api`, `api-2`, …).
- **iTerm tabs.** The Mac agent keeps one iTerm tmux-integration connection (`tmux -CC`) open to every reachable
  server in `<data dir>/servers.json`. iTerm shows each window as a native tab. A window created on the server (by
  voice, from the phone or by hand) becomes a tab within a second.
- **Network drops.** A drop closes that server's tabs. They come back about 15 s after the server is reachable
  again, and the sessions keep running throughout.
- **Reboots.**
  - `<data dir>/deck-save.sh` (cron, every minute) records the agent windows.
  - After a reboot, `deck-start.sh` brings them back, resuming each folder's last conversation (`claude --continue`
    or `opencode --continue`).
  - On the hub it runs from a systemd user unit; on other servers from `@reboot` cron.
- **Passwords.** The Mac never opens a gateway to a server whose ssh asks for a password. The agent log then says
  `needs a password or key`.

## Add a server
The server needs:
- ssh from the Mac with a key;
- `tmux` and the agent installed (and logged in once);
- its projects in one folder under home (default `~/Projects`).

```sh
make server HOST=me@build-box NAME=build-box                # projects in ~/Projects
make server HOST=gpu NAME=gpu PROJECTS=work                 # an ~/.ssh/config alias, projects in ~/work
```

This copies the session scripts and `agent.sh` to the server's data folder, installs the cron lines, starts the
tmux session, adds the server to `servers.json`, and restarts the Mac agent. It reports a missing `tmux` or agent
instead of installing it.

## Start a session
| From | How |
|---|---|
| Voice | "Jarvis, start Claude in api" / "… on build-box in api" |
| Phone / web page | ＋ |
| On the server | `tmux new-window -t =deck: -n api -c ~/Projects/api claude` |

On the hub, a project that isn't there yet is cloned from its git remote (from the Mac's `export-projects`).

## Move a Mac session to a server (Claude Code)
```sh
tabdeck move api build-box                  # the project's most recent conversation
tabdeck move api build-box --session <id>   # a specific one
```

This does three things:
1. Copies the project, leaving out `node_modules`, `.gradle`, `build`, `Pods` and `DerivedData`.
2. Copies the conversation and the project's memory, with folder paths rewritten.
3. Resumes it with `claude --resume` in a new window.

The Mac session keeps running: close it once the new tab works. Move sessions while they are idle, because the
history is copied once. The moved session may miss things that exist only on the Mac, such as ssh keys for the
hosts it uses, tools in `~/bin`, and MCP servers.

## Everyday rules
- **Closing a server tab in iTerm ends that session**, because it kills the tmux window. To hide a tab instead,
  use Shell → tmux → Dashboard.
- **Don't attach extra tmux views to the session.** iTerm's tmux integration can drop the real tab when such a view
  closes. If a tab vanishes while its session still runs, detach iTerm's control client; the agent reattaches and
  rebuilds the tabs:
  `ssh <server> "tmux list-clients -F '#{client_control_mode} #{client_name}' | awk '\$1==1{print \$2}' | xargs -n1 tmux detach-client -t"`
- **The hub sees other servers through the Mac.** While the Mac sleeps, the phone shows the hub's own sessions
  only; every session keeps running.

## Troubleshooting
| Symptom | Check |
|---|---|
| No tabs for a server | `grep gateway <data dir>/service.log`: "needs a password or key" means fix the ssh key. Nothing logged means the server is unreachable or not in `servers.json`. |
| Server shown offline | The Mac must be awake and reach the server. |
| Session missing after reboot | `cat <data dir>/<session>.tsv` on the server; `crontab -l` must show the `# tabdeck` line. |
