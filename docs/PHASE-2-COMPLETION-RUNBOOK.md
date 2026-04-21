# Phase 2 Completion Pass -- Runbook

After the 18-droplet seed crawl finishes, Phase 2 is a targeted re-crawl of domains whose first pass hit an evidence gap (content blocked, WHOIS timeout, too few signals collected). This pass uses longer timeouts, forces escalation through tiers 2-5, and runs only on the gap set -- not the full Tranco top-1M.

## When to run this

Run Phase 2 only when **both** are true:

1. All 18 seeds have completed their segments (checkpoint at 100%, no live workers).
2. Per-droplet DBs have been rsynced to a merge host.

Phase 2 before the seeds finish is premature because the selector's input is incomplete.

## Success metric

Phase 2 succeeds when the incomplete-evidence set shrinks by at least 60%. The residual 40% is the structurally unreachable set (Cloudflare Enterprise on residential + Wayback both failing, domains with no public WHOIS, etc). 60% rescue is the bar; lower than that means the fetch ladder has a new bug, not a tuning opportunity.

## Step-by-step

### Step 1 -- Gather all per-droplet DBs to one merge host

Pick any box with disk (the API box works, or a fresh merge droplet). Each per-droplet DB follows the naming `ots-{pid}.db`. Pull every one into a single directory:

```bash
# On merge host
mkdir -p /tmp/seed-dbs

# For each of ots-seed-1 .. ots-seed-18:
for ip in 138.68.30.188 146.190.142.163 142.93.248.84 138.197.129.121 \
          142.93.34.243 68.183.13.238 137.184.1.1 64.23.228.248 \
          149.28.169.34 66.42.118.203 144.202.17.121 45.76.174.191 \
          216.128.176.173 45.77.88.41 104.238.158.94 207.148.102.73 \
          144.202.93.207 45.77.148.196; do
  scp -o StrictHostKeyChecking=accept-new root@${ip}:/opt/ots-seed/data/ots-*.db \
      /tmp/seed-dbs/seed-${ip//./-}-$(date +%s).db 2>&1 | tail -1
done

ls -lh /tmp/seed-dbs/
```

Expected: 18 files, ~10-50MB each depending on how many domains that seed covered.

### Step 2 -- Generate the completion list with --dry-run first

```bash
cd /path/to/opentrusttoken/server/scripts
python3 generate_completion_list.py /tmp/seed-dbs/ --dry-run
```

Dry-run prints:
- total incomplete count
- gap distribution (content_blocked / whois_failed / identity=0 / only N/6 signals)
- score distribution (how many of the gaps are DENY / CAUTION / PROCEED)

**Sanity check before proceeding:** the count should be 10-20% of the total scanned domains. If it's 50%+, the selector is broken or the first pass had a systemic failure -- investigate before re-crawling.

### Step 3 -- Generate the file for real

```bash
python3 generate_completion_list.py /tmp/seed-dbs/ --out /tmp/completion.txt
wc -l /tmp/completion.txt
```

### Step 4 -- Fan out the completion list across seeds 1-6

The completion pass wants the full fetch ladder (tiers 1-5), not fast mode. Tiers 2-5 are expensive per-domain, so don't run this across all 18 seeds -- 6 is enough, and seeds 7-18 are already saturated on the Tranco 100K-1M work.

Split the completion list into 6 equal files:

```bash
split -n l/6 -d --suffix-length=1 /tmp/completion.txt /tmp/completion-part-
# produces completion-part-0 through completion-part-5
```

Copy each file to the matching seed box:

```bash
for i in 1 2 3 4 5 6; do
  # map seed-i to its IP -- see project_seed_cluster.md
  scp /tmp/completion-part-$((i-1)) root@${SEED_IP}:/opt/ots-seed/completion.txt
done
```

### Step 5 -- Kick off the completion crawl on each seed

On each seed 1-6:

```bash
cd /opt/ots-seed
# Full ladder mode: no --fast, longer timeouts, tier 2-5 enabled
OTS_DATA_DIR=/opt/ots-seed/data \
  python3 crawl_seed.py /opt/ots-seed/completion.txt \
  --workers 3 \
  > /var/log/ots-completion.log 2>&1 &
```

Notes:
- `--workers 3` is lower than the initial pass because Playwright (tier 2) uses more memory and CPU per worker.
- No `--fast` flag -- we want the full escalation ladder.
- No `--resume` flag -- this is a fresh list, not a resumption.
- The per-process DB fix still applies. Each worker writes to its own `ots-{pid}.db`. Merge at the end.

### Step 6 -- Monitor

The completion pass is slower per-domain (30-60s each) because tiers 2-5 run. Expect ~2-3 hours per 1,000 domains. Full 192-domain production test ran in ~12 minutes with tier 2 enabled; a 20K+ incomplete set from the full seed will take ~8-10 hours on 6 seeds.

Monitor via the existing progress file:

```bash
watch -n 10 "for i in 1 2 3 4 5 6; do \
  ssh root@\${SEED_${i}_IP} 'cat /opt/ots-seed/data/.seed-progress.json 2>/dev/null | head -c 200'; \
  echo; \
done"
```

### Step 7 -- Merge, rescore, re-measure

After all 6 completion seeds finish:

```bash
# Pull completion DBs
for i in 1 2 3 4 5 6; do
  scp root@${SEED_IP}:/opt/ots-seed/data/ots-*.db /tmp/completion-dbs/
done

# Merge into the already-merged Phase 1 DB
cd /path/to/opentrusttoken/server
python3 merge_db.py --source /tmp/completion-dbs/ --target /tmp/seed-dbs/merged.db --dry-run
# review the plan, then run without --dry-run

# Rescore everything with v1.4
python3 rescore.py --db /tmp/seed-dbs/merged.db --model v1.4

# Re-run the selector: how much did the gap set shrink?
python3 scripts/generate_completion_list.py /tmp/seed-dbs/merged.db --dry-run
```

Phase 2 success = incomplete count dropped by 60%+.

### Step 8 -- Destroy the completion seeds

Same as Phase 1: once data is merged and rescored, destroy the 6 completion seeds. DO and Vultr both bill by the hour, so every hour a completed droplet stays up is pure waste.

## Failure modes and what to do

| Symptom | Likely cause | Fix |
|---|---|---|
| Phase 2 rescue rate < 30% | Tier 4 residential breaker tripping for everything | Check `/stats.fetch` on the API box. If tier4_breaker_open is true, restart the Mac Air crawler and inspect its logs. |
| Phase 2 adds more incomplete domains than it removes | merge_db.py overwriting with newer-but-worse data (rare, means a completion run returned lower-quality data than Phase 1 had) | Inspect the conflicts: `sqlite3 merged.db "SELECT domain FROM scored_results WHERE ..."`. Keep the higher-score record. |
| Completion pass hangs on one domain for 10+ minutes | Circuit breaker not tripping on a stuck Playwright context | Kill the worker process; the checkpoint will resume past it on restart. |
| Seed box OOMs mid-run | Memory limits on the Chromium pool not applied | Reduce `--workers` to 2, or add a 1GB swap file with `fallocate`. |

## Phase 2 is not

- Not a general re-crawl. It only touches domains flagged by the selector.
- Not a scoring change. v1.4 rescore is a separate step that runs against the merged DB.
- Not infinite. If a domain still has `content_blocked` after Phase 2, it stays `content_blocked` in the dataset -- the residual unreachable set is a known limitation, not a bug to keep chasing.
