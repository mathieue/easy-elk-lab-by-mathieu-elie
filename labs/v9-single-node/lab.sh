#!/usr/bin/env bash
# Manage this lab: ./lab.sh help
set -euo pipefail

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$LAB_DIR"

MIN_MAP_COUNT=262144
MIN_FREE_MEM_MB=6144
# Bare servers for Elastic Agent labs (Compose services of the "agents" profile)
AGENT_HOSTS="web-04"

# ---------------------------------------------------------------- output

if [ -t 1 ]; then
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
  RED=""; GREEN=""; YELLOW=""; BOLD=""; RESET=""
fi

ok()    { echo "${GREEN}[ OK ]${RESET} $*"; }
warn()  { echo "${YELLOW}[WARN]${RESET} $*"; }
fail()  { echo "${RED}[FAIL]${RESET} $*"; }
die()   { fail "$*"; exit 1; }
title() { echo; echo "${BOLD}$*${RESET}"; }

# ---------------------------------------------------------------- helpers

compose() { docker compose "$@"; }

load_env() {
  [ -f .env ] || die ".env not found. Run: ./lab.sh init"
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
}

random_string() {
  # $1 = length. Alphanumeric only, so values are safe in shell, JSON and URLs.
  head -c 2000 /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-"$1"
}

host_ip() {
  local ip=""
  if command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  fi
  echo "${ip:-localhost}"
}

port_in_use() {
  command -v ss >/dev/null 2>&1 || return 1
  ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
}

lab_running() {
  [ -n "$(compose ps -q es01 2>/dev/null)" ]
}

has_test_data() {
  # True when generated data streams exist (lab running, .env loaded).
  local found
  found="$(es_query GET "/_data_stream/logs-*-lab,metrics-*-lab?filter_path=data_streams.name" 2>/dev/null || true)"
  case "$found" in
    *'"name"'*) return 0 ;;
  esac
  return 1
}

es_url() { echo "http://localhost:${ES_PORT:-9200}"; }

kibana_url() { echo "http://localhost:${KIBANA_PORT:-5601}"; }

fleet_created() {
  [ -n "$(compose --profile fleet ps -aq fleet-server 2>/dev/null)" ]
}

logstash_created() {
  [ -n "$(compose --profile logstash ps -aq logstash 2>/dev/null)" ]
}

agent_host_created() {
  [ -n "$(compose --profile agents ps -aq "$1" 2>/dev/null)" ]
}

# ---------------------------------------------------------------- commands

cmd_init() {
  if [ -f .env ]; then
    ok ".env already exists, keeping it."
    return
  fi
  [ -f .env.example ] || die ".env.example not found."
  sed \
    -e "s/^ELASTIC_PASSWORD=$/ELASTIC_PASSWORD=$(random_string 32)/" \
    -e "s/^KIBANA_PASSWORD=$/KIBANA_PASSWORD=$(random_string 32)/" \
    -e "s/^KIBANA_ENCRYPTION_KEY=$/KIBANA_ENCRYPTION_KEY=$(random_string 64)/" \
    .env.example > .env
  chmod 600 .env
  ok "Created .env with random passwords."
}

cmd_check() {
  local errors=0

  title "Checking prerequisites"

  if command -v docker >/dev/null 2>&1; then
    ok "docker is installed."
  else
    fail "docker is not installed. See https://docs.docker.com/engine/install/"
    return 1
  fi

  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is reachable."
  else
    fail "Cannot talk to the Docker daemon. Is it running? Is your user in the docker group?"
    echo "       sudo usermod -aG docker \"\$USER\"   # then log out and back in"
    return 1
  fi

  if docker compose version >/dev/null 2>&1; then
    ok "docker compose plugin is installed."
  else
    fail "The 'docker compose' plugin is missing. See https://docs.docker.com/compose/install/"
    errors=$((errors + 1))
  fi

  if command -v curl >/dev/null 2>&1; then
    ok "curl is installed."
  else
    warn "curl is not installed (needed for './lab.sh es')."
  fi

  if [ -r /proc/sys/vm/max_map_count ]; then
    local map_count
    map_count="$(cat /proc/sys/vm/max_map_count)"
    if [ "$map_count" -ge "$MIN_MAP_COUNT" ]; then
      ok "vm.max_map_count = $map_count"
    else
      fail "vm.max_map_count = $map_count (Elasticsearch needs at least $MIN_MAP_COUNT). Fix with:"
      echo "       sudo sysctl -w vm.max_map_count=$MIN_MAP_COUNT"
      errors=$((errors + 1))
    fi
  fi

  if [ -r /proc/meminfo ]; then
    local free_mb
    free_mb="$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)"
    if [ "$free_mb" -ge "$MIN_FREE_MEM_MB" ]; then
      ok "Available memory: ${free_mb} MB"
    else
      warn "Available memory: ${free_mb} MB (recommended: ${MIN_FREE_MEM_MB} MB). The lab may be slow or fail."
    fi
  fi

  if [ -f .env ]; then
    load_env
    ok ".env found (stack version ${STACK_VERSION})."
    if lab_running; then
      ok "Lab is already running, skipping port checks."
    else
      local port
      for port in "${ES_PORT:-9200}" "${KIBANA_PORT:-5601}"; do
        if port_in_use "$port"; then
          fail "Port $port is already in use. Stop what uses it, or change the port in .env."
          errors=$((errors + 1))
        else
          ok "Port $port is free."
        fi
      done
    fi
  else
    warn ".env not found yet (created by './lab.sh init' or './lab.sh start')."
  fi

  echo
  if [ "$errors" -gt 0 ]; then
    fail "$errors problem(s) found."
    return 1
  fi
  ok "All good."
}

