# Incident response

Use read-only evidence first. Avoid repeated restarts because they erase timing evidence and can amplify failures.

## Service unavailable

Collect:

```bash
sudo systemctl status dp-game.service --no-pager -l
sudo journalctl -u dp-game.service -n 150 --no-pager
sudo ss -lntp
readlink -f /opt/dp-game/current
```

Interpretation:

- Service inactive/failed: inspect the first startup error before considering one restart.
- Service active but no `:8888`: inspect Skynet gate/config errors.
- `0.0.0.0:8888` listening but public TCP fails: inspect the Alibaba Cloud Simple Application Server firewall; `ufw` is not the only layer.
- TCP works but WebSocket fails: check WebSocket path `/`, handshake logs, protocol envelope, and whether the client used `ws://` versus `wss://`.

## Database failure

Collect without exposing secrets:

```bash
sudo systemctl status mysql --no-pager -l
sudo journalctl -u mysql -n 100 --no-pager
stat -c '%U:%G %a %n' /opt/dp-game/shared/mysql.password
```

Run a count-only application-user query with the password loaded on the remote host. Do not print `MYSQL_PWD`, config contents, hashes, tokens, or rows. Distinguish service-down, authentication failure, missing schema, and exhausted resources before changing anything.

## Resource pressure

Collect:

```bash
free -h
swapon --show
df -h /
ps -eo pid,comm,%cpu,%mem,rss --sort=-rss | head -n 15
sudo journalctl -k -g 'oom\|Out of memory' -n 50 --no-pager
```

Record the server's normal memory and swap baseline before incidents. Swap is protection, not capacity. Sustained swap usage, OOM logs, or high tick latency should lead to load reduction or a larger instance, not aggressive service tuning without measurements.

## Host identity or SSH failure

- On host-key mismatch, stop. Compare the presented ED25519 fingerprint with `DP_GAME_HOST_FINGERPRINT` through the cloud console before touching `known_hosts`.
- Never request, print, or copy private-key content. A public-key comment is not proof of host identity.
- If SSH is unreachable, distinguish public port/firewall failure from authentication failure. Use the Alibaba Cloud browser remote terminal only for recovery.

## Useful recovery boundary

One carefully justified restart may be appropriate after collecting evidence. If the same failure repeats, stop retrying and report the exact error, current release, service state, and required user decision.
