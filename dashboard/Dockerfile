# Safex node + stratum dashboard. All files are baked into the image on purpose:
# Umbrel does not reliably refresh single files that are bind-mounted from the app data dir.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
RUN rm -f /etc/nginx/conf.d/default.conf
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html
# Background job that writes /storage.json (disk used by the node data dir, mounted read-only at /node-data).
COPY --chmod=755 storage-size.sh /docker-entrypoint.d/40-storage-size.sh

# CPU-usage toggle: a tiny CGI behind nginx (/api/cpu-mode) that writes /control/cpu_mode, which the node's
# start-up wrapper watches. Alpine ships httpd in busybox-extras; the build fails loudly if it is missing.
RUN apk add --no-cache busybox-extras \
    && busybox-extras --list | grep -qx httpd
COPY --chmod=755 cpu-mode.cgi /srv/cgi/cgi-bin/cpu-mode
COPY --chmod=755 40-cpu-mode.sh /docker-entrypoint.d/45-cpu-mode.sh

# Build-time check using the image's REAL start-up script, with only the one variable that
# docker-compose provides (STRATUM_API_TOKEN). Fails the build if any placeholder is left
# unreplaced or if nginx rejects the rendered config.
RUN STRATUM_API_TOKEN=buildcheck /docker-entrypoint.d/20-envsubst-on-templates.sh \
    && ! grep -F '${' /etc/nginx/conf.d/default.conf \
    && grep -q 'Bearer buildcheck' /etc/nginx/conf.d/default.conf \
    && nginx -t \
    && rm -f /etc/nginx/conf.d/default.conf
