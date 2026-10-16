#!/bin/sh
# There is no systemd in a container: `elastic-agent install` creates an init.d
# service instead. Start it with the container, and stop it cleanly with it.
set -eu

SERVICE=/etc/init.d/elastic-agent

stop_agent() {
  if [ -x "$SERVICE" ]; then
    "$SERVICE" stop || true
  fi
  exit 0
}
trap stop_agent TERM INT

if [ -x "$SERVICE" ]; then
  "$SERVICE" start || echo "Elastic Agent did not start, see /var/log/elastic-agent.err"
fi

sleep infinity &
wait $!
