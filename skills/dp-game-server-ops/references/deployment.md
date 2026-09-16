# Deployment and rollback

Read this file before any release, configuration change, rebuild, activation, or rollback.

## Preflight

1. Confirm `DP_GAME_SSH_HOST`, `DP_GAME_SSH_USER`, and `DP_GAME_HOST_FINGERPRINT` against the cloud console.
2. Run the health-check script and capture the current release target, service state, MySQL state, free disk, memory, and swap.
3. In `DP_GAME_SERVER_REPO`, inspect `git status`, `git diff --check`, recent commits, and the Skynet commit.
4. If dirty, stop and ask whether to deploy the last commit or the exact dirty candidate. Do not silently choose.
5. Run at least:

```bash
cd <SERVER_REPO>/server
bash test/check_lua_syntax.sh
bash test/run_boss_regress.sh
```

Run the broader relevant WebSocket regression when the changed subsystem warrants it.

## Package invariants

Build a small release payload containing current `service/`, `lualib/`, `sql/`, the MySQL setup tool when needed, and `<GAME_DATA_DIR>` as `server/game-data/`.

Production config differences:

```lua
thread = 4
ws_host = "0.0.0.0"
ws_port = 8888
data_path = "./game-data"
mysql_host = "127.0.0.1"
mysql_port = 3306
mysql_password_file = "/opt/dp-game/shared/mysql.password"
```

Do not include local account data, local DB passwords, editor/debug folders, test scratch files, Git metadata, or local compiled Skynet artifacts. Generate a manifest with file SHA-256 values and release metadata containing source commit, dirty status, and pinned Skynet commit.

## Stage and build

1. Choose a unique release ID such as `<UTC timestamp>-<short commit>`; add `-wt` only for an explicitly approved dirty candidate.
2. Verify the exact remote upload path is absent before upload.
3. Upload to a temporary path, calculate SHA-256 remotely, and compare with the local archive before extraction.
4. Create `/opt/dp-game/releases/<release-id>` owned by the configured deployment user; refuse if it exists.
5. Extract and verify the package manifest.
6. Clone Skynet inside the release, detach at `2251550a785480fb04c343da1eb8b42f9a8484fd`, initialize submodules, and run `make linux`.
7. Verify the executable exists and the checked-out commit is exact.

Required build/runtime packages currently installed include `build-essential`, `git`, `autoconf`, `automake`, `libtool`, `mysql-server`, `mysql-client`, and `ca-certificates`. Do not run broad upgrades merely because updates are available.

## Database

- Database: `dp_game`; application user: `dp_game_app`; table: `accounts`.
- Run schema/setup only when needed and only from a reviewed script. It must be idempotent and must not display the generated password.
- Validate with a count-only query using `MYSQL_PWD` populated on the remote host from `/opt/dp-game/shared/mysql.password`. Never transmit the password back to the local machine.
- Before migrations that can transform or delete data, create and verify a database backup and obtain explicit approval.

## Activate

Before changing the active symlink, ensure the new release has built successfully and the prior release path is recorded.

For first install, create `/opt/dp-game/current` as a symlink to the release and install `dp-game.service`. For later releases, atomically replace the symlink only after explicit activation approval. Then restart `dp-game.service` and immediately verify:

- `systemctl is-active dp-game.service` returns `active`.
- `systemctl is-enabled dp-game.service` returns `enabled`.
- `ss -lntp` shows `0.0.0.0:8888` owned by `skynet`.
- Logs show MySQL connected and the WebSocket gate listening.
- A public TCP probe succeeds.
- A protocol smoke test covers random test registration/login, room creation, ready/start, and receipt of `s.state`.

Do not declare deployment complete on port-open status alone.

## Rollback

Rollback changes live service state. Describe the target release and expected interruption, then get approval.

1. Record `readlink -f /opt/dp-game/current` and current service logs.
2. Verify the exact previous release directory, executable, config, and manifest.
3. Atomically repoint `current` to that release.
4. Restart `dp-game.service` once.
5. Repeat service, port, logs, DB connectivity, and WebSocket smoke checks.
6. If rollback fails, restore the formerly active symlink and stop for user direction rather than cycling releases.

Release rollback does not roll back MySQL schema or data. Treat DB rollback as a separate, backup-driven operation.
