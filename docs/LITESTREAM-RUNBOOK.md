# Litestream DB Replication Runbook

Continuous replication of the production SQLite DB to Backblaze B2. RPO ~1 second. Independent failure domain from DigitalOcean (different provider, different region).

## Where things live

| Item | Path / identifier |
|---|---|
| Source DB | `/opt/opentrusttoken/data/ott.db` (API box, `ott-api-1`) |
| Litestream config | `/etc/litestream.yml` |
| B2 credentials env | `/etc/default/litestream` (mode 600, root:root) |
| Service unit | `litestream.service` (from the deb package) |
| Unit override | `/etc/systemd/system/litestream.service.d/env.conf` |
| B2 bucket | `ots-db-backup` |
| B2 path prefix | `production/ott.db/` |
| B2 region / endpoint | `us-west-004` / `s3.us-west-004.backblazeb2.com` |
| B2 key name | `ots-db-litestream` (scoped to the one bucket only) |

## Operational commands

**Service status:**

```bash
systemctl status litestream
journalctl -u litestream -f --lines=50
```

**Inspect replication state:**

```bash
litestream generations -replica s3 /opt/opentrusttoken/data/ott.db
litestream snapshots   -replica s3 /opt/opentrusttoken/data/ott.db
```

**Restore to a temp path** (safe, non-destructive):

```bash
litestream restore -o /tmp/ott-test.db \
  -replica s3 /opt/opentrusttoken/data/ott.db

# Then sanity-check row counts:
python3 -c "
import sqlite3
c = sqlite3.connect('/tmp/ott-test.db')
for t in ['scored_results','raw_signals','tier6_gate','feedback']:
    print(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
"
```

**Restore to a specific point in time** (within 72h window):

```bash
litestream restore -o /tmp/ott-old.db \
  -replica s3 \
  -timestamp 2026-04-21T12:00:00Z \
  /opt/opentrusttoken/data/ott.db
```

## Disaster recovery -- rebuild from scratch

Scenario: the API box is gone. New empty box ready.

1. Install litestream on the new box (same deb as `LITESTREAM-INSTALL.md` step 2).
2. Copy `/etc/litestream.yml` + `/etc/default/litestream` from a known-good source (password manager, repo, etc).
3. Restore the DB directly from B2 (no source DB needed):

```bash
source /etc/default/litestream
mkdir -p /opt/opentrusttoken/data
litestream restore -o /opt/opentrusttoken/data/ott.db \
  s3://ots-db-backup/production/ott.db
chown -R ott:ott /opt/opentrusttoken/data
```

4. Start opentrusttoken + litestream. The new replica generation will continue from the restored state.

## Gotchas

- **WAL mode is required.** The prod DB is already in WAL mode; any future code that touches it should set `PRAGMA journal_mode=WAL` at open time to prevent accidental mode drift. `database.py` should enforce this.
- **Don't vacuum while Litestream is running.** `VACUUM` rewrites the whole DB and invalidates Litestream's WAL position tracking. It won't break anything, but it will trigger a full re-sync of the ~13 MB DB. Schedule vacuums in a maintenance window with litestream stopped.
- **B2 key is scoped to this one bucket only.** If you create other OTS-related buckets (dataset backup, log archive), make new keys for them instead of broadening this one. Blast radius stays small on leak.
- **/etc/default/litestream contains the secret.** Mode 600, never commit, never copy outside the VPS without rewriting creds.

## Cost

- B2 storage at us-west-004: $0.005/GB/month.
- Current DB: 13 MB → ~$0.00007/mo (rounds to nothing).
- Projected post-1M-merge: ~5 GB → ~$0.025/mo.
- Egress only on restore: $0.01/GB (also effectively free).

Budget for this line-item: $1/mo indefinitely.

## Layering with DO backups

Keep the DO weekly backups feature enabled alongside Litestream:

- **Litestream**: continuous, file-level, point-in-time restore of the DB
- **DO backups**: weekly, full-droplet snapshot for fast re-provisioning of the whole box (nginx config, systemd units, env files, certbot state, etc)

Disaster-recovery drill: spin up a fresh droplet from DO's weekly snapshot (RPO 7 days for the box config, seconds for the DB after litestream restore). Total recovery < 30 minutes.
