# Windows Crawler Deploy -- Gaming PC (RTX 4090, Win11 Pro)

Adds the home gaming PC as a second residential crawler endpoint. Identical role to the Mac Air: runs `fetch_service.py`, listens on Tailscale, gets called as Tier 4 by the API box when a site needs real-browser egress from a residential IP.

## What this gives you

- Second residential endpoint on the same Spectrum home IP as the Mac Air (when the Mac is home). After the Mac moves to the Starlink house, this becomes the Spectrum-residency endpoint.
- Different browser fingerprint than macOS: stock Chrome on Win11 vs Chrome for Testing on macOS. Useful for sites that specifically fingerprint macOS.
- 2x parallelism on Tier 4 (round-robin between Mac and Gaming PC endpoints).
- The 4090 is wasted on Playwright (headless Chromium doesn't use GPU). Fine. The PC's 16+ GB RAM and fast NVMe are what matter.

## Prerequisites

- Windows 11 Pro (you have this)
- Admin access to install Python + Tailscale + NSSM
- ~30 minutes of clock time, most of it waiting on `playwright install`

## Step 1 -- Install Python 3.12

Open PowerShell as Administrator. If Python 3.12 is not installed:

```powershell
winget install --id Python.Python.3.12 --scope machine -e
# Close and reopen PowerShell after install so PATH refreshes
python --version    # should print 3.12.x
```

If `winget` is unavailable, download the installer from python.org. Check "Add Python to PATH" during install.

## Step 2 -- Install Tailscale and join the tailnet

```powershell
winget install --id Tailscale.Tailscale -e
```

Launch Tailscale from the Start menu. Log in with the same account you used for `pennys-macbook-air`. Confirm the PC shows up in the tailnet and note its Tailscale IPv4 (format `100.x.y.z`). You will use this in Step 8.

Recommended: in the Tailscale admin console, give the PC a stable hostname (e.g. `ots-gaming-pc`) so future config doesn't tie to the IP if it changes.

## Step 3 -- Create the crawler directory and Python venv

```powershell
mkdir C:\ots-crawler
cd C:\ots-crawler
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Keep this PowerShell session open for the rest of the setup.

## Step 4 -- Install Python dependencies

```powershell
pip install fastapi uvicorn[standard] pydantic playwright playwright-stealth httpx python-dotenv
playwright install chromium
```

`playwright install chromium` downloads Playwright's bundled Chromium (around 150 MB). This is the browser the service will use. On Windows there is no CDP regression so the bundled build works fine -- no need for Chrome for Testing like we do on macOS.

## Step 5 -- Copy fetch_service.py onto the PC

Easiest path: SCP from your Mac to the PC over Tailscale, or use a file share.

From your Mac:
```bash
# Replace <GAMING_PC_TS_IP> with the Tailscale IP from Step 2
scp "/Users/admin/Library/Mobile Documents/com~apple~CloudDocs/robots/opentrusttoken/crawler/fetch_service.py" \
    Administrator@<GAMING_PC_TS_IP>:C:\ots-crawler\fetch_service.py
```

If SCP on Windows is not configured, use OneDrive/iCloud sync, a USB stick, or paste the file contents into Notepad on the PC. The only file you need is `fetch_service.py` (the patched version with resource blocking).

Sanity check on the PC:
```powershell
cd C:\ots-crawler
python -c "import ast; ast.parse(open('fetch_service.py').read()); print('OK')"
```

## Step 6 -- Configure the environment file

Create `C:\ots-crawler\crawler.env` with this content (use the same CRAWLER_SHARED_SECRET as the Mac Air so the API box's single secret works for both endpoints):

```
CRAWLER_SHARED_SECRET=<paste the shared secret from the Mac Air's crawler.env>
CRAWLER_POOL_SIZE=3
CRAWLER_DEFAULT_TIMEOUT_MS=25000
CRAWLER_BLOCK_RESOURCES=true
```

Notes:
- `CRAWLER_POOL_SIZE=3` gives a bit more parallelism than the Mac (2) since the PC has more RAM.
- `CRAWLER_BLOCK_RESOURCES=true` enables the bandwidth-saving resource blocker (default-on in the patched fetch_service). 5-10x bandwidth reduction, 3-5x faster per-fetch.
- No `CRAWLER_BROWSER_CHANNEL` or `CRAWLER_BROWSER_EXECUTABLE` needed. Playwright's bundled Chromium is the right choice on Windows.

File permissions: right-click `crawler.env`, Properties, Security, restrict read access to your user and Administrators only. The shared secret lives in there.

## Step 7 -- Test run manually

Create `C:\ots-crawler\start.ps1`:

```powershell
# Load environment from crawler.env, then launch the service
Get-Content C:\ots-crawler\crawler.env | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
    }
}
Set-Location C:\ots-crawler
.\venv\Scripts\python.exe -m uvicorn fetch_service:app --host 0.0.0.0 --port 8901
```

Test it:
```powershell
cd C:\ots-crawler
.\start.ps1
```

In another PowerShell on the PC:
```powershell
curl.exe http://localhost:8901/health
# should return {"status":"ok","pool_free":3,"pool_size":3}
```

From the Mac or API box (over Tailscale):
```bash
curl http://<GAMING_PC_TS_IP>:8901/health
```

If that works, you are good. Stop the service with Ctrl-C.

## Step 8 -- Install as a Windows service via NSSM

NSSM wraps the service so it starts at boot and auto-restarts on crash.

```powershell
winget install --id NSSM.NSSM -e
# Close and reopen PowerShell as Administrator after install
```

Install the service:
```powershell
nssm install ots-crawler "C:\ots-crawler\venv\Scripts\python.exe" "-m" "uvicorn" "fetch_service:app" "--host" "0.0.0.0" "--port" "8901"
nssm set ots-crawler AppDirectory "C:\ots-crawler"
nssm set ots-crawler AppStdout "C:\ots-crawler\stdout.log"
nssm set ots-crawler AppStderr "C:\ots-crawler\stderr.log"
nssm set ots-crawler AppRotateFiles 1
nssm set ots-crawler AppRotateBytes 10485760
nssm set ots-crawler Start SERVICE_AUTO_START
nssm set ots-crawler AppRestartDelay 5000

