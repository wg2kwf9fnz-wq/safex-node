#!/bin/sh
# Tiny CGI (served by busybox httpd on 127.0.0.1:8081, reached through nginx at /api/cpu-mode).
# GET  -> {"mode":"half|full","active":"half|full","threads":N,"cores":M}
# POST -> body containing "full" or "half" sets the mode, then returns the same JSON.
# The node container's wrapper watches /control/cpu_mode and restarts safexd when it changes.
MODE_FILE=/control/cpu_mode
ACTIVE_FILE=/control/cpu_active

printf 'Content-Type: application/json\r\nCache-Control: no-store\r\n\r\n'

if [ "$REQUEST_METHOD" = "POST" ]; then
  body=""
  case "$CONTENT_LENGTH" in
    ''|*[!0-9]*) ;;
    *) [ "$CONTENT_LENGTH" -le 64 ] && body=$(head -c "$CONTENT_LENGTH") ;;
  esac
  new=""
  case "$body" in
    *full*) new=full ;;
    *half*) new=half ;;
  esac
  if [ -n "$new" ]; then
    echo "$new" > "$MODE_FILE.tmp" && mv "$MODE_FILE.tmp" "$MODE_FILE"
  fi
fi

mode=half
[ -f "$MODE_FILE" ] && read mode < "$MODE_FILE"
[ "$mode" = full ] || mode=half

am=""; at=0; ac=0
[ -f "$ACTIVE_FILE" ] && read am at ac < "$ACTIVE_FILE"
case "$at" in ''|*[!0-9]*) at=0 ;; esac
case "$ac" in ''|*[!0-9]*) ac=0 ;; esac
[ "$am" = full ] || [ "$am" = half ] || am=""

printf '{"mode":"%s","active":"%s","threads":%s,"cores":%s}\n' "$mode" "$am" "$at" "$ac"
