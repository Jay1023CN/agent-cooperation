#!/usr/bin/env bash
set -euo pipefail

# Install the Agent Cooperation center on Ubuntu 22.04/24.04.
# Run as the user that should own the service; sudo is used only for packages
# and the systemd unit. The script never asks for or stores application tokens.

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer supports Linux only." >&2
  exit 1
fi
if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run as a normal user with sudo access, not root." >&2
  exit 1
fi

repo_dir="${ARENA_REPO_DIR:-/opt/agent-cooperation}"
data_dir="${ARENA_DATA_DIR:-$HOME/.pal-arena/server}"
service_file="/etc/systemd/system/agent-cooperation.service"

sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip
sudo install -d -o "$USER" -g "$(id -gn)" "$repo_dir"

if [[ -d "$repo_dir/.git" ]]; then
  git -C "$repo_dir" pull --ff-only
else
  git clone "${ARENA_REPO_URL:-https://github.com/Jay1023CN/agent-cooperation.git}" "$repo_dir"
fi

python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/pip" install --upgrade pip
"$repo_dir/.venv/bin/pip" install -e "$repo_dir[platform]"
install -d -m 700 "$data_dir"

sed -e "s|ARENA_USER|$USER|g" "$repo_dir/deploy/linux/agent-cooperation.service.example" \
  | sudo tee "$service_file" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now agent-cooperation

echo "Agent Cooperation is running. Check: systemctl status agent-cooperation"
echo "Owner token: $data_dir/owner-token.txt"
