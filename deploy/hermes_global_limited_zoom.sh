#!/bin/sh
set -eu
exec /var/www/albery/.venv/bin/python \
  /var/www/albery/scripts/run_legacy_system_job.py zoom-to-tasks
