# Safex stratum gateway: per-miner payout address (solo mining), built on xmrig-proxy patched for Safex.
FROM debian:bookworm-slim AS builder

ARG XMRIG_PROXY_VERSION=v6.26.0
ARG XMRIG_PROXY_COMMIT=0dc23f6

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential cmake libuv1-dev libssl-dev python3 procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${XMRIG_PROXY_VERSION} https://github.com/xmrig/xmrig-proxy.git \
    && cd xmrig-proxy && test "$(git rev-parse --short=7 HEAD)" = "${XMRIG_PROXY_COMMIT}"

COPY xmrig-proxy-safex.patch /src/
RUN cd xmrig-proxy && git apply --whitespace=nowarn /src/xmrig-proxy-safex.patch \
    && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=Release \
    && make -j"$(nproc)" \
    && strip xmrig-proxy

# Offline self-tests (fail the image build if anything is wrong):
#  1. proxy: replay a real Safex mainnet block template, check job blob / submitted block byte for byte
#  2. gateway: per-address routing, block credit per address, 12h worker expiry, persistence, limits
COPY tests /src/tests
COPY gateway.py /src/gateway.py
RUN cd /src/tests && W=$(python3 sfxaddr.py) \
    && printf '{"mode":"extra_nonce","donate-level":0,"bind":[{"host":"127.0.0.1","port":3333}],"pools":[{"url":"127.0.0.1:17412","user":"%s","coin":"SFX","daemon":true,"daemon-poll-interval":1000}],"colors":false}' "$W" > /tmp/t.json \
    && (timeout 60 /src/xmrig-proxy/build/xmrig-proxy -c /tmp/t.json >/dev/null 2>&1 &) \
    && timeout 90 python3 e2e_test.py tpl-mainnet-2099175.json "$W" 3333 17412 \
    && pkill -f /tmp/t.json || true
RUN cd /src/tests && timeout 150 python3 gateway_test.py tpl-mainnet-2099175.json /src/gateway.py /src/xmrig-proxy/build/xmrig-proxy

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libuv1 libssl3 python3-minimal tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/xmrig-proxy/build/xmrig-proxy /usr/local/bin/xmrig-proxy
COPY gateway.py /usr/local/bin/gateway.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME /data
EXPOSE 17404 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
