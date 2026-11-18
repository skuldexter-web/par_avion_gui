#!/usr/bin/env bash
#
# install.sh — PAR AVION GUI installer for Kali Linux and Raspberry Pi OS.
#
# What it does:
#   - detects Debian/Kali vs Raspberry Pi OS (both Debian-based, but
#     package availability and architecture differ)
#   - installs the system packages the GUI and the SDR tooling need
#   - builds the ADS-B decoder from the source vendored in
#     third_party/dump1090, so Airplanes mode does not depend on the
#     distribution packaging one (Kali, for instance, does not)
#   - creates a Python virtual environment in ./venv — note no leading
#     dot: a hidden .venv caused permission and visibility trouble on
#     Kali, and a plain `pip install` is blocked by PEP 668 anyway
#   - installs the Python dependencies into that venv
#   - caches current ISS orbital elements, so the ISS tracker is
#     accurate offline afterwards
#   - optionally creates a desktop menu entry
#
# Usage:
#   chmod +x install.sh
#   ./install.sh                  full install
#   ./install.sh --dump1090-only  just (re)build the bundled ADS-B decoder
#   ./install.sh --no-launcher    skip the desktop entry prompt
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
DUMP1090_DIR="$SCRIPT_DIR/third_party/dump1090"

DUMP1090_ONLY=false
SKIP_LAUNCHER=false
for arg in "$@"; do
    case "$arg" in
        --dump1090-only) DUMP1090_ONLY=true ;;
        --no-launcher)   SKIP_LAUNCHER=true ;;
        -h|--help)       sed -n '2,25p' "$0"; exit 0 ;;
    esac
done

echo "=============================================="
echo "  PAR AVION GUI — Installation"
echo "=============================================="

