# Reaching the Deck and the test PC

This project spans three machines. This doc is the map between them —
written for whoever (owner or a Claude Code session) is working from the
owner's Mac and needs to reach the other two.

- **This repo's clone and the game server** live on a **Steam Deck**
  (SteamOS), reachable over SSH as `steamdeck`.
- A **Windows PC** runs a Palworld *client* kept connected to the server for
  manual/agent-driven testing, reachable only by hopping through the Deck.

See `CLAUDE.md` (gitignored, Deck-local) for the full project context —
architecture, systemd units, mods, and working conventions. This file is
just the network map, safe to keep in the public repo.

## Steam Deck

```sh
ssh steamdeck                  # LAN, at home
ssh -o HostName=100.111.186.106 steamdeck   # Tailscale tailnet, away from home
ssh steamdeck '<command>'
```

- The Deck's LAN IP is static, set via NetworkManager on the Deck itself.
- The Mac, Deck and PC share a Tailscale tailnet. Use the Deck's tailnet
  address when off the home network.
- No `hostname` binary on the Deck — use `cat /etc/hostname`.
- A persistent Claude Code session runs on the Deck in tmux
  (`claude-session.service`, `KillMode=none` — see the persistence section
  in the local CLAUDE.md before touching this unit). Attach with
  `~/.local/bin/claude-attach` on the Deck, or via Remote Control.

## Windows PC (Palworld test client)

The PC's SSH key lives **on the Deck**, not on the Mac — always hop through
the Deck:

```sh
ssh steamdeck 'ssh palpc "<windows cmd>"'
ssh steamdeck 'scp palpc:C:/palctl/cap.png /tmp/' && scp steamdeck:/tmp/cap.png .
```

- The PC's remote shell is **cmd.exe** — chain commands with `&`, not `;`.
- It sits on the outer subnet of a double NAT and can't reach the Deck's LAN
  IP directly; it connects to the game server over the Tailscale tailnet
  instead. RDP is on a non-default port — check the Deck-local CLAUDE.md if
  you need it.
- **Driving the Palworld client** (screenshots, keyboard/mouse) goes through
  `pcagent/agent.py`, run by a scheduled task in the PC's interactive console
  session — a plain SSH session can't screenshot or send input (separate
  window station). The agent polls `C:\palctl\queue\*.cmd` (JSON) and writes
  `<id>.out`; write files directly rather than via a temp-file-then-rename
  dance, which silently fails here.
- `pydirectinput`'s navigation keys are **numpad scancodes**
  (`delete`/`end`/`home`/arrows). With NumLock on they type digits/`.`
  instead of navigating — turn NumLock off before sending them.

## Health check (read-only)

```sh
ssh steamdeck 'systemctl is-active sshd; systemctl --user is-active palserver.service claude-session.service; \
  ~/tailscale/tailscale --socket=$HOME/tailscale/tailscaled.sock status'
ssh steamdeck 'ssh palpc "query session & sc query sshd | findstr STATE & schtasks /query /tn PalCtlAgent /fo list | findstr Status & tasklist /fi \"imagename eq Palworld-Win64-Shipping.exe\" /nh"'
```

Healthy: sshd active; all three tailnet nodes present; on the PC, the
console session is **Active** (not `Disc`), sshd RUNNING, `PalCtlAgent`
Running, and the game process present.

(`--socket=$HOME/...` needs `$HOME`, not `~` — a literal `~` after `=` isn't
shell-expanded there and the check fails with "no such file".)

## Re-opening access when something is down

| Symptom | Fix | Who |
|---|---|---|
| `ssh steamdeck` refused/timing out | On the Deck (Desktop Mode → Konsole): `sudo systemctl enable --now sshd`; confirm the LAN IP with `ip -4 addr`. | Owner, at the Deck |
| Tailnet IPs unreachable | On the Deck: `sudo systemctl restart tailscaled`. Re-advertising routes needs a longer command (Deck-local CLAUDE.md). | Owner — Claude's permissions block the advertise step |
| `ssh palpc` fails from the Deck | On the PC, in admin PowerShell: `Start-Service sshd; Set-Service sshd -StartupType Automatic`; check the Windows firewall allows TCP 22. | Owner, at the PC or via RDP |
| PC console session shows `Disc` (no desktop, so screenshots/input fail) | Reconnect headlessly: a SYSTEM scheduled task running `tscon <id> /dest:console`. No RDP needed. | Claude over SSH, or the owner |
| `PalCtlAgent` not running | `ssh steamdeck 'ssh palpc "schtasks /run /tn PalCtlAgent"'` | Claude |

## Ground rules

- Don't commit, push, or merge without the owner's go — work on a feature
  branch; the owner reviews (often with `/ultrareview`) before anything
  reaches `main`.
- Never print or copy secrets (`config.json`, `key.pem`/`cert.pem`, API
  tokens) off the Deck.
- Don't drive another Claude session's terminal via `tmux send-keys` or
  similar — use cross-session messaging, or go through the owner.
- If a permission classifier blocks an action, stop and tell the owner
  rather than routing around it through another session or machine.
