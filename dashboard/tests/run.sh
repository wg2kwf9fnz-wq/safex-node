#!/bin/bash
# Phone-size browser test of the dashboard against a mock gateway/node (needs chromium + node + python3).
cd "$(dirname "$0")"
export NO_PROXY='*'
sed -i "s#/data/projects/safex-node/dashboard/index.html#$(cd .. && pwd)/index.html#" mock_dash.py 2>/dev/null
python3 mock_dash.py &
MP=$!
sleep 1
rm -rf /tmp/cdp-profile2; mkdir -p shots
timeout 90 node mobile_driver.js shots
kill $MP