# ---------------------------------------------------------------------
# Build the bundled ADS-B decoder
# ---------------------------------------------------------------------
build_dump1090() {
    echo ""
    echo "Building the bundled dump1090 (third_party/dump1090)..."
    if [[ ! -f "$DUMP1090_DIR/dump1090.c" ]]; then
        echo "  ! Source missing from $DUMP1090_DIR — skipping."
        return 1
    fi
    if make -C "$DUMP1090_DIR" >/dev/null 2>&1 && [[ -x "$DUMP1090_DIR/dump1090" ]]; then
        echo "  Built $DUMP1090_DIR/dump1090"
        echo "  PAR AVION uses this copy first, ahead of anything on PATH."
        # Let the invoking user run and rebuild it.
        chown "$REAL_USER":"$REAL_USER" "$DUMP1090_DIR"/* 2>/dev/null || true
        return 0
    fi
    echo "  ! Build failed. It needs librtlsdr-dev and pkg-config:"
    echo "      sudo apt-get install -y librtlsdr-dev pkg-config build-essential"
    echo "    then: ./install.sh --dump1090-only"
    return 1
}

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root for system package installs."
    echo "Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"

if $DUMP1090_ONLY; then
    build_dump1090 || exit 1
    exit 0
fi

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

# ---------------------------------------------------------------------
# 1-2. System packages
# ---------------------------------------------------------------------
echo "[1/6] Updating package lists..."
apt-get update -qq

echo ""
echo "[2/6] Installing system dependencies..."
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
    gpsd \
    gpsd-clients \
    sox \
    libsox-fmt-all \
    alsa-utils \
    multimon-ng \
    || true

# HackRF support is optional; its absence must not fail the install.
apt-get install -y hackrf libhackrf-dev >/dev/null 2>&1 || \
    echo "  (hackrf packages unavailable — RTL-SDR support is unaffected)"

# ---------------------------------------------------------------------
# 3. ADS-B + AIS decoders
# ---------------------------------------------------------------------
echo ""
echo "[3/6] ADS-B and AIS decoders..."
build_dump1090 || true

if ! command -v rtl_ais >/dev/null 2>&1; then
    echo "  rtl_ais not found — trying to build it from source (needs network)..."
    TMP_DIR="$(mktemp -d)"
    git clone --depth 1 https://github.com/dgiardini/rtl-ais.git "$TMP_DIR/rtl-ais" 2>/dev/null || true
    if [[ -d "$TMP_DIR/rtl-ais" ]]; then
        make -C "$TMP_DIR/rtl-ais" >/dev/null 2>&1 || true
        if [[ -f "$TMP_DIR/rtl-ais/rtl_ais" ]]; then
            install -m 0755 "$TMP_DIR/rtl-ais/rtl_ais" /usr/local/bin/rtl_ais
            echo "  Installed /usr/local/bin/rtl_ais"
        else
            echo "  ! rtl_ais build failed. Maritime mode still listens on UDP 10110,"
            echo "    so an AIS feed from elsewhere on the network works regardless."
        fi
    else
        echo "  ! Could not fetch rtl-ais (offline?). Maritime mode still listens"
        echo "    on UDP 10110 for an external AIS feed."
    fi
    rm -rf "$TMP_DIR"
else
    echo "  rtl_ais already installed."
fi

# ---------------------------------------------------------------------
# USB permissions and driver conflicts
# ---------------------------------------------------------------------
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
        echo "  ! Could not unload dvb_usb_rtl28xxu (in use). Reboot to apply the blacklist."
fi

systemctl enable gpsd.socket >/dev/null 2>&1 || true
systemctl restart gpsd.socket >/dev/null 2>&1 || true

# ---------------------------------------------------------------------
# 4. Python virtual environment (PEP 668)
# ---------------------------------------------------------------------
echo ""
echo "[4/6] Setting up the Python virtual environment..."

# A repo cloned or copied as root leaves SCRIPT_DIR root-owned, which is
# exactly what produces "Permission denied" when the venv is then created
# as an unprivileged user.
if [[ "$(stat -c '%U' "$SCRIPT_DIR")" != "$REAL_USER" ]]; then
    echo "  Fixing ownership of $SCRIPT_DIR (not currently owned by $REAL_USER)..."
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
    echo "  apt-installed python3-tk, which pip cannot provide)."
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
# 5. Orbital elements
# ---------------------------------------------------------------------
echo ""
echo "[5/6] Caching current ISS orbital elements..."
if sudo -u "$REAL_USER" bash -c "
        source '$VENV_DIR/bin/activate'
        python3 '$SCRIPT_DIR/tools/update_tle.py'
    "; then
    echo "  Elements cached — ISS tracking is now accurate offline."
else
    echo "  Could not fetch elements (offline?). The bundled set in"
    echo "  data/tle/iss.tle is used instead, and you can refresh any time"
    echo "  from the ISS tab or by pasting elements into it by hand."
fi

# ---------------------------------------------------------------------
# 6. Desktop launcher (optional)
# ---------------------------------------------------------------------
echo ""
echo "[6/6] Desktop launcher"
create_launcher="n"
if ! $SKIP_LAUNCHER; then
    read -r -p "Create a desktop application menu entry for PAR AVION GUI? [Y/n] " create_launcher
    create_launcher="${create_launcher:-Y}"
fi

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
    chown "$REAL_USER":"$REAL_USER" "$LAUNCHER_SCRIPT" 2>/dev/null || true

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
    chown "$REAL_USER":"$REAL_USER" "$DESKTOP_FILE_SRC" 2>/dev/null || true

    DESKTOP_TARGET_DIR="$REAL_HOME/.local/share/applications"
    sudo -u "$REAL_USER" mkdir -p "$DESKTOP_TARGET_DIR"
    sudo -u "$REAL_USER" cp "$DESKTOP_FILE_SRC" "$DESKTOP_TARGET_DIR/par-avion-gui.desktop"
    echo "  Desktop launcher installed to $DESKTOP_TARGET_DIR/par-avion-gui.desktop"
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
echo "First run: open Settings and set your station position. Range,"
echo "bearing and ISS pass times are all measured from it, and without"
echo "it (and without a GPS fix) the tracking modes will say so rather"
echo "than plot anything at a guessed location."
echo ""
echo "Check everything works, no radio needed:"
echo "  source venv/bin/activate"
echo "  python3 tools/sstv_selftest.py     # SSTV decoder, all 7 modes"
echo "  python3 tools/iss_selftest.py      # ISS tracking, fully offline"
echo ""
echo "SDR hardware notes:"
echo "  1. Unplug and replug your SDR/GPS USB devices so udev rules apply."
echo "  2. If dvb_usb_rtl28xxu was in use, reboot to release the device."
echo "  3. Log out and back in (or run 'newgrp plugdev') for group access."
echo ""
