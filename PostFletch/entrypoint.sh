#!/bin/sh
set -e

# Fix ownership of the cache volume mount so appuser can read/write it.
# This runs as root before we drop privileges — necessary because Docker
# creates the host-side directory as root when the volume is first mounted.
mkdir -p /app/cache/tmdb_posters /app/cache/tmdb_logos
chown -R appuser:appuser /app/cache

# Drop from root to appuser and exec uvicorn.
# gosu correctly transfers signals (SIGTERM etc.) to the child process,
# unlike 'su -c' which leaves an extra shell in the process tree.
exec gosu appuser uvicorn main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS:-1}"