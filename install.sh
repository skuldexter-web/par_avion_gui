#!/usr/bin/env bash
#
# install.sh — PAR AVION GUI installer for Kali Linux and Raspberry Pi OS.
#
# - Detects Debian/Kali vs. Raspberry Pi OS (both are Debian-based, but
#   package availability and architecture differ)
# - Installs system packages needed for the GUI (python3-tk) and for the
#   backend SDR/audio tools (rtl-sdr, sox, etc. — same as the CLI's own
#   install.sh)
# - Creates a dedicated Python virtual environment (venv), required on
#   modern Debian/Kali/Raspberry Pi OS due to PEP 668 (externally
#   managed environment) blocking a plain `pip install`
# - Installs Python dependencies into that venv
# - Optionally creates a desktop launcher (.desktop entry)
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo "=============================================="
echo "  PAR AVION GUI — Installation"
echo "=============================================="

# ---------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------
OS_NAME="unknown"
IS_RASPBERRY_PI=false

if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_NAME="${PRETTY_NAME:-$ID}"
fi

if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    IS_RASPBERRY_PI=true
elif [[ -f /etc/rpi-issue ]]; then
    IS_RASPBERRY_PI=true
fi

ARCH="$(uname -m)"

echo ""
echo "Detected OS:   $OS_NAME"
echo "Architecture:  $ARCH"
if $IS_RASPBERRY_PI; then
    echo "Platform:      Raspberry Pi (Raspberry Pi OS / Raspbian)"
else
    echo "Platform:      Generic Debian-based (Kali Linux or similar)"
fi
echo ""

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root for system package installs."
    echo "Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"

# ---------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------
echo "[1/5] Updating package lists..."
apt-get update -qq

echo ""
echo "[2/5] Installing system dependencies..."
apt-get install -y \
    python3-tk \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    git \
    pkg-config \
    libusb-1.0-0-dev \
    rtl-sdr \
    librtlsdr-dev \
    hackrf \
    libhackrf-dev \
    gpsd \
    gpsd-clients \
    sox \
    libsox-fmt-all \
    alsa-utils \
    pulseaudio-utils \
    multimon-ng \
    || true

_has_candidate() {
    apt-cache policy "$1" 2>/dev/null | grep -q "Candidate: (none)" && return 1
    apt-cache policy "$1" 2>/dev/null | grep -q "Candidate:" && return 0
    return 1
}

echo ""
echo "[3/5] Installing dump1090 (ADS-B decoder)..."
if _has_candidate dump1090-mutability && apt-get install -y dump1090-mutability; then
    :
elif _has_candidate dump1090-fa && apt-get install -y dump1090-fa; then
    :
else
    echo "  No packaged dump1090 available — building from source..."
    TMP_DIR="$(mktemp -d)"
    git clone --depth 1 https://github.com/flightaware/dump1090.git "$TMP_DIR/dump1090" || true
    if [[ -d "$TMP_DIR/dump1090" ]]; then
        make -C "$TMP_DIR/dump1090" || echo "  ! dump1090 build failed; install manually later if needed."
        [[ -f "$TMP_DIR/dump1090/dump1090" ]] && install -m 0755 "$TMP_DIR/dump1090/dump1090" /usr/local/bin/dump1090
    fi
    rm -rf "$TMP_DIR"
fi

if ! command -v rtl_ais >/dev/null 2>&1; then
    echo "  rtl_ais not found — building from source..."
    TMP_DIR="$(mktemp -d)"
    git clone --depth 1 https://github.com/dgiardini/rtl-ais.git "$TMP_DIR/rtl-ais" 2>/dev/null || \
        git clone --depth 1 https://github.com/Guenael/rtl-ais.git "$TMP_DIR/rtl-ais" 2>/dev/null || true
    if [[ -d "$TMP_DIR/rtl-ais" ]]; then
        make -C "$TMP_DIR/rtl-ais" || echo "  ! rtl_ais build failed; Maritime mode will report 'no feed' until installed manually."
        [[ -f "$TMP_DIR/rtl-ais/rtl_ais" ]] && install -m 0755 "$TMP_DIR/rtl-ais/rtl_ais" /usr/local/bin/rtl_ais
    fi
    rm -rf "$TMP_DIR"
fi

UDEV_RULES_FILE="/etc/udev/rules.d/20-par-avion-sdr.rules"
cat > "$UDEV_RULES_FILE" <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6089", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="6089", GROUP="plugdev", MODE="0666"
EOF
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
if getent group plugdev >/dev/null 2>&1; then
    usermod -aG plugdev "$REAL_USER" || true
fi

