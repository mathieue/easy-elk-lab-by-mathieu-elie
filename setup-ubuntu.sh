#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24.04 machine for the labs: Docker Engine, the docker compose
# plugin, docker group, vm.max_map_count, and the lab images pulled in advance.
# Run it once as root: sudo ./setup-ubuntu.sh   (help: ./setup-ubuntu.sh --help)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

MIN_MAP_COUNT=262144
MIN_MEM_MB=8192
MIN_DISK_GB=20
SYSCTL_FILE=/etc/sysctl.d/99-easy-elk-lab.conf
BASE_PACKAGES="ca-certificates curl git"
CONFLICTING_PACKAGES="docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc"
DOCKER_PACKAGES="docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"

ASSUME_YES=0
PULL_IMAGES=1
TARGET_USER="${SUDO_USER:-}"
SKIPPED=""
NEED_RELOGIN=0

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
info()  { echo "       $*"; }

# Print a command, then run it.
run() { echo "       \$ $*"; "$@"; }

# Ask before a step. Always yes with --yes. Returns 1 (and remembers it) when declined.
confirm() {
  local answer
  if [ "$ASSUME_YES" -eq 1 ]; then
    return 0
  fi
  if ! read -r -p "       $1 [Y/n] " answer </dev/tty; then
    die "Cannot read the answer (no terminal). Run again with --yes."
  fi
  case "$answer" in
    ""|y|Y|yes|YES|o|O|oui) return 0 ;;
    *) warn "Skipped."; SKIPPED="$SKIPPED\n  - $2"; return 1 ;;
  esac
}

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }

usage() {
  cat <<EOF
Usage: sudo ./setup-ubuntu.sh [options]

Prepares a fresh Ubuntu 24.04 machine for the labs. Each step explains what it does
and asks before changing anything. Steps already done are skipped, so the script can
be run again safely.

  1. Install base packages ($BASE_PACKAGES)
  2. Remove distribution packages that conflict with Docker Engine
  3. Add the official Docker apt repository (download.docker.com)
  4. Install Docker Engine and the docker compose plugin
  5. Start Docker now and at every boot
  6. Add your user to the docker group (docker without sudo)
  7. Set vm.max_map_count=$MIN_MAP_COUNT, now and at every boot (Elasticsearch)
  8. Download the lab images in advance (a few GB, saves time on the first start)

Options:
  -y, --yes          Do not ask for confirmation, run every step
  -u, --user NAME    User to add to the docker group (default: the user running sudo)
      --no-pull      Skip step 8 (images are then downloaded by ./lab.sh start)
  -h, --help         Show this help

Then, as that user, in a new session:
  cd labs/v9-single-node && ./lab.sh start
EOF
}

# ---------------------------------------------------------------- steps

check_machine() {
  title "Checking the machine"

  [ "$(id -u)" -eq 0 ] || die "Run this script as root: sudo $0"

  [ -r /etc/os-release ] || die "Cannot find /etc/os-release: is this Ubuntu?"
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] || die "This script is for Ubuntu (found: ${PRETTY_NAME:-unknown})."
  if [ "${VERSION_ID:-}" = "24.04" ]; then
    ok "$PRETTY_NAME"
  else
    warn "$PRETTY_NAME: only Ubuntu 24.04 is tested. Continuing."
  fi

  [ "$(dpkg --print-architecture)" = "amd64" ] || [ "$(dpkg --print-architecture)" = "arm64" ] \
    || die "Unsupported architecture: $(dpkg --print-architecture) (amd64 or arm64 needed)."

  [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ] \
    || die "Cannot tell which user will run the labs. Use: sudo $0 --user NAME"
  id "$TARGET_USER" >/dev/null 2>&1 || die "User '$TARGET_USER' does not exist."
  ok "Lab user: $TARGET_USER"

  local mem_mb disk_gb
  mem_mb="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
  if [ "$mem_mb" -ge "$MIN_MEM_MB" ]; then
    ok "Memory: ${mem_mb} MB"
  else
    warn "Memory: ${mem_mb} MB (recommended: ${MIN_MEM_MB} MB). The lab may be slow or fail."
  fi
  disk_gb="$(df -Pk /var/lib | awk 'NR == 2 {print int($4 / 1024 / 1024)}')"
  if [ "$disk_gb" -ge "$MIN_DISK_GB" ]; then
    ok "Free disk space in /var/lib: ${disk_gb} GB"
  else
    warn "Free disk space in /var/lib: ${disk_gb} GB (recommended: ${MIN_DISK_GB} GB)."
  fi
}

