#!/usr/bin/env bash
# One-time installer for the daily re-crawl cron entry.
#
# Run on the API box as root:
#   bash /opt/opentrusttoken/scripts/install-cron.sh
#
# Installs (or replaces) a single cron line in the ott user's crontab
# that runs the daily crawler at 03:00 UTC. Idempotent: re-running removes
# any existing ott entry with the same tag before reinstalling.

set -euo pipefail

TAG="# ott:crawl_daily"
LINE="0 3 * * * /opt/opentrusttoken/scripts/crawl_daily.sh >> /opt/opentrusttoken/logs/crawl-cron.log 2>&1 ${TAG}"
USER_NAME="ott"

if ! id -u "${USER_NAME}" >/dev/null 2>&1; then
    echo "FATAL: user ${USER_NAME} not found on this box" >&2
    exit 1
fi

chmod +x /opt/opentrusttoken/scripts/crawl_daily.sh
chmod +x /opt/opentrusttoken/scripts/crawl_daily.py

# Load existing crontab (empty if none), strip prior crawl_daily entries
# (tagged OR untagged -- e.g. the original ghost entry at the old path),
# append the fresh line, and reinstall. The `|| true` on crontab -u -l
# keeps the script from failing when the user has no existing crontab.
CURRENT="$(crontab -u "${USER_NAME}" -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "${CURRENT}" | grep -v 'crawl_daily\.sh' || true)"

{
    if [ -n "${FILTERED}" ]; then
        printf '%s\n' "${FILTERED}"
    fi
    printf '%s\n' "${LINE}"
} | crontab -u "${USER_NAME}" -

echo "Installed cron entry for ${USER_NAME}:"
crontab -u "${USER_NAME}" -l | grep "${TAG}"
echo
echo "First run will execute at 03:00 UTC."
echo "To test immediately: sudo -u ${USER_NAME} /opt/opentrusttoken/scripts/crawl_daily.sh"
