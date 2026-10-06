#!/bin/sh
# Starts the stratum gateway (one xmrig-proxy per miner address, started on demand; stats in $DATA_DIR/gateway-state.json).
set -eu
DATA_DIR="${DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

# Clean-up: releases before 7.0.3-umbrel8 kept ONE payout wallet in config.json. Every miner is now paid to the
# address in its own username and that file is no longer read, so remove it (it only contains the old wallet address).
if [ -f "$DATA_DIR/config.json" ]; then
  echo "[entrypoint] removing obsolete $DATA_DIR/config.json (old single payout wallet, no longer used)"
  rm -f "$DATA_DIR/config.json"
fi

export STATE_FILE="${STATE_FILE:-$DATA_DIR/gateway-state.json}"
exec python3 -u /usr/local/bin/gateway.py
