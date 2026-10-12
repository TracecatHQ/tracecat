#!/bin/sh
# Start the Pangolin machine client, then the Tracecat gateway proxy.
# If either process exits, the container exits so Kubernetes restarts it.
set -eu

: "${PANGOLIN_ENDPOINT:?PANGOLIN_ENDPOINT is required}"
: "${PANGOLIN_CLIENT_ID:?PANGOLIN_CLIENT_ID is required}"
: "${PANGOLIN_CLIENT_SECRET:?PANGOLIN_CLIENT_SECRET is required}"

# Pangolin overrides DNS to resolve tunnel aliases. Forward everything else to
# the resolver the container started with (cluster DNS), so the proxy can
# still reach the Tracecat API.
upstream_dns="${PANGOLIN_UPSTREAM_DNS:-$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf)}"

pangolin up client \
  --attach \
  --silent \
  --endpoint "$PANGOLIN_ENDPOINT" \
  --id "$PANGOLIN_CLIENT_ID" \
  --secret "$PANGOLIN_CLIENT_SECRET" \
  --upstream-dns "${upstream_dns}:53" &
client_pid=$!

# Keep the client secret out of the proxy's environment.
unset PANGOLIN_CLIENT_SECRET

python -m tracecat_ee.tunnels.gateway &
proxy_pid=$!

trap 'kill "$client_pid" "$proxy_pid" 2>/dev/null' INT TERM
while kill -0 "$client_pid" 2>/dev/null && kill -0 "$proxy_pid" 2>/dev/null; do
  sleep 2
done
kill "$client_pid" "$proxy_pid" 2>/dev/null || true
wait || true
exit 1
