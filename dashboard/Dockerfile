# Safex node + stratum dashboard. All files are baked into the image on purpose:
# Umbrel does not reliably refresh single files that are bind-mounted from the app data dir.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
RUN rm -f /etc/nginx/conf.d/default.conf
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html
# Build-time check: the template must render and pass nginx's own config test.
RUN STRATUM_API_TOKEN=buildcheck NGINX_LOCAL_RESOLVERS=127.0.0.11 \
      envsubst '${STRATUM_API_TOKEN} ${NGINX_LOCAL_RESOLVERS}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf \
    && nginx -t \
    && rm -f /etc/nginx/conf.d/default.conf
