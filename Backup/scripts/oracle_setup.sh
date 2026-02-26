#!/bin/bash
# ============================================================
# Oracle Cloud Free Tier - Instance Setup Script
# Run this on a fresh Ubuntu 22.04 Ampere A1 instance
# ============================================================
#
# Prerequisites:
# 1. Create an Oracle Cloud Free Tier account
# 2. Launch an Ampere A1 Compute instance (4 OCPU, 24GB RAM - always free)
# 3. Choose Ubuntu 22.04 as the OS
# 4. SSH into the instance
# 5. Upload this project (git clone or scp)
# 6. Run: bash scripts/oracle_setup.sh
#
# ============================================================

set -e

echo "=== Oracle Cloud Setup for Trending Content Shorts Engine ==="

# Update system
echo "[1/6] Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
echo "[2/6] Installing Python..."
sudo apt install -y python3 python3-pip python3-venv

# Install FFmpeg
echo "[3/6] Installing FFmpeg..."
sudo apt install -y ffmpeg

# Verify versions
echo "[4/6] Verifying installations..."
python3 --version
ffmpeg -version | head -1
pip3 --version

# Set up project
echo "[5/6] Setting up Python environment..."
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Initialize database
python main.py --setup

# Set up cron jobs
echo "[6/6] Setting up cron schedule..."
CRON_FILE="/tmp/yttt_cron"
PROJECT_DIR="$(pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

cat > "$CRON_FILE" << EOF
# Trending Content Shorts Engine - Automated Pipeline
# Runs daily at 6:00 AM UTC

# Full pipeline run (discover -> ideate -> source -> compose)
0 6 * * * cd $PROJECT_DIR && $VENV_PYTHON main.py --run >> $PROJECT_DIR/logs/cron.log 2>&1

# Upload check (uploads any newly approved videos) - runs every 2 hours
0 */2 * * * cd $PROJECT_DIR && $VENV_PYTHON main.py --upload >> $PROJECT_DIR/logs/cron_upload.log 2>&1
EOF

crontab "$CRON_FILE"
rm "$CRON_FILE"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy your config/settings.yaml to this machine"
echo "  2. Copy your config/client_secret.json for YouTube OAuth"
echo "  3. Run: python main.py --setup  (to verify config)"
echo "  4. Run: python main.py --run    (to test the pipeline)"
echo ""
echo "Cron jobs installed:"
crontab -l
echo ""
echo "To view cron logs:  tail -f logs/cron.log"
echo "To edit schedule:   crontab -e"
echo ""
