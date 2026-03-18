#!/usr/bin/env bash
# scripts/setup_kiosk.sh
# Configures Raspberry Pi 5 to boot into a Chromium kiosk showing the SoterCare dashboard.
# Run once as the desktop user (not root):  bash scripts/setup_kiosk.sh

set -e
DASHBOARD_URL="http://localhost:5173"
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

echo "[kiosk] Creating autostart entry for Chromium kiosk (X11)..."
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/sotercare-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SoterCare Kiosk
Exec=bash -c "sleep 10 && chromium-browser --kiosk --disable-restore-session-state --disable-infobars --noerrdialogs --disable-session-crashed-bubble --autoplay-policy=no-user-gesture-required $DASHBOARD_URL"
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "[kiosk] Configuring Labwc (Wayland) autostart..."
LABWC_AUTOSTART_DIR="$HOME/.config/labwc"
mkdir -p "$LABWC_AUTOSTART_DIR"
cat > "$LABWC_AUTOSTART_DIR/autostart" <<EOF
# Labwc autostart for SoterCare Kiosk
unclutter -idle 0.1 -root &
sleep 10 && chromium-browser --kiosk --disable-restore-session-state --disable-infobars --noerrdialogs --disable-session-crashed-bubble --autoplay-policy=no-user-gesture-required "$DASHBOARD_URL" &
EOF

echo "[kiosk] Creating autostart entry for unclutter (X11)..."
cat > "$AUTOSTART_DIR/unclutter.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Unclutter
Exec=unclutter -idle 0.1 -root
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo "[kiosk] Setting up PM2 startup..."
# Ensure PM2 is running the current ecosystem and save it
pm2 start ecosystem.config.js || pm2 restart ecosystem.config.js
pm2 save

# Generate PM2 startup script and execute it (requires sudo)
# Note: This usually outputs a command that needs to be run.
# We'll try to automate the standard Raspberry Pi / Linux case.
if ! pm2 startup | grep -q "sudo env PATH"; then
    echo "[kiosk] PM2 startup needs manual intervention. Please run the command PM2 gave you."
else
    STARTUP_CMD=$(pm2 startup | grep "sudo env PATH" | head -n 1)
    eval "$STARTUP_CMD"
    pm2 save
fi

echo "[kiosk] Done. Reboot to apply: sudo reboot"
