#!/bin/sh
# Generates / maintains /data/config.json for xmrig-proxy.
# User settings (payout wallet, custom-diff) persist across restarts and can be changed
# from the Umbrel dashboard through the proxy's HTTP API.
set -eu

CONFIG=/data/config.json
DAEMON_HOST="${DAEMON_HOST:-node}"
DAEMON_PORT="${DAEMON_PORT:-17402}"
STRATUM_PORT="${STRATUM_PORT:-17404}"
API_PORT="${API_PORT:-8080}"
API_TOKEN="${API_TOKEN:-}"
DEFAULT_DIFF="${DEFAULT_DIFF:-50000}"
OLD_DEFAULT_DIFF=20000   # earlier releases shipped this default; migrate it, leave any other value alone
PLACEHOLDER="SET_YOUR_SAFEX_WALLET_ADDRESS"

mkdir -p /data

if [ ! -s "$CONFIG" ]; then
  echo "[entrypoint] creating $CONFIG"
  cat > "$CONFIG" <<EOF
{
  "mode": "extra_nonce",
  "donate-level": 0,
  "custom-diff": ${DEFAULT_DIFF},
  "workers": true,
  "verbose": false,
  "colors": false,
  "watch": true,
  "pools": [
    { "url": "${DAEMON_HOST}:${DAEMON_PORT}", "user": "${WALLET_ADDRESS:-$PLACEHOLDER}", "coin": "SFX", "daemon": true, "daemon-poll-interval": 1000 }
  ]
}
EOF
fi

# Always enforce the settings owned by the app packaging (bind, daemon, API, mode).
tmp=$(mktemp)
jq --arg dh "${DAEMON_HOST}:${DAEMON_PORT}" \
   --argjson sp "$STRATUM_PORT" --argjson ap "$API_PORT" --arg tok "$API_TOKEN" \
   --argjson dd "$DEFAULT_DIFF" --argjson od "$OLD_DEFAULT_DIFF" '
  .bind = [ { "host": "0.0.0.0", "port": $sp, "tls": false } ]
  | .http = { "enabled": ($tok != ""), "host": "0.0.0.0", "port": $ap, "access-token": (if $tok == "" then null else $tok end), "restricted": false }
  | .pools[0].url = $dh
  | .pools[0].daemon = true
  | .pools[0].coin = "SFX"
  | .["donate-level"] = 0
  | .["custom-diff-stats"] = true
  | if .["custom-diff"] == $od then .["custom-diff"] = $dd else . end
  | .mode = "extra_nonce"
' "$CONFIG" > "$tmp" && cat "$tmp" > "$CONFIG" && rm -f "$tmp"

if [ "$(jq -r '.pools[0].user' "$CONFIG")" = "$PLACEHOLDER" ]; then
  echo "[entrypoint] No payout wallet set yet - open the Safex app in Umbrel and enter your Safex address."
fi

exec xmrig-proxy --config="$CONFIG" --no-color
