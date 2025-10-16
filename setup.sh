#!/usr/bin/env bash
# Bootstrap script for the Linode Ubuntu LTS VM.
# Run as a non-root user with sudo privileges from the project root.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_PATH="${PROJECT_ROOT}/.venv"

echo "[*] Updating package index..."
sudo apt-get update -y

echo "[*] Installing system packages..."
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  redis-server \
  nginx \
  git \
  ufw \
  unattended-upgrades

echo "[*] Enabling Redis on boot..."
sudo systemctl enable redis-server
sudo systemctl start redis-server

echo "[*] Configuring automatic security updates..."
sudo dpkg-reconfigure --priority=low unattended-upgrades

echo "[*] Setting up firewall rules..."
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "[*] Creating Python virtual environment at ${VENV_PATH}..."
${PYTHON_BIN} -m venv "${VENV_PATH}"
source "${VENV_PATH}/bin/activate"

echo "[*] Upgrading pip and installing dependencies..."
pip install --upgrade pip wheel
pip install -r "${PROJECT_ROOT}/requirements.txt"

echo "[*] Creating data directory..."
mkdir -p "${PROJECT_ROOT}/data"
chmod 700 "${PROJECT_ROOT}/data"

echo "[*] Copying environment template if .env missing..."
if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
  echo "[-] Remember to edit ${PROJECT_ROOT}/.env with your secrets."
fi

echo "[*] Setup complete."
