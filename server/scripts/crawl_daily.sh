#!/usr/bin/env bash
# Daily re-crawl wrapper. Called by cron. Activates the venv, runs the
# Python re-crawler, writes a dated log, prunes old logs.
#
# Expects to live at /opt/opentrusttoken/scripts/crawl_daily.sh on the VPS.
# Cron entry (run as the ott user):
#   0 3 * * * /opt/opentrusttoken/scripts/crawl_daily.sh >> /opt/opentrusttoken/logs/crawl-cron.log 2>&1
#
# The daily Python script writes the heartbeat JSON. This wrapper's exit
# code mirrors the Python script so the cron line's output signals whether
# the run succeeded.

set -u

APP_DIR="/opt/opentrusttoken"
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

export OTT_DATA_DIR="${DATA_DIR}"

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