BLACKLIST_FILE="/etc/modprobe.d/20-par-avion-blacklist-rtl.conf"
cat > "$BLACKLIST_FILE" <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
if lsmod | grep -q dvb_usb_rtl28xxu; then
    modprobe -r dvb_usb_rtl28xxu 2>/dev/null || \
        echo "  ! Could not hot-unload dvb_usb_rtl28xxu (in use). Reboot to fully apply blacklist."
fi

systemctl enable gpsd.socket >/dev/null 2>&1 || true
systemctl restart gpsd.socket >/dev/null 2>&1 || true

# ---------------------------------------------------------------------
# 4. Python virtual environment (PEP 668 compliance)
# ---------------------------------------------------------------------
echo ""
echo "[4/5] Setting up Python virtual environment..."

# Make sure REAL_USER actually owns the project directory before trying
# to create a venv as that user — a repo cloned or copied while running
# as root (common when install.sh itself was invoked via sudo from the
# start) leaves SCRIPT_DIR root-owned, which is exactly what produces a
# "Permission denied" error when venv creation is later attempted as an
# unprivileged user.
if [[ "$(stat -c '%U' "$SCRIPT_DIR")" != "$REAL_USER" ]]; then
    echo "  Fixing ownership of $SCRIPT_DIR (currently not owned by $REAL_USER)..."
    chown -R "$REAL_USER":"$REAL_USER" "$SCRIPT_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    if ! sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR" --system-site-packages; then
        echo ""
        echo "  ERROR: could not create the virtual environment at $VENV_DIR"
        echo "  This is usually a permissions problem. Try:"
        echo "    sudo chown -R $REAL_USER:$REAL_USER '$SCRIPT_DIR'"
        echo "  then re-run ./install.sh"
        exit 1
    fi
    echo "  Created $VENV_DIR (--system-site-packages so it can see the"
    echo "  apt-installed python3-tk, which pip cannot install on its own)."
else
    echo "  $VENV_DIR already exists — reusing it."
fi

sudo -u "$REAL_USER" bash -c "
    source '$VENV_DIR/bin/activate'
    pip install --upgrade pip -q
    pip install -r '$SCRIPT_DIR/requirements.txt' -q
"
echo "  Python dependencies installed into $VENV_DIR"

# ---------------------------------------------------------------------
# 5. Desktop launcher (optional)
# ---------------------------------------------------------------------
echo ""
echo "[5/5] Desktop launcher"
read -r -p "Create a desktop application menu entry for PAR AVION GUI? [Y/n] " create_launcher
create_launcher="${create_launcher:-Y}"

if [[ "$create_launcher" =~ ^[Yy] ]]; then
    ICON_PATH="$SCRIPT_DIR/assets/icon.png"
    LAUNCHER_SCRIPT="$SCRIPT_DIR/assets/par-avion-gui.sh"

    cat > "$LAUNCHER_SCRIPT" <<EOF
#!/usr/bin/env bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
exec python3 "$SCRIPT_DIR/main.py"
EOF
    chmod +x "$LAUNCHER_SCRIPT"

    DESKTOP_FILE_SRC="$SCRIPT_DIR/assets/par-avion-gui.desktop"
    cat > "$DESKTOP_FILE_SRC" <<EOF
[Desktop Entry]
Type=Application
Name=PAR AVION GUI
Comment=Tactical RF, ADS-B, AIS, Satellite & Signal Decoding Suite
Exec=$LAUNCHER_SCRIPT
Icon=$ICON_PATH
Terminal=false
Categories=Utility;HamRadio;Science;
EOF
    chmod +x "$DESKTOP_FILE_SRC"

    DESKTOP_TARGET_DIR="$REAL_HOME/.local/share/applications"
    sudo -u "$REAL_USER" mkdir -p "$DESKTOP_TARGET_DIR"
    sudo -u "$REAL_USER" cp "$DESKTOP_FILE_SRC" "$DESKTOP_TARGET_DIR/par-avion-gui.desktop"
    echo "  Desktop launcher installed to $DESKTOP_TARGET_DIR/par-avion-gui.desktop"
    echo "  (Look for 'PAR AVION GUI' in your application menu.)"
else
    echo "  Skipped desktop launcher."
fi

echo ""
echo "=============================================="
echo "  Installation complete."
echo "=============================================="
echo ""
echo "To launch PAR AVION GUI:"
echo "  cd $SCRIPT_DIR"
echo "  source venv/bin/activate"
echo "  python3 main.py"
echo ""
echo "Or use the desktop launcher created above, if you chose that option."
echo ""
echo "Next steps for SDR hardware:"
echo "  1. Unplug and replug your SDR/GPS USB devices so udev rules apply."
echo "  2. If dvb_usb_rtl28xxu was in use, reboot to fully release the device."
echo "  3. Log out/in (or run 'newgrp plugdev') to pick up the plugdev group."
echo ""
