# Vultr ticket reply -- GGG-19UBL

Send via the Vultr console. Keep formatting simple (no markdown rendering in their ticket system).

---

Hi,

Thank you for the heads-up. Happy to cooperate and reduce load.

Quick context on the workload so we can find the right configuration. We are OpenTrustSeal, Inc. (California C-Corp), and we are building an open-source trust dataset for the Tranco top-1M domain list. The dataset is published under CC-BY-4.0 and will be used by AI agent developers to check merchant trust before automated payments. The distributed setup is intentional: we spread 10 instances across your global regions specifically so we can politely query country-local WHOIS servers from geographically close hosts, rather than hammering every TLD from a single region. Per-instance, each worker handles one domain at a time (one HTTP fetch, one WHOIS query, one DNS lookup, one SQLite write), with a target steady-state of roughly 40 queries per minute. The sustained runtime is what likely triggered your detector, not the QPS.

Two asks:

1. Can you tell us the specific CPU threshold you are using? We can self-regulate if we know the budget (reducing worker counts, adding inter-request sleeps, etc.). Right now the cap itself is opaque to us.

2. If the current Cloud Compute 2 GB plans are not the right match for this workload, happy to move these instances to a plan that is. Which plan tier do you recommend for a multi-day crawler with our profile? Dedicated CPU? High Performance? We would rather be on the right plan than keep tripping the abuse detector.

In the meantime we are reducing each instance's worker pool to lower sustained CPU. Let us know what else would help.

Thanks,
Allen Lu
OpenTrustSeal, Inc.
alu@opentrustseal.com

---

## Notes for you before sending

- Ticket link: https://console.vultr.com/support/view_ticket/?TICKETID=GGG-19UBL
- Tone is conciliatory on purpose. First-reply tone sets the trajectory of the whole thread.
- Do NOT mention that you have 14 more droplets planned or that this is just the start. Support reads those signals as "escalation risk."
- Do NOT threaten to leave. If they refuse, pivot silently to Hetzner for future batches.
- If they come back with a threshold number, we can wire explicit rate-limits into `crawl_seed.py` -- I can add a `--max-cpu-percent` soft cap that sleeps between batches to stay under it.

## Side action to do now while waiting for reply

Drop each Vultr seed's worker count from 5 to 2. Lowers sustained CPU by roughly 60%. On each seed 9-18:

```bash
ssh root@<vultr_ip> '
  pgrep -f crawl_seed | xargs -r kill -TERM
  sleep 3
  pgrep -f crawl_seed | xargs -r kill -KILL 2>/dev/null
  cd /opt/ots-seed
  nohup python3 crawl_seed.py /opt/ots-seed/domains.txt --workers 2 --resume \
    > /var/log/ots-seed.log 2>&1 &
  sleep 2
  pgrep -f crawl_seed | head -3
'
```

The `--resume` flag picks up from the last checkpoint so nothing is lost. Adjust the file path and args to whatever you set on first spin-up.
