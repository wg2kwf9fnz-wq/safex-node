#!/bin/bash
export PATH=/home/linuxbrew/.linuxbrew/bin:$PATH
[ -f /tmp/dash-test/nginx.pid ] && kill $(cat /tmp/dash-test/nginx.pid) 2>/dev/null
[ -f /tmp/dash-test/proxy.pid ] && kill $(cat /tmp/dash-test/proxy.pid) 2>/dev/null
sleep 1
rm -rf /tmp/dash-test && mkdir -p /tmp/dash-test/html /tmp/dash-test/logs && cd /tmp/dash-test
sed -e 's#CONFIG=/data/config.json#CONFIG=/tmp/dash-test/config.json#' -e 's#mkdir -p /data#mkdir -p /tmp/dash-test#' -e 's#^exec xmrig-proxy.*#true#' /data/projects/safex-node/stratum/entrypoint.sh > ep.sh
DAEMON_HOST=rpc.safex.org DAEMON_PORT=17402 STRATUM_PORT=17404 API_PORT=8095 API_TOKEN=testtoken123 sh ep.sh
jq '.bind[0].host="127.0.0.1" | .http.host="127.0.0.1"' config.json > c2 && mv c2 config.json
nohup /data/projects/xmrig-proxy/build/xmrig-proxy --config=/tmp/dash-test/config.json --no-color > proxy.log 2>&1 &
echo $! > /tmp/dash-test/proxy.pid
cp /data/projects/safex-node/dashboard/index.html html/index.html
python3 - <<'EOF'
t=open('/data/projects/safex-node/dashboard/default.conf.template').read()
t=t.replace('${STRATUM_API_TOKEN}','testtoken123').replace('${NGINX_LOCAL_RESOLVERS}','127.0.0.1')
t=t.replace('listen 80;','listen 8099;').replace('/usr/share/nginx/html','/tmp/dash-test/html')
t=t.replace('http://node:17402','http://rpc.safex.org:17402').replace('http://stratum:8080','http://127.0.0.1:8095')
open('/tmp/dash-test/site.conf','w').write(t)
EOF
cat > nginx.conf <<EOF
worker_processes 1; pid /tmp/dash-test/nginx.pid; error_log /tmp/dash-test/logs/error.log info; daemon on;
events { worker_connections 64; }
http { access_log /tmp/dash-test/logs/access.log; include /home/linuxbrew/.linuxbrew/etc/nginx/mime.types; default_type application/octet-stream;
  client_body_temp_path /tmp/dash-test/b; proxy_temp_path /tmp/dash-test/p; fastcgi_temp_path /tmp/dash-test/f; uwsgi_temp_path /tmp/dash-test/u; scgi_temp_path /tmp/dash-test/s;
  include /tmp/dash-test/site.conf; }
EOF
nginx -t -c /tmp/dash-test/nginx.conf 2>&1 | tail -2
nginx -c /tmp/dash-test/nginx.conf
sleep 3
echo "--- node info via nginx:"; curl -s localhost:8099/api/get_info | jq -c '{status,height,difficulty,mainnet}'
echo "--- stratum summary via nginx:"; curl -s localhost:8099/stratum-api/1/summary | jq -c '{upstreams,miners}'
echo "--- config via nginx:"; curl -s localhost:8099/stratum-api/1/config | jq -c '.pools[0] | {url,user,coin,daemon}'
echo "--- unlisted path status:"; curl -s -o /dev/null -w '%{http_code}\n' localhost:8099/stratum-api/1/other
echo "--- cache header:"; curl -sI localhost:8099/ | grep -i cache-control
