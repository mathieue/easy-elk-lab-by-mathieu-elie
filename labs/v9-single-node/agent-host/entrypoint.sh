#!/bin/sh
# There is no systemd in a container: `elastic-agent install` creates an init.d
# service instead. Start it with the container, and stop it cleanly with it.
# The lab data generator plays the web server: it writes the Apache access log.
set -eu

SERVICE=/etc/init.d/elastic-agent

mkdir -p /var/log/apache2
python3 -u /opt/datagen/datagen.py file --host "$(hostname)" \
  --path /var/log/apache2/access.log 2>>/var/log/datagen.log &
WEBLOG_PID=$!

stop_all() {
  kill "$WEBLOG_PID" 2>/dev/null || true
  if [ -x "$SERVICE" ]; then
    "$SERVICE" stop || true
  fi
  exit 0
}
trap stop_all TERM INT

if [ -x "$SERVICE" ]; then
  "$SERVICE" start || echo "Elastic Agent did not start, see /var/log/elastic-agent.err"
fi

sleep infinity &
wait $!
