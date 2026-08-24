#!/bin/bash
# Stack cb-refresh: supercronic drives the scheduled jobs (crontab).
set -euo pipefail
exec supercronic -passthrough-logs /app/deploy/crontab
