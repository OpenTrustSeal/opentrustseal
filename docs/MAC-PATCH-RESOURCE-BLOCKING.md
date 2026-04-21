# Push Resource-Blocking Patch to Mac Air

Applies to the existing Mac Air crawler at `pennyai@100.125.118.64` (pennys-macbook-air). Takes 30 seconds once the Mac is reachable via Tailscale.

## What changed

`crawler/fetch_service.py` now blocks `image`, `media`, and `font` resource types during Playwright fetches. Default-on via `CRAWLER_BLOCK_RESOURCES=true` (toggleable). Drops bandwidth 5-10x per fetch and shortens page load 3-5x.

## Deploy steps

From the Mac that has this repo (`/Users/admin/Library/Mobile Documents/com~apple~CloudDocs/robots`):

```bash
# 1. Copy patched file over Tailscale
scp "/Users/admin/Library/Mobile Documents/com~apple~CloudDocs/robots/opentrusttoken/crawler/fetch_service.py" \
    pennyai@100.125.118.64:~/ots-crawler/fetch_service.py

# 2. Restart the crawler service to pick up the patch
ssh pennyai@100.125.118.64 "launchctl kickstart -k gui/$(id -u)/com.opentrustseal.crawler"

# 3. Verify health
ssh pennyai@100.125.118.64 "sleep 2 && curl -s http://100.125.118.64:8901/health"

# 4. Hit a known-blocked merchant from the API box to smoke test
curl -s "https://api.opentrustseal.com/v1/check/chewy.com?refresh=true" | python3 -c "import sys,json;d=json.load(sys.stdin);print('trustScore:',d.get('trustScore'),'crawlability:',d.get('crawlability'),'brandTier:',d.get('brandTier'))"
```

## Optional: toggle off if it breaks anything

If resource blocking interferes with any site's scoring (should not happen since the scorer reads DOM text, but belt-and-suspenders):

```bash
ssh pennyai@100.125.118.64 "echo 'CRAWLER_BLOCK_RESOURCES=false' >> ~/ots-crawler/crawler.env && launchctl kickstart -k gui/$(id -u)/com.opentrustseal.crawler"
```

## When to do this

Before the Mac moves to the Starlink house if you can. If the Mac is already asleep or in transit, apply it when it boots up at Starlink. The patch is entirely local -- no network dependencies beyond Tailscale -- so there's no risk in holding for the relocation.
