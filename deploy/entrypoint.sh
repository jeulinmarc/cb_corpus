#!/bin/bash
# Stack cb-refresh : supercronic pilote les jobs planifiés (crontab).
set -euo pipefail
exec supercronic -passthrough-logs /app/deploy/crontab