# Load env vars into the service's environment
$env_vars = Get-Content C:\ots-crawler\crawler.env | Where-Object { $_ -match '^\s*[A-Z_]+\s*=' } | ForEach-Object { $_.Trim() }
nssm set ots-crawler AppEnvironmentExtra $env_vars

nssm start ots-crawler
nssm status ots-crawler   # should print SERVICE_RUNNING
```

Verify from the Mac or API box:
```bash
curl http://<GAMING_PC_TS_IP>:8901/health
```

To check logs:
```powershell
Get-Content C:\ots-crawler\stdout.log -Tail 50
Get-Content C:\ots-crawler\stderr.log -Tail 50
```

## Step 9 -- Firewall: allow Tailscale inbound only

Tailscale binds its own interface and handles the tunnel encryption, so you do NOT need to expose 8901 on your public internet-facing interface (and should not).

In PowerShell as Administrator:
```powershell
# Allow inbound on port 8901 only on the Tailscale virtual network adapter
New-NetFirewallRule -DisplayName "OTS Crawler (Tailscale only)" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8901 `
    -InterfaceAlias "Tailscale"

# Deny 8901 on all other interfaces (defense in depth)
New-NetFirewallRule -DisplayName "OTS Crawler (block public)" `
    -Direction Inbound -Action Block -Protocol TCP -LocalPort 8901 `
    -InterfaceAlias "Ethernet","Wi-Fi"
```

Adjust `Ethernet`/`Wi-Fi` names to match your adapters. Run `Get-NetAdapter` to list them.

