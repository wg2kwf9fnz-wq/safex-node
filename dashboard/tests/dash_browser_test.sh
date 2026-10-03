#!/bin/bash
export PATH=/home/linuxbrew/.linuxbrew/bin:$PATH
export NO_PROXY='*' no_proxy='*'; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
set -u
T=/tmp/dash-test
# re-render nginx site from the (changed) template, keep everything else, reload
python3 - <<'EOF'
t=open('/data/projects/safex-node/dashboard/default.conf.template').read()
t=t.replace('${STRATUM_API_TOKEN}','testtoken123').replace('${NGINX_LOCAL_RESOLVERS}','127.0.0.11')
t=t.replace('listen 80;','listen 8099;').replace('/usr/share/nginx/html','/tmp/dash-test/html')
t=t.replace('http://node:17402','http://rpc.safex.org:17402').replace('http://stratum:8080','http://127.0.0.1:8095')
open('/tmp/dash-test/site.conf','w').write(t)
EOF
cp /data/projects/safex-node/dashboard/index.html $T/html/index.html
nginx -t -c $T/nginx.conf 2>&1 | tail -1
nginx -s reload -c $T/nginx.conf; sleep 1
echo "unlisted api paths:"; curl -s -o /dev/null -w ' /stratum-api/1/other -> %{http_code}\n' localhost:8099/stratum-api/1/other; curl -s -o /dev/null -w ' /api/other -> %{http_code}\n' localhost:8099/api/other
# reset the proxy to a fresh-install state (placeholder wallet)
jq '.pools[0].user="SET_YOUR_SAFEX_WALLET_ADDRESS"' $T/config.json > $T/c2 && cat $T/c2 > $T/config.json; sleep 3
rm -rf /tmp/cdp-profile; mkdir -p $T/shots
W=$(python3 /data/projects/safex-stratum/tools/sfxaddr.py)
echo "test wallet: $W"
timeout 120 node /data/projects/safex-stratum/tools/cdp_driver.js http://localhost:8099/ "$W" $T/shots
echo "--- config file on disk now:"; jq -c '.pools[0] | {url,user,coin,daemon}' $T/config.json
echo "--- proxy log tail:"; tail -6 $T/proxy.log
echo "--- summary:"; curl -s localhost:8099/stratum-api/1/summary | jq -c '{upstreams,miners}'
