#!/bin/sh
# Publishes how much disk space the node's data directory uses as /storage.json (refreshed every minute).
# The node's get_info only reports FREE space, so the dashboard container measures the (read-only mounted)
# data directory itself.
DIR="${NODE_DATA_DIR:-/node-data}"
OUT=/usr/share/nginx/html/storage.json
(
  while true; do
    if [ -d "$DIR" ]; then
      kb=$(du -sk "$DIR" 2>/dev/null | cut -f1)
      printf '{"used_bytes":%s,"updated":%s}\n' "$(( ${kb:-0} * 1024 ))" "$(date +%s)" > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
    fi
    sleep 60
  done
) >/dev/null 2>&1 &
