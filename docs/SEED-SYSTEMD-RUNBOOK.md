# Seed crawler systemd runbook

Wraps `crawl_seed.py` in a systemd unit on each seed droplet so the crawl survives kernel upgrades, panic reboots, provider-initiated reboots (e.g. Vultr CPU-abuse throttle), and process crashes.

## Unit file

`/etc/systemd/system/ots-seed.service`:

```ini
[Unit]
Description=OpenTrustSeal seed crawler
Documentation=https://opentrustseal.com
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ott
Group=ott
WorkingDirectory=/opt/opentrusttoken
Environment=OTS_ENABLE_WAYBACK_TIER=1
Environment=OTS_ENABLE_PROBE_TIER=1
ExecStart=/opt/opentrusttoken/venv/bin/python3 /opt/opentrusttoken/crawl_seed.py data/domains.txt --fast --workers 2 --delay 400 --resume
Restart=on-failure
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=5
StandardOutput=append:/var/log/ots-seed.log
StandardError=append:/var/log/ots-seed.log
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

## Rationale for each knob

- **`Type=simple`** -- unit considers the service started when the main Python process is spawned. crawl_seed.py doesn't daemonize.
- **`User=ott`** -- matches the existing nohup invocation pattern on all seeds.
- **`Environment=OTS_ENABLE_WAYBACK_TIER=1`** and `OTS_ENABLE_PROBE_TIER=1` -- turn on Tier 5 (Wayback) and Tier 1.5 (protocol probe) in the fetch ladder. Present in the original nohup invocation; carried over verbatim.
- **`--fast --workers 2 --delay 400 --resume`** -- fast mode (no Playwright tiers 2-4), 2 worker processes, 400ms per-request delay, resume from checkpoint. The 2-worker + 400ms combo is what we tuned to stay below Vultr's CPU abuse threshold after the 2026-04-20 throttle incident.
- **`Restart=on-failure`** + `RestartSec=30` -- if python exits non-zero, wait 30s and retry. A 30s back-off avoids tight-loop restarts if the failure is environmental (e.g. WHOIS server down).
- **`StartLimitBurst=5`** + `StartLimitIntervalSec=600` -- give up and stay stopped if it fails 5 times in 10 minutes. Prevents infinite restarts on a genuinely broken install.
- **`CPUQuota=80%`** -- cgroup-level cap on total CPU used by the unit. Leaves 20% headroom for system tasks and nudges us below Vultr's detection threshold. This cap is per-host, not per-process, and applies to the whole unit's cgroup.
- **`KillMode=mixed`** + `TimeoutStopSec=30` -- on stop, SIGTERM the main process and wait up to 30s for graceful checkpoint flush. Then SIGKILL. The crawl_seed.py SIGTERM handler writes the current progress to `.seed-checkpoint.json`.

## One-shot install on a seed

Assumes you have SSH root on the seed and `/tmp/ots-seed.service` already uploaded.

```bash
# On the seed box, run:
cp /tmp/ots-seed.service /etc/systemd/system/ots-seed.service
chmod 644 /etc/systemd/system/ots-seed.service
chown root:root /etc/systemd/system/ots-seed.service

# Log file owned by ott so the service can append
touch /var/log/ots-seed.log
chown ott:ott /var/log/ots-seed.log
chmod 664 /var/log/ots-seed.log

# Kill any existing nohup-launched crawl so systemd can take over cleanly.
# The --resume flag means no data is lost -- the checkpoint picks up exactly
# where the killed process was.
pkill -TERM -f crawl_seed 2>/dev/null || true
sleep 3
pkill -KILL -f crawl_seed 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now ots-seed.service
sleep 4
systemctl status ots-seed.service --no-pager --lines=5
```

## Fan-out across multiple seeds (from dev Mac)

```bash
# Prepare the files locally
cat > /tmp/ots-seed.service <<'EOF'
<paste the unit file above>
EOF

cat > /tmp/install_systemd.sh <<'EOF'
<paste the install commands above, wrapped in set -e>
EOF

# Fan out sequentially (output capture via stdin-pipe, works reliably)
for ip in IP1 IP2 IP3 ...; do
  echo "=== $ip ==="
  scp -q /tmp/ots-seed.service root@$ip:/tmp/ots-seed.service
  ssh root@$ip 'bash -s' < /tmp/install_systemd.sh
done
```

Sequential (not parallel) is intentional: output capture through `( ssh ... ) &` subshells is lossy and hard to debug. ~10s per seed, so 10 seeds takes ~2 minutes.

## Ops

```bash
# Check service state
systemctl status ots-seed --no-pager

# Tail logs
journalctl -u ots-seed -f
# Or the raw log file:
tail -f /var/log/ots-seed.log

# Graceful stop (flushes checkpoint via SIGTERM handler)
systemctl stop ots-seed

# Restart (picks up from checkpoint)
systemctl restart ots-seed

# Disable auto-start on boot (but keep running)
systemctl disable ots-seed

# Adjust worker count or delay without rewriting the whole unit:
systemctl edit ots-seed
# then add:
# [Service]
# ExecStart=
# ExecStart=/opt/opentrusttoken/venv/bin/python3 /opt/opentrusttoken/crawl_seed.py data/domains.txt --fast --workers 3 --delay 300 --resume
```

## Matches to other fleet state

- **Vultr seeds 9-18:** managed by this unit as of 2026-04-20, workers=2, delay=400ms, CPUQuota=80%
- **DigitalOcean seeds 1-6:** NOT yet systemd-managed. Running nohup at workers=4, delay=200. Seeds 1-6 have ~3-5h left at the rate they are moving, so the cost of the switchover outweighs the benefit. If one crashes before finishing, just re-launch via nohup and let --resume pick up.
- **DigitalOcean seeds 7-8:** NOT yet systemd-managed. Running nohup at workers=4, ETA 2-3 days. Worth adding the unit for crash resilience -- same pattern, just adjust `--workers 4 --delay 200` in the ExecStart to match the current live rate.
- **API box:** opentrustseal.service is already systemd-managed for the web API. Different unit, different concerns.

## When NOT to use this unit

- For ad-hoc one-shot crawls you are monitoring manually (use `python3 crawl_seed.py ... &` directly).
- For debugging a single bad domain (use the on-demand `--tranco-top 1` flag pattern).
- For the Phase 2 completion pass (that is a different command invocation and should stay manual).
