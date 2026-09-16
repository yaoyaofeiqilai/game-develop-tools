---
name: dp-game-server-ops
description: Operate, deploy, diagnose, and maintain a configured DP Game Skynet server. Use for SSH access, health checks, logs, versioned releases, rollback, MySQL, systemd, and production WebSocket troubleshooting. Do not use for Godot client or art changes.
---

# DP Game Server Operations

Operate the existing DP Game server conservatively. Start with read-only checks, preserve recoverability, and treat deployment, restart, rollback, firewall, database, and package changes as external mutations requiring authorization appropriate to the user's request.

## Local configuration

Keep machine- and account-specific values outside the skill. Configure these environment variables locally:

- `DP_GAME_SSH_HOST`: server hostname or public IP.
- `DP_GAME_SSH_USER`: SSH user.
- `DP_GAME_SSH_PORT`: SSH port; defaults to `22` in the bundled scripts.
- `DP_GAME_SSH_KEY`: absolute path to the private key.
- `DP_GAME_KNOWN_HOSTS`: known-hosts file; defaults to `$HOME/.ssh/known_hosts`.
- `DP_GAME_HOST_FINGERPRINT`: expected server host-key fingerprint, obtained through the cloud console.
- `DP_GAME_PROJECT_ROOT`: local project root when repository-specific documents are needed.
- `DP_GAME_SERVER_REPO`: local or WSL server repository path.

Do not commit a `.env`, private key, real IP address, username, fingerprint, account identifier, or credential-bearing config with this skill.

Project runtime defaults that are safe to keep in the reusable instructions:

- systemd unit: `dp-game.service`; MySQL unit: `mysql.service`.
- Release root: `/opt/dp-game/releases`; active symlink: `/opt/dp-game/current`.
- Persistent secret directory: `/opt/dp-game/shared`; DB password file: `/opt/dp-game/shared/mysql.password` (`0600`).
- Pinned Skynet commit: `2251550a785480fb04c343da1eb8b42f9a8484fd`.
- WebSocket port: `8888` unless the deployed config says otherwise.

## Safety invariants

- Never print, download, copy, or commit the SSH private key, AccessKeys, account passwords, tokens, `accounts.json`, or `mysql.password`.
- Never open MySQL port `3306` publicly. MySQL must remain reachable only through `127.0.0.1` unless the user explicitly designs a different secured topology.
- Use `StrictHostKeyChecking=yes` after the known-host entry exists. Stop and alert the user on any host-key mismatch.
- Resolve and inspect exact paths before overwrite, symlink replacement, rollback, or deletion. Never delete `/opt/dp-game`, the release root, database files, or broad paths recursively.
- Before upload, check whether the exact remote destination exists. Use a unique immutable release directory and refuse overwrite.
- Do not deploy from a dirty local Git worktree unless the user explicitly chooses that exact working tree as a candidate. Record base commit and dirty status in release metadata.
- Run syntax and relevant regression tests before packaging. Exclude `.git`, `.debug`, `.vscode`, local tests/debug artifacts, compiled local Skynet output, `data/accounts.json`, and `data/mysql.password`.
- Do not change fields governed by `<PROJECT_ROOT>/shared/API.md` as part of operations work.
- Restart/stop, rollback, package upgrade, firewall change, DB mutation, and release deletion need a clear impact statement and user authorization unless already unambiguously requested.
- Keep at least one previously known-good release. Never clean old releases during the same step that activates a new one.

## Start every task

1. Read `<PROJECT_ROOT>/shared/PROTOCOL.md` and the applicable repository `AGENTS.md` when they exist.
2. Inspect local `git status` and remote current state. Preserve unrelated or other-agent changes.
3. For health/status requests, run `scripts/health-check.ps1` and remain read-only.
4. For log requests, run `scripts/logs.ps1`; avoid exposing credential-bearing payloads.

## Route by operation

- Health, status, resource pressure, port checks: use `scripts/health-check.ps1`.
- Logs and startup failures: use `scripts/logs.ps1`, then read [references/incident-response.md](references/incident-response.md).
- New release, configuration change, rollback, or dependency rebuild: read [references/deployment.md](references/deployment.md) before mutating anything.
- Firewall: remember there are two layers. Ubuntu `ufw` is currently inactive; the Alibaba Cloud Simple Application Server firewall controls public reachability. Port `8888/TCP` is currently open.

## Standard SSH invocation

On Windows, read connection values from the local environment and call OpenSSH explicitly:

```powershell
$sshExe = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$knownHosts = if ($env:DP_GAME_KNOWN_HOSTS) { $env:DP_GAME_KNOWN_HOSTS } else { "$HOME\.ssh\known_hosts" }
$target = "$($env:DP_GAME_SSH_USER)@$($env:DP_GAME_SSH_HOST)"
& $sshExe `
  -i $env:DP_GAME_SSH_KEY `
  -p $(if ($env:DP_GAME_SSH_PORT) { $env:DP_GAME_SSH_PORT } else { "22" }) `
  -o BatchMode=yes -o IdentitiesOnly=yes `
  -o StrictHostKeyChecking=yes `
  -o "UserKnownHostsFile=$knownHosts" `
  $target "<remote command>"
```

Each remote invocation is independent. Put commands requiring shared `cd` or variables into one quoted remote command and use `set -euo pipefail` for mutation workflows.

## Operational boundaries

- Do not assume the configured cloud product, region, firewall layer, hardware size, or current release; inspect them at task start.
- Treat unsolicited public WebSocket connections as routine internet scanning, not valid players. For private testing, restrict the cloud firewall source CIDR; for public use, retain application authentication, add connection/rate limits, and monitor abuse.
- `ws://` is unencrypted. Treat an IP endpoint as testing/demo infrastructure and do not use real sensitive passwords. Prefer a domain plus TLS/WSS before public production use.