cmd_start() {
  cmd_init
  cmd_check || die "Fix the problems above, then run ./lab.sh start again."
  load_env
  title "Starting the lab (Kibana can take a couple of minutes)"
  compose up -d --wait --wait-timeout 600 es01 setup kibana
  cmd_info
  if fleet_created; then
    compose --profile fleet up -d fleet-server >/dev/null 2>&1
    ok "Fleet Server restarted."
  fi
  if logstash_created; then
    compose --profile logstash up -d logstash fw-02 >/dev/null 2>&1
    ok "Logstash and fw-02 restarted."
  fi
  local host
  for host in $AGENT_HOSTS; do
    if agent_host_created "$host"; then
      compose --profile agents up -d "$host" >/dev/null 2>&1
      ok "Server $host restarted."
    fi
  done
  if has_test_data; then
    compose --profile datagen up -d datagen >/dev/null 2>&1
    ok "Live test data restarted (it first catches up on the time the lab was stopped)."
  else
    echo
    warn "No test data yet. Load it with: ./lab.sh data load"
  fi
}

cmd_stop() {
  title "Stopping the lab (data is kept)"
  compose --profile '*' stop
  ok "Stopped. Start again with: ./lab.sh start"
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_status() {
  title "Containers"
  compose ps -a
  if [ -f .env ] && lab_running; then
    load_env
    title "Cluster health"
    curl -s -u "elastic:${ELASTIC_PASSWORD}" "$(es_url)/_cluster/health?pretty" \
      || warn "Elasticsearch is not answering yet."
  fi
}

cmd_info() {
  load_env
  local ip
  ip="$(host_ip)"
  title "Lab access"
  echo "  Kibana         http://${ip}:${KIBANA_PORT:-5601}"
  echo "  Elasticsearch  http://${ip}:${ES_PORT:-9200}"
  echo "  User           elastic"
  echo "  Password       ${ELASTIC_PASSWORD}"
  echo
  if ! lab_running; then
    echo "  Lab            stopped: ./lab.sh start"
  elif has_test_data; then
    echo "  Test data      Kibana > Discover, one data view per dataset (./lab.sh data status)"
  else
    echo "  Test data      none yet: ./lab.sh data load"
  fi
  echo "  Try:  ./lab.sh es GET /"
}

cmd_logs() {
  compose logs -f --tail 100 "$@"
}

cmd_es() {
  [ $# -ge 2 ] || die "Usage: ./lab.sh es <METHOD> <path> [json-body]   e.g. ./lab.sh es GET /_cat/indices?v"
  load_env
  local path="$2"
  case "$path" in
    /*) ;;
    *) path="/$path" ;;
  esac
  es_query "$1" "$path" "${@:3}"
  echo
}

es_query() {
  # $1 = method, $2 = path, $3 = optional JSON body. Prints the raw response.
  local args=(-s -u "elastic:${ELASTIC_PASSWORD}" -X "$1" -H "Content-Type: application/json")
  [ $# -ge 3 ] && args+=(-d "$3")
  curl "${args[@]}" "$(es_url)$2"
}

kb_query() {
  # $1 = method, $2 = Kibana API path, $3 = optional JSON body. Prints the raw response
  # followed by a last line with the HTTP status code.
  local args=(-s -w '\n%{http_code}' -u "elastic:${ELASTIC_PASSWORD}" -X "$1"
    -H "Content-Type: application/json" -H "kbn-xsrf: true")
  [ $# -ge 3 ] && args+=(-d "$3")
  curl "${args[@]}" "$(kibana_url)$2"
}

kb_call() {
  # Like kb_query, but fails with the response when the status is not 2xx.
  local out code
  out="$(kb_query "$@")"
  code="$(echo "$out" | tail -n 1)"
  case "$code" in
    2*) return 0 ;;
  esac
  fail "Kibana API $1 $2 returned HTTP $code:"
  echo "$out" | sed '$d'
  exit 1
}

kb_exists() {
  # True when the Kibana API object at $1 exists.
  [ "$(kb_query GET "$1" | tail -n 1)" = "200" ]
}

fleet_prepare() {
  # Fleet settings the Fleet Server needs. Only creates what is missing.
  kb_call POST /api/fleet/setup
  ok "Fleet is set up in Kibana."
  # Agents run in containers on the lab network: they reach Elasticsearch as es01.
  kb_call PUT /api/fleet/outputs/fleet-default-output \
    '{"name":"default","type":"elasticsearch","hosts":["http://es01:9200"]}'
  ok "Default output: http://es01:9200"
  if ! kb_exists /api/fleet/fleet_server_hosts/fleet-server-lab; then
    kb_call POST /api/fleet/fleet_server_hosts \
      '{"id":"fleet-server-lab","name":"Lab Fleet Server","host_urls":["https://fleet-server:8220"],"is_default":true}'
  fi
  ok "Fleet Server host: https://fleet-server:8220"
  if ! kb_exists /api/fleet/agent_policies/fleet-server-policy; then
    kb_call POST "/api/fleet/agent_policies?sys_monitoring=true" \
      '{"id":"fleet-server-policy","name":"Fleet Server policy","namespace":"default","has_fleet_server":true,"monitoring_enabled":["logs","metrics"]}'
  fi
  ok "Agent policy: Fleet Server policy"
}

fleet_wait_online() {
  # Waits until the Fleet Server agent shows as online in Fleet (max ~2 minutes).
  local i
  for ((i = 0; i < 40; i++)); do
    case "$(kb_query GET "/api/fleet/agents?perPage=1&kuery=policy_id:fleet-server-policy%20and%20status:online")" in
      *'"total":0'*) ;;
      *'"total":'*) return 0 ;;
    esac
    sleep 3
  done
  return 1
}

cmd_fleet() {
  local action="${1:-status}"
  load_env
  case "$action" in
    start)
      lab_running || die "The lab is not running. Start it first: ./lab.sh start"
      title "Preparing Fleet"
      fleet_prepare
      title "Starting Fleet Server"
      compose --profile fleet up -d --wait --wait-timeout 300 fleet-server
      if fleet_wait_online; then
        ok "Fleet Server is online: Kibana > Management > Fleet > Agents"
      else
        warn "Fleet Server is running but not online in Fleet yet. Check: ./lab.sh fleet logs"
      fi
      echo "  Agents on the lab network enroll with: --url=https://fleet-server:8220 --insecure"
      ;;
    stop)
      title "Stopping Fleet Server (Fleet settings and enrollment are kept)"
      compose --profile fleet stop fleet-server
      ;;
    status)
      title "Fleet Server"
      compose --profile fleet ps -a fleet-server
      if fleet_created && lab_running; then
        title "Agents in Fleet"
        kb_query GET "/api/fleet/agent_status" | sed '$d'
        echo
      fi
      ;;
    logs)
      compose --profile fleet logs -f --tail 50 fleet-server
      ;;
    *)
      die "Unknown fleet command '$action'. See ./lab.sh help"
      ;;
  esac
}

agent_host_arg() {
  # $1 = host name given by the user (default: first host). Prints it if valid.
  # Called in $(...): the error goes to stderr so that it is shown.
  local host="${1:-${AGENT_HOSTS%% *}}"
  case " $AGENT_HOSTS " in
    *" $host "*) echo "$host" ;;
    *) fail "Unknown server '$host'. Available: $AGENT_HOSTS" >&2; exit 1 ;;
  esac
}

cmd_agent() {
  local action="${1:-status}" host
  [ $# -gt 0 ] && shift
  load_env
  case "$action" in
    shell)
      host="$(agent_host_arg "${1:-}")"
      lab_running || die "The lab is not running. Start it first: ./lab.sh start"
      if ! docker image inspect easy-elk-lab/agent-host:ubuntu-24.04-v2 >/dev/null 2>&1; then
        title "Building the server image (first time only)"
        compose --profile agents build "$host"
      fi
      compose --profile agents up -d "$host" >/dev/null
      title "Shell on $host as user 'student' (sudo allowed). Leave with: exit"
      echo "  Paste the Linux commands from Kibana > Fleet > Add agent."
      echo "  Fleet Server uses a self-signed certificate: add --insecure to the install command."
      echo
      compose exec -u student -w /home/student "$host" bash
      ;;
    status)
      title "Servers"
      # shellcheck disable=SC2086 # one argument per server
      compose --profile agents ps -a $AGENT_HOSTS
      for host in $AGENT_HOSTS; do
        if [ -n "$(compose --profile agents ps -q --status running "$host" 2>/dev/null)" ]; then
          title "Elastic Agent on $host"
          compose exec -T "$host" sh -c \
            'if [ -x /etc/init.d/elastic-agent ]; then elastic-agent status; else echo "Not installed."; fi' \
            || true
        fi
      done
      ;;
    stop)
      host="$(agent_host_arg "${1:-}")"
      title "Stopping server $host (what is installed on it is kept)"
      compose --profile agents stop "$host"
      ;;
    reset)
      host="$(agent_host_arg "${1:-}")"
      title "This deletes server $host and everything installed on it."
      if [ "${2:-}" != "--yes" ]; then
        printf "Type 'yes' to continue: "
        local answer
        read -r answer
        [ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
      fi
      compose --profile agents rm -sf "$host"
      ok "Server $host deleted. The next './lab.sh agent shell $host' gives a fresh one."
      echo "  If its agent was enrolled, it now shows as offline in Fleet: unenroll it there."
      ;;
    *)
      die "Unknown agent command '$action'. See ./lab.sh help"
      ;;
  esac
}

cmd_logstash() {
  local action="${1:-status}"
  load_env
  case "$action" in
    start)
      lab_running || die "The lab is not running. Start it first: ./lab.sh start"
      if [ ! -d logstash/pipeline ]; then
        cp -R logstash/pipeline.example logstash/pipeline
        ok "Created logstash/pipeline/ (your pipeline: logstash/pipeline/fw-02.conf)."
      fi
      title "Starting Logstash and fw-02 (Logstash takes about a minute)"
      compose --profile logstash up -d --wait --wait-timeout 300 logstash fw-02
      ok "Logstash is running. Edit logstash/pipeline/fw-02.conf: it reloads on save."
      echo "  See the events: ./lab.sh logstash logs"
      ;;
    stop)
      title "Stopping Logstash and fw-02 (your pipeline is kept)"
      compose --profile logstash stop logstash fw-02
      ;;
    status)
      title "Logstash"
      compose --profile logstash ps -a logstash fw-02
      if [ -n "$(compose --profile logstash ps -q --status running logstash 2>/dev/null)" ]; then
        title "Events (in = received, out = sent by the outputs)"
        compose exec -T logstash curl -s "http://localhost:9600/_node/stats/events?pretty" | sed -n '/"events"/,/}/p'
      fi
      ;;
    logs)
      compose --profile logstash logs -f --tail 50 logstash
      ;;
    reset)
      title "This replaces logstash/pipeline/ with the example pipeline."
      if [ "${2:-}" != "--yes" ]; then
        printf "Type 'yes' to continue: "
        local answer
        read -r answer
        [ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
      fi
      rm -rf logstash/pipeline
      cp -R logstash/pipeline.example logstash/pipeline
      ok "Pipeline reset to logstash/pipeline.example/ (Logstash reloads it)."
      ;;
    *)
      die "Unknown logstash command '$action'. See ./lab.sh help"
      ;;
  esac
}

draw_bar() {
  # $1 = dataset, $2 = percent, $3 = docs. Uses width/full/empty from render_progress.
  local filled=$(( $2 * width / 100 )) bar="" j
  for ((j = 0; j < width; j++)); do
    if [ "$j" -lt "$filled" ]; then bar+="$full"; else bar+="$empty"; fi
  done
  printf '\r\033[K  %-11s [%s] %3d%%  %9s docs\n' "$1" "$bar" "$2" "$3"
}

render_progress() {
  # Reads the generator's "load --progress" lines on stdin, draws one bar per dataset.
  local width=30 full="#" empty="-" tty=0 drawn=0
  local names=() pcts=() docs=() steps=()
  local kind name pct count i step
  [ -t 1 ] && tty=1
  case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *UTF-8*|*utf8*) full="█"; empty="░" ;;
  esac
  while read -r kind name pct count; do
    case "$kind" in
      plan)
        echo "  From $(date -u -d "@$name" '+%Y-%m-%d %H:%M') UTC to now"
        echo
        continue
        ;;
      dataset)
        names+=("$name"); pcts+=("$pct"); docs+=("$count"); steps+=(-1)
        i=$(( ${#names[@]} - 1 ))
        ;;
      progress|done)
        for i in "${!names[@]}"; do
          [ "${names[$i]}" = "$name" ] && break
        done
        pcts[i]=$pct
        docs[i]=$count
        [ "$kind" = "done" ] && pcts[i]=100
        ;;
      *)
        continue
        ;;
    esac
    if [ "$tty" = 1 ]; then
      [ "$drawn" -gt 0 ] && printf '\033[%dA' "$drawn"
      for i in "${!names[@]}"; do
        draw_bar "${names[$i]}" "${pcts[$i]}" "${docs[$i]}"
      done
      drawn=${#names[@]}
    else
      # Not a terminal: one plain line every 10 %.
      step=$(( pcts[i] / 10 ))
      if [ "$step" -gt "${steps[$i]}" ]; then
        steps[i]=$step
        printf '  %-11s %3d%%  %s docs\n' "${names[$i]}" "${pcts[$i]}" "${docs[$i]}"
      fi
    fi
  done
}

data_load() {
  # $1 = days (optional), $2 = datasets (optional, comma separated)
  local env_args=() log status
  lab_running || die "The lab is not running. Start it first: ./lab.sh start"
  [ -n "$1" ] && env_args+=(-e "DATAGEN_HISTORY_DAYS=$1")
  [ -n "$2" ] && env_args+=(-e "DATAGEN_DATASETS=$2")
  title "Loading test data (${1:-$DATAGEN_HISTORY_DAYS} days: ${2:-$DATAGEN_DATASETS})"
  # Live generation would compete with the load: it restarts at the end.
  compose --profile datagen stop datagen >/dev/null 2>&1 || true
  log="$(mktemp)"
  trap 'echo; warn "Interrupted. Run ./lab.sh data load again: it resumes where it stopped."; rm -f "$log"; exit 130' INT
  set +e
  compose --profile datagen run --rm --no-deps -T ${env_args[@]+"${env_args[@]}"} datagen \
    python -u /app/datagen.py load --progress 2>"$log" | render_progress
  status=${PIPESTATUS[0]}
  set -e
  trap - INT
  if [ "$status" -eq 130 ]; then
    # The generator stopped cleanly on Ctrl-C: everything sent so far is kept.
    echo
    warn "Interrupted. Run ./lab.sh data load again: it resumes where it stopped."
    rm -f "$log"
    exit 130
  fi
  if [ "$status" -ne 0 ]; then
    fail "Loading failed (exit code $status). Last generator messages:"
    tail -n 20 "$log"
    rm -f "$log"
    exit 1
  fi
  rm -f "$log"
  echo
  ok "Test data loaded."
  compose --profile datagen up -d datagen >/dev/null 2>&1
  ok "Live generation started (stop it with: ./lab.sh data stop)."
  echo "  In Kibana: Discover, then pick a data view (Web logs, Firewall logs, Database logs, Host metrics)."
}

cmd_data() {
  local action="${1:-status}"
  [ $# -gt 0 ] && shift
  load_env
  case "$action" in
    status)
      title "Data generator"
      compose --profile datagen ps -a datagen
      title "Generated data"
      es_query POST "/_query?format=txt" \
        '{"query":"FROM logs-*-lab,metrics-*-lab | STATS docs = COUNT(*), oldest = MIN(@timestamp), newest = MAX(@timestamp) BY data_stream.dataset"}' \
        | grep -v '"status":400' || true
      echo
      echo "Current time (UTC): $(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
      ;;
    load)
      local days="" datasets=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --days|--datasets)
            [ $# -ge 2 ] || die "Missing value after $1"
            if [ "$1" = "--days" ]; then days="$2"; else datasets="$2"; fi
            shift 2
            ;;
          *)
            die "Usage: ./lab.sh data load [--days N] [--datasets apache,firewall,metrics,postgresql]"
            ;;
        esac
      done
      data_load "$days" "$datasets"
      ;;
    start)
      if has_test_data; then
        title "Starting live test data"
        compose --profile datagen up -d datagen
        ok "Follow it with: ./lab.sh data logs"
      else
        data_load "" ""
      fi
      ;;
    stop)
      title "Stopping the data generator (data is kept)"
      compose --profile datagen stop datagen
      ;;
    logs)
      compose --profile datagen logs -f --tail 50 datagen
      ;;
    backfill)
      [ $# -eq 2 ] || die "Usage: ./lab.sh data backfill <from> <to>   e.g. ./lab.sh data backfill now-7d now+3d"
      compose --profile datagen run --rm --no-deps datagen python -u /app/datagen.py backfill "$1" "$2"
      ;;
    reset)
      title "This deletes all generated data, then loads it again from scratch."
      if [ "${1:-}" != "--yes" ]; then
        printf "Type 'yes' to continue: "
        local answer
        read -r answer
        [ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
      fi
      compose --profile datagen stop datagen
      es_query DELETE "/_data_stream/logs-*-lab,metrics-*-lab" >/dev/null
      echo
      ok "Generated data deleted."
      data_load "" ""
      ;;
    *)
      die "Unknown data command '$action'. See ./lab.sh help"
      ;;
  esac
}

cmd_destroy() {
  title "This removes the containers AND all lab data (indices, Kibana objects)."
  if [ "${1:-}" != "--yes" ]; then
    printf "Type 'yes' to continue: "
    local answer
    read -r answer
    [ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
  fi
  compose --profile '*' down -v
  ok "Lab removed. Your .env (passwords) is kept. Start fresh with: ./lab.sh start"
}

cmd_help() {
  cat <<EOF
Usage: ./lab.sh <command>

  start              Create .env if needed, check prerequisites, start the lab
  stop               Stop the lab (data is kept)
  restart            Stop then start
  status             Show containers and cluster health
  info               Show URLs and credentials
  logs [service]     Follow logs (services: es01, kibana, setup, fleet-server)
  es <METHOD> <path> [body]
                     Send an authenticated request to Elasticsearch, e.g.
                       ./lab.sh es GET /_cat/indices?v
                       ./lab.sh es PUT /my-index '{"settings":{"number_of_replicas":0}}'
  data [action]      Test data (not loaded at start):
                       load [--days N] [--datasets a,b]
                                         load history with progress bars, then go live
                       status            what has been generated (default)
                       start | stop      start or stop live generation
                       logs              follow the generator logs
                       backfill FROM TO  load a time range, e.g. now-7d now+3d
                       reset [--yes]     delete generated data and load it again
  fleet [action]     Fleet Server (not started by default, about 1 GB more memory):
                       start             prepare Fleet in Kibana, start Fleet Server
                       status            Fleet Server container and agent counts (default)
                       stop              stop Fleet Server (enrollment is kept)
                       logs              follow the Fleet Server logs
  logstash [action]  Logstash + fw-02 (firewall sending syslog), not started by default:
                       start             create logstash/pipeline/ if needed, start them
                       status            containers and event counters (default)
                       logs              follow Logstash (the stdout output shows here)
                       stop              stop them (your pipeline is kept)
                       reset [--yes]     replace your pipeline with the example
  agent [action] [server]
                     Bare Linux servers to install Elastic Agent on (servers: $AGENT_HOSTS):
                       shell [server]    open a shell as 'student' (creates the server)
                       status            servers and Elastic Agent status (default)
                       stop [server]     stop a server (what is installed is kept)
                       reset <server> [--yes]
                                         delete a server, to start again from scratch
  check              Check prerequisites only
  init               Create .env with random passwords (never overwrites)
  destroy [--yes]    Remove the lab and ALL its data
EOF
}

# ---------------------------------------------------------------- main

command="${1:-help}"
[ $# -gt 0 ] && shift

case "$command" in
  start)          cmd_start ;;
  stop)           cmd_stop ;;
  restart)        cmd_restart ;;
  status)         cmd_status ;;
  info)           cmd_info ;;
  logs)           cmd_logs "$@" ;;
  es)             cmd_es "$@" ;;
  data)           cmd_data "$@" ;;
  fleet)          cmd_fleet "$@" ;;
  agent)          cmd_agent "$@" ;;
  logstash)       cmd_logstash "$@" ;;
  check)          cmd_check ;;
  init)           cmd_init ;;
  destroy)        cmd_destroy "$@" ;;
  help|-h|--help) cmd_help ;;
  *)              cmd_help; exit 1 ;;
esac
