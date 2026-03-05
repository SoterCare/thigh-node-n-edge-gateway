#!/usr/bin/env bash
# scripts/setup_kiosk.sh
# Configures Raspberry Pi 5 to boot into a Chromium kiosk showing the SoterCare dashboard.
# Run once as the desktop user (not root):  bash scripts/setup_kiosk.sh

set -e
DASHBOARD_URL="http://localhost:5000"
AUTOSTART_DIR="$HOME/.config/autostart"

echo "[kiosk] Installing unclutter (cursor hider)..."
sudo apt-get install -y unclutter xdotool

echo "[kiosk] Disabling screen blanking..."
mkdir -p "$HOME/.config/lxsession/LXDE-pi"
cat > "$HOME/.config/lxsession/LXDE-pi/autostart" <<EOF
@lxpanel --profile LXDE-pi
@pcmanfm --desktop --profile LXDE-pi
@xset s off
@xset -dpms
@xset s noblank
EOF

echo "[kiosk] Creating autostart entry for Chromium kiosk..."
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/sotercare-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SoterCare Kiosk
Exec=bash -c "sleep 5 && chromium-browser --kiosk --disable-restore-session-state --disable-infobars --noerrdialogs --disable-session-crashed-bubble $DASHBOARD_URL"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "[kiosk] Creating autostart entry for unclutter..."
cat > "$AUTOSTART_DIR/unclutter.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Unclutter
Exec=unclutter -idle 0.1 -root
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "[kiosk] Done. Reboot to apply: sudo reboot"