## Step 10 -- Register the endpoint on the API box

On the API box (`root@206.189.65.177`), edit `/etc/opentrustseal/macbook.env`:

```bash
ssh root@206.189.65.177 "cat /etc/opentrustseal/macbook.env"
```

Currently this is something like:
```
MACBOOK_URL=http://100.125.118.64:8901
```

Switch to the multi-residential fleet variable `RESIDENTIAL_URLS` that `fetch_escalation.py` reads (comma-separated list, round-robin with per-endpoint circuit breakers):

```
RESIDENTIAL_URLS=http://100.125.118.64:8901,http://<GAMING_PC_TS_IP>:8901
```

Replace `<GAMING_PC_TS_IP>` with the actual Tailscale IP from Step 2. Keep the same CRAWLER_SHARED_SECRET; both endpoints share it.

Restart the API service:
```bash
ssh root@206.189.65.177 "systemctl restart opentrustseal"
```

Sanity check:
```bash
curl https://api.opentrustseal.com/stats | python3 -m json.tool | grep -E "tier4|macbook|residential"
```

You should see `tier4_ok` incrementing when live checks route to residential. Hit a known-blocked merchant to force Tier 4:
```bash
curl https://api.opentrustseal.com/v1/check/chewy.com?refresh=true | python3 -m json.tool | head -30
```

## Step 11 -- Ongoing operations

- **Start/stop/restart:** `nssm start|stop|restart ots-crawler`
- **Reload code after a patch:** copy new `fetch_service.py` over, then `nssm restart ots-crawler`
- **Watch live logs:** `Get-Content C:\ots-crawler\stdout.log -Wait`
- **Bandwidth stats (daily):** Windows Task Manager > Performance > Ethernet/Wi-Fi shows live throughput. Or `Get-NetAdapterStatistics`.
- **Sleep settings:** open Power Options and set the PC to "Never sleep" when plugged in. Windows defaults to 30-minute sleep which will take the crawler offline.
- **Tailscale:** make sure Tailscale is set to "start at login" and is logged in with the persistent account (check `tailscale status` via the tray icon).

## Step 12 -- When the Mac moves to Starlink

After the Mac Air reboots at the Starlink house and rejoins the tailnet, its Tailscale IP stays the same (100.125.118.64) because Tailscale is network-agnostic. `RESIDENTIAL_URLS` does not need to change. The Mac's public-internet egress IP switches from Spectrum to Starlink automatically, giving you two-carrier diversity:

- `100.125.118.64` (pennys-macbook-air) → Starlink
- `<GAMING_PC_TS_IP>` (ots-gaming-pc) → Spectrum

From the API box's perspective both endpoints behave identically; the round-robin handler rotates between them. If Cloudflare blanket-blocks one carrier's IP block, the other carries the load.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl http://<TS_IP>:8901/health` hangs from API box | Firewall blocking Tailscale inbound | Check Step 9 rules. `Test-NetConnection <TS_IP> -Port 8901` from another tailnet peer. |
| Service status shows SERVICE_PAUSED | Unhandled exception in fetch_service | Check `stderr.log`. Usually missing env var or import error. |
| Every fetch returns 403 | Bot protection detecting headless Playwright | Confirm `CRAWLER_BLOCK_RESOURCES=true`. Add `playwright-stealth` if missing (should already be installed). |
| Service restarts every few minutes | `CRAWLER_SHARED_SECRET` mismatch or missing env | Check `AppEnvironmentExtra` is populated: `nssm get ots-crawler AppEnvironmentExtra` |
| Bandwidth way higher than expected | Resource blocking not active | Test with `curl -X POST http://<TS_IP>:8901/fetch -H 'X-Crawler-Secret: <secret>' -H 'Content-Type: application/json' -d '{"url":"https://amazon.com"}'` and watch response size. Should be around 200-800 KB. |
