#!/usr/bin/env sh
# Nightly backup, run inside the container so the host needs nothing but
# Docker. scripts/backup.py copies the database into data/backups/ with
# SQLite's online backup API, checks the copy and keeps the last 30; the
# directory is on the bind mount, so the copies are on the host as well.
# Cron line, see the README:
#
#   0 4 * * * /home/<user>/git/analytica/deploy/backup.sh
set -eu

exec docker exec analytica python scripts/backup.py