step_base_packages() {
  title "1. Base packages"
  local missing="" pkg
  for pkg in $BASE_PACKAGES; do
    installed "$pkg" || missing="$missing $pkg"
  done
  if [ -z "$missing" ]; then
    ok "Already installed: $BASE_PACKAGES"
    return
  fi
  info "Missing:$missing (needed to download Docker and to run ./lab.sh)."
  confirm "Install them with apt?" "base packages" || return 0
  run apt-get update
  # shellcheck disable=SC2086 # one argument per package
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y $missing
  ok "Installed:$missing"
}

step_remove_conflicts() {
  title "2. Conflicting packages"
  local found="" pkg
  for pkg in $CONFLICTING_PACKAGES; do
    installed "$pkg" && found="$found $pkg"
  done
  if [ -z "$found" ]; then
    ok "None installed."
    return
  fi
  info "Found:$found"
  info "These Ubuntu packages conflict with Docker Engine from docker.com."
  info "Removing them keeps images, containers and volumes in /var/lib/docker."
  confirm "Remove them?" "remove conflicting packages" || return 0
  # shellcheck disable=SC2086 # one argument per package
  run env DEBIAN_FRONTEND=noninteractive apt-get remove -y $found
  ok "Removed:$found"
}

step_docker_repo() {
  title "3. Docker apt repository"
  if [ -f /etc/apt/sources.list.d/docker.sources ] && [ -f /etc/apt/keyrings/docker.asc ]; then
    ok "Already configured: /etc/apt/sources.list.d/docker.sources"
    return
  fi
  info "Adds the official Docker repository (https://download.docker.com/linux/ubuntu)"
  info "and its signing key, as documented on https://docs.docker.com/engine/install/ubuntu/"
  confirm "Add the Docker repository?" "Docker repository" || return 0
  # shellcheck disable=SC1091
  . /etc/os-release
  run install -m 0755 -d /etc/apt/keyrings
  run curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  run chmod a+r /etc/apt/keyrings/docker.asc
  echo "       \$ write /etc/apt/sources.list.d/docker.sources"
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  run apt-get update
  ok "Docker repository added."
}

step_docker_install() {
  title "4. Docker Engine and docker compose"
  local missing="" pkg
  for pkg in $DOCKER_PACKAGES; do
    installed "$pkg" || missing="$missing $pkg"
  done
  if [ -z "$missing" ]; then
    ok "Already installed: $(docker --version)"
    return
  fi
  [ -f /etc/apt/sources.list.d/docker.sources ] \
    || { warn "The Docker repository is not configured (step 3): cannot install."; SKIPPED="$SKIPPED\n  - Docker Engine"; return 0; }
  info "Installs:$missing"
  confirm "Install Docker Engine?" "Docker Engine" || return 0
  # shellcheck disable=SC2086 # one argument per package
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y $missing
  ok "Installed: $(docker --version)"
}

step_docker_service() {
  title "5. Docker service"
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed (step 4): nothing to start."
    return
  fi
  if systemctl is-enabled --quiet docker && systemctl is-active --quiet docker; then
    ok "Docker is running and starts at boot."
    return
  fi
  info "Starts the Docker daemon now and enables it at boot."
  confirm "Enable and start Docker?" "Docker service" || return 0
  run systemctl enable --now docker
  ok "Docker is running."
}

step_docker_group() {
  title "6. docker group"
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then
    ok "$TARGET_USER is already in the docker group."
    return
  fi
  if ! getent group docker >/dev/null; then
    warn "The docker group does not exist (Docker not installed?): skipped."
    SKIPPED="$SKIPPED\n  - docker group"
    return
  fi
  info "Lets $TARGET_USER run docker (and ./lab.sh) without sudo."
  info "Lab machine only: members of the docker group are root-equivalent."
  confirm "Add $TARGET_USER to the docker group?" "docker group" || return 0
  run usermod -aG docker "$TARGET_USER"
  ok "$TARGET_USER added to the docker group (effective at the next login)."
  NEED_RELOGIN=1
}

