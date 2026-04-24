#!/usr/bin/env bash
# Daily re-crawl wrapper. Called by cron. Activates the venv, runs the
# Python re-crawler, writes a dated log, prunes old logs.
#
# Expects to live at /opt/opentrustseal/scripts/crawl_daily.sh on the VPS.
# Cron entry (run as the ott user):
#   0 3 * * * /opt/opentrustseal/scripts/crawl_daily.sh >> /opt/opentrustseal/logs/crawl-cron.log 2>&1
#
# The daily Python script writes the heartbeat JSON. This wrapper's exit
# code mirrors the Python script so the cron line's output signals whether
# the run succeeded.

set -u

APP_DIR="/opt/opentrustseal"
VENV="${APP_DIR}/venv"
LOG_DIR="${APP_DIR}/logs"
DATA_DIR="${APP_DIR}/data"
SCRIPT="${APP_DIR}/scripts/crawl_daily.py"

mkdir -p "${LOG_DIR}"
mkdir -p "${DATA_DIR}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/crawl-daily-${TS}.log"

# Fail loud if the venv is missing. Deploy should always leave it in place,
# but we'd rather surface "venv gone" than silently skip the crawl.
if [ ! -f "${VENV}/bin/activate" ]; then
    echo "[$(date -u +%FT%TZ)] FATAL: venv not found at ${VENV}" | tee -a "${LOG_FILE}"
    exit 2
fi

# shellcheck disable=SC1091
. "${VENV}/bin/activate"

export OTS_DATA_DIR="${DATA_DIR}"
# Critical: app reads OTS_DB_PATH and defaults to ./data/ots.db if missing.
# Without this export the cron silently runs against a blank ots.db, logs
# "registry empty", and skips the real production DB at ott.db. Make the
# target explicit instead of relying on cwd + default.
export OTS_DB_PATH="${DATA_DIR}/ott.db"
export OTS_KEY_DIR="${APP_DIR}/keys"
# Enable the same fetch-tier flags the live service runs with so daily
# re-crawls escalate through probe + Wayback on blocked sites, matching
# what's live in production.
export OTS_ENABLE_WAYBACK_TIER=1
export OTS_ENABLE_PROBE_TIER=1

cd "${APP_DIR}"

echo "[$(date -u +%FT%TZ)] crawl_daily starting (log: ${LOG_FILE})"
python3 "${SCRIPT}" >> "${LOG_FILE}" 2>&1
RC=$?

if [ ${RC} -eq 0 ]; then
    echo "[$(date -u +%FT%TZ)] crawl_daily completed ok"
else
    echo "[$(date -u +%FT%TZ)] crawl_daily exited with rc=${RC} (see ${LOG_FILE})"
fi

# Prune crawl logs older than 14 days to keep disk usage bounded.
find "${LOG_DIR}" -maxdepth 1 -name 'crawl-daily-*.log' -mtime +14 -delete 2>/dev/null || true

exit ${RC}
