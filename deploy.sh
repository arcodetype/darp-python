#!/bin/zsh

set -e

pip3 install pyinstaller
pip3 install colorama

# Always run relative to this script's directory (project root)
SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Cleaning old PyInstaller outputs..."
rm -rf build dist darp.spec 2>/dev/null || true

echo "==> Building Darp (fast ONEDIR mode)..."
pyinstaller \
  --onedir \
  --name darp \
  --console \
  --strip \
  run.py

echo "==> Preparing /usr/local/opt/darp ..."
sudo rm -rf /usr/local/opt/darp 2>/dev/null || true
sudo mkdir -p /usr/local/opt/darp

echo "==> Copying darp bundle into /usr/local/opt/darp ..."
sudo cp -R dist/darp/* /usr/local/opt/darp/

echo "==> Copying nginx.conf into /usr/local/opt/darp ..."
sudo cp "$SCRIPT_DIR/nginx.conf" /usr/local/opt/darp/nginx.conf

echo "==> Removing old /usr/local/bin/darp ..."
sudo rm -f /usr/local/bin/darp 2>/dev/null || true

echo "==> Creating symlink /usr/local/bin/darp -> /usr/local/opt/darp/darp ..."
sudo ln -s /usr/local/opt/darp/darp /usr/local/bin/darp

echo
echo "==========================================="
echo "  Darp installed fast version successfully!"
echo "  Location: /usr/local/opt/darp"
echo "  Global CLI: /usr/local/bin/darp"
echo "==========================================="
echo
echo "Try:  darp --help"
