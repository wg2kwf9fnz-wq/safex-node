#!/bin/sh
# The gateway starts one xmrig-proxy per miner address on demand and keeps stats in /data/gateway-state.json.
# (Older versions kept a single payout wallet in /data/config.json; it is no longer used - every miner
#  is paid to the Safex address in its own username.)
set -eu
mkdir -p /data
exec python3 -u /usr/local/bin/gateway.py