step_max_map_count() {
  title "7. vm.max_map_count"
  local current
  current="$(cat /proc/sys/vm/max_map_count)"
  if [ "$current" -ge "$MIN_MAP_COUNT" ] && grep -qs "vm.max_map_count" "$SYSCTL_FILE"; then
    ok "vm.max_map_count = $current, kept at boot by $SYSCTL_FILE"
    return
  fi
  info "Current value: $current. Elasticsearch needs at least $MIN_MAP_COUNT."
  info "Writes $SYSCTL_FILE (kept at boot) and applies it now."
  confirm "Set vm.max_map_count=$MIN_MAP_COUNT?" "vm.max_map_count" || return 0
  echo "       \$ write $SYSCTL_FILE"
  printf '# Elasticsearch (easy-elk-lab)\nvm.max_map_count = %s\n' "$MIN_MAP_COUNT" >"$SYSCTL_FILE"
  run sysctl -p "$SYSCTL_FILE"
  ok "vm.max_map_count = $(cat /proc/sys/vm/max_map_count)"
}

step_pull_images() {
  title "8. Lab images"
  if [ "$PULL_IMAGES" -eq 0 ]; then
    ok "Skipped (--no-pull): ./lab.sh start will download them."
    return
  fi
  if ! docker info >/dev/null 2>&1; then
    warn "Docker is not running (steps 4-5): cannot download the images."
    SKIPPED="$SKIPPED\n  - lab images"
    return
  fi
  local labs="" compose_file
  for compose_file in "$SCRIPT_DIR"/labs/*/compose.yml; do
    [ -f "$compose_file" ] && labs="$labs $(dirname "$compose_file")"
  done
  if [ -z "$labs" ]; then
    warn "No lab found in $SCRIPT_DIR/labs: nothing to download."
    return
  fi
  info "Downloads the images of every lab service (Elasticsearch, Kibana, Elastic Agent,"
  info "Logstash, Python) and builds the local ones: a few GB, several minutes."
  info "Otherwise ./lab.sh downloads them on first use, during the training."
  confirm "Download the images now?" "lab images" || return 0
  local lab
  for lab in $labs; do
    info "Lab: ${lab#"$SCRIPT_DIR"/}"
    # Placeholder secrets: only the image names are needed from the Compose file.
    (
      cd "$lab"
      export ELASTIC_PASSWORD=pull KIBANA_PASSWORD=pull KIBANA_ENCRYPTION_KEY=pull
      run docker compose --env-file .env.example --profile '*' pull --ignore-buildable
      run docker compose --env-file .env.example --profile '*' build
    )
  done
  ok "Images ready."
}

summary() {
  title "Summary"
  local good=1
  if docker compose version >/dev/null 2>&1; then
    ok "$(docker --version)"
    ok "$(docker compose version)"
  else
    fail "docker compose is not available."; good=0
  fi
  if systemctl is-active --quiet docker 2>/dev/null; then
    ok "Docker service is running."
  else
    fail "Docker service is not running."; good=0
  fi
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then
    ok "$TARGET_USER is in the docker group."
  else
    fail "$TARGET_USER is not in the docker group."; good=0
  fi
  if [ "$(cat /proc/sys/vm/max_map_count)" -ge "$MIN_MAP_COUNT" ]; then
    ok "vm.max_map_count = $(cat /proc/sys/vm/max_map_count)"
  else
    fail "vm.max_map_count = $(cat /proc/sys/vm/max_map_count) (needs $MIN_MAP_COUNT)."; good=0
  fi
  if [ -n "$SKIPPED" ]; then
    warn "Skipped steps:"
    printf '%b\n' "$SKIPPED"
  fi

  title "Next steps (as $TARGET_USER)"
  if [ "${NEED_RELOGIN:-0}" -eq 1 ]; then
    echo "  Log out and log back in (or reboot), so the docker group applies."
  fi
  echo "  cd $SCRIPT_DIR/labs/v9-single-node"
  echo "  ./lab.sh start"
  echo
  [ "$good" -eq 1 ] || { warn "Some prerequisites are still missing: run this script again."; exit 1; }
  ok "The machine is ready for the labs."
}

# ---------------------------------------------------------------- main

while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes)   ASSUME_YES=1 ;;
    -u|--user)  [ $# -ge 2 ] || die "--user needs a user name."; TARGET_USER="$2"; shift ;;
    --no-pull)  PULL_IMAGES=0 ;;
    -h|--help)  usage; exit 0 ;;
    *)          usage; echo; die "Unknown option: $1" ;;
  esac
  shift
done

echo "${BOLD}Easy ELK lab: machine setup${RESET}"
info "Installs and configures what the labs need. Each step explains what it does"
if [ "$ASSUME_YES" -eq 1 ]; then
  info "and runs without asking (--yes)."
else
  info "and asks before changing anything (use --yes to skip the questions)."
fi

check_machine
step_base_packages
step_remove_conflicts
step_docker_repo
step_docker_install
step_docker_service
step_docker_group
step_max_map_count
step_pull_images
summary
