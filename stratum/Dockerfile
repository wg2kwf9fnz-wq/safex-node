# Safex stratum server: xmrig-proxy in solo "daemon" mode, patched for Safex (rx/sfx, v1 miner tx).
FROM debian:bookworm-slim AS builder

ARG XMRIG_PROXY_VERSION=v6.26.0
ARG XMRIG_PROXY_COMMIT=0dc23f6

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential cmake libuv1-dev libssl-dev python3 \
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

# Offline self-test: replay a real Safex mainnet block template through the proxy and check
# the job blob / submitted block byte-for-byte. The image build fails if this is wrong.
COPY tests /src/tests
RUN cd /src/tests && W=$(python3 sfxaddr.py) \
    && printf '{"mode":"extra_nonce","donate-level":0,"bind":[{"host":"127.0.0.1","port":3333}],"pools":[{"url":"127.0.0.1:17412","user":"%s","coin":"SFX","daemon":true,"daemon-poll-interval":1000}],"colors":false}' "$W" > /tmp/t.json \
    && (timeout 60 /src/xmrig-proxy/build/xmrig-proxy -c /tmp/t.json >/dev/null 2>&1 &) \
    && timeout 90 python3 e2e_test.py tpl-mainnet-2099175.json "$W" 3333 17412

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libuv1 libssl3 jq tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/xmrig-proxy/build/xmrig-proxy /usr/local/bin/xmrig-proxy
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME /data
EXPOSE 17404 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
