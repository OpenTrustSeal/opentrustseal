# Tier 6 Activation Runbook

The code ships dark by default. Follow these steps to turn on commercial scraper fallback.

## Prereqs

- Bright Data account at https://brightdata.com/
- A "Web Unlocker" zone provisioned in your Bright Data dashboard
- The API box deployed with the tier 6 code (everything in this runbook requires the post-Codex-fix-bundle deploy)

## Step 1 -- sign up with Bright Data

1. Sign up at https://brightdata.com/ (corporate account, OpenTrustSeal, Inc.).
2. Set a **hard monthly spend cap** in the dashboard before enabling anything. Recommended $200/mo for the 100K seed, $500/mo for the full 1M. Bright Data's billing dashboard supports a hard ceiling; use it.
3. Create a zone of type **Web Unlocker**. Name it something descriptive like `ots_web_unlocker`.
4. In the zone settings, note:
   - Customer ID (top of the zone page, format `hl_xxxxxxxx`)
   - Zone name (what you just named it)
   - Zone password (the "API key" for this zone)

## Step 2 -- install the env file on the API box

From your dev Mac:

```bash
scp opentrusttoken/deploy/scraper.env.template root@206.189.65.177:/tmp/scraper.env
ssh root@206.189.65.177 "sudoedit /etc/opentrustseal/scraper.env"
# Paste the template, fill in the three credential fields, leave SCRAPER_ENABLED=false.
ssh root@206.189.65.177 "chown root:ott /etc/opentrustseal/scraper.env && chmod 640 /etc/opentrustseal/scraper.env"
```

Verify permissions:
```bash
ssh root@206.189.65.177 "ls -la /etc/opentrustseal/scraper.env"
# Expected: -rw-r----- 1 root ott ...
```

## Step 3 -- restart the API + confirm scraper sees the config (still dark)

```bash
ssh root@206.189.65.177 "systemctl restart opentrustseal"
sleep 3
curl -s https://api.opentrustseal.com/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print('scraper_enabled:', d['fetch']['scraper_enabled']); print('scraper_provider:', d['fetch']['scraper_provider'])"
```

Expected: `scraper_enabled: False`, `scraper_provider: None`. That's correct -- `SCRAPER_ENABLED=false` means the flag is off even though credentials are present. We're verifying the config is parseable before flipping the switch.

## Step 4 -- (recommended) shadow mode for 24 hours

Before billing hits, run a day of shadow mode. Edit the API box code to hardcode a fake tier 6 that returns `None` (no network call) but still increments strikes. Shadow confirms the gate + escalation plumbing is correct with zero spend.

Shortcut: skip to Step 5. The 3-strike gate already prevents runaway cost; shadow mode is belt-and-suspenders for paranoia.

## Step 5 -- flip to live

```bash
ssh root@206.189.65.177 'sed -i "s/SCRAPER_ENABLED=false/SCRAPER_ENABLED=true/" /etc/opentrustseal/scraper.env && systemctl restart opentrustseal'
sleep 3
curl -s https://api.opentrustseal.com/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print('scraper_enabled:', d['fetch']['scraper_enabled'])"
# Expected: scraper_enabled: True
```

## Step 6 -- preload known-stubborn domains (optional)

If you want tier 6 to fire on day one for domains you already know are stuck (instead of waiting 3 daily cycles for them to accumulate strikes), preload them:

```bash
ssh root@206.189.65.177 "cd /opt/opentrusttoken && sudo -u ott /opt/opentrusttoken/venv/bin/python3 -c 'from app import tier6_gate; n = tier6_gate.preload_bootstrap_strikes([\"kohls.com\"], strikes=3); print(f\"preloaded {n}\")'"
```

Expand the list with any other domains you want to force through tier 6 on the next fetch. Typical candidates are the ones still showing `crawlability: blocked` after tiers 1-5 stabilize.

## Step 7 -- monitor

### Real-time stats

```bash
curl -s https://api.opentrustseal.com/stats | python3 -c "
import json, sys
d = json.load(sys.stdin)
f = d['fetch']
print('Tier 6 status')
print(f'  enabled:         {f[\"scraper_enabled\"]}')
print(f'  provider:        {f[\"scraper_provider\"]}')
print(f'  breaker_open:    {f[\"scraper_breaker_open\"]}')
print(f'  ok:              {f[\"scraper_ok\"]}')
print(f'  error:           {f[\"scraper_error\"]}')
print(f'  skipped_gate:    {f[\"scraper_skipped_gate\"]}')
print(f'  skipped_breaker: {f[\"scraper_skipped_breaker\"]}')
print(f'  gate_strikes:    {f[\"scraper_gate_strikes\"]}')
"
```

`scraper_skipped_gate` high relative to `scraper_ok` is healthy -- it means the gate is doing its job of keeping cost down. If you see `scraper_ok` climb fast and `scraper_skipped_gate` stay low, the gate is opening too easily and you should raise `SCRAPER_GATE_STRIKES`.

### Per-domain audit trail

```bash
ssh root@206.189.65.177 "sqlite3 /opt/opentrusttoken/data/ott.db 'SELECT domain, strike_count, tier6_call_count, last_tier6_status, last_tier6_called_at FROM tier6_gate ORDER BY tier6_call_count DESC LIMIT 20;'"
```

Shows which domains have cost you tier 6 calls, how many times each, and whether they returned success (`200`) or error (`403`/other).

### Budget check

Bright Data's dashboard is the source of truth for spend. Check weekly. If you see spend trending above the monthly cap, either raise `SCRAPER_GATE_STRIKES` in scraper.env (restart required) or turn off with `SCRAPER_ENABLED=false`.

## Rollback

If something breaks or spend spirals:

```bash
ssh root@206.189.65.177 'sed -i "s/SCRAPER_ENABLED=true/SCRAPER_ENABLED=false/" /etc/opentrustseal/scraper.env && systemctl restart opentrustseal'
```

Tier 6 turns off. Tiers 1-5 continue as normal. The strike counter continues accumulating but fires nothing.

## Future provider swaps

To swap to ZenRows or ScraperAPI later:

1. Add a `_fetch_via_<provider>` function in `fetch_escalation.py` matching the `_fetch_via_brightdata` shape.
2. Add a dispatch branch in `fetch_via_commercial_scraper`.
3. Update `SCRAPER_PROVIDER` in scraper.env and restart.

The gate logic, strike table, and escalation plumbing are provider-agnostic.
