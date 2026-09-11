# PAR AVION GUI

**A modern, mouse-driven graphical interface for the PAR AVION Tactical RF, ADS-B, Maritime AIS, Satellite & Signal Decoding Suite.**

PAR AVION GUI wraps the original terminal-based PAR AVION toolkit in a responsive [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) desktop application: a sidebar navigation rail, live system telemetry, an embedded color-coded log console, and native controls for every mode — while the original curses-based radar/scope views remain available as full-detail launchable windows for the modes best served by them.

Every mode is **receive-only**. ADS-B, AIS, satellite TLE data, and amateur SSTV/CW transmissions on their conventional calling frequencies are all publicly broadcast or published information — the same sources behind sites like FlightRadar24, MarineTraffic, and N2YO. Nothing in this application transmits on any RF interface.

![screenshot placeholder](assets/screenshot-dashboard.png)
*(Screenshot: Dashboard view — hardware status and quick launch)*

---

## Features

- **Sidebar navigation rail** — one-click access to every mode, live CPU/RAM telemetry, and an instant light/dark theme toggle (no restart required)
- **Embedded live log console** — color-coded success/warning/error/info output, streamed in real time from background worker threads, with Clear and Copy buttons
- **Fully asynchronous backend** — every hardware scan, decoder, or subprocess call runs on a background thread; the GUI never freezes
- **Native controls where they fit best:**
  - **Radio** — frequency entry, band toggle, presets, volume slider, Play/Stop, driving `rtl_fm -> sox play` directly
  - **SSTV** — live-updating image preview canvas as the picture decodes line-by-line, Start/Stop/Save/Reset
  - **Morse/CW** — live decoded-text stream with adaptive WPM estimate
  - **ISS Tracker** — sub-satellite position and next-pass prediction from entered coordinates
  - **Dashboard** — hardware status cards (SDR/GPS/dump1090) and a quick-launch grid for every mode
- **Launch-to-terminal for radar/scope modes** — Airplanes, Waterfalls, and Maritime open their full tactical radar display in the proven curses interface (in its own terminal window), with Start/Stop controls for the underlying feed (dump1090, rtl_ais) available directly from the GUI panel
- **Cross-platform installer** — detects Kali Linux vs. Raspberry Pi OS, installs system + Python dependencies into an isolated virtual environment, and can register a desktop application menu entry

---

## Prerequisites

- **OS:** Kali Linux (Debian x86_64/ARM) or Raspberry Pi OS / Raspbian (ARM32/ARM64)
- **Python:** 3.9 or newer
- **Hardware (optional but recommended):** an RTL-SDR dongle (v3/v4, Nooelec, etc.) for live Airplanes/Waterfalls/Radio/Maritime/SSTV/Morse data, and a USB GPS dongle for auto-centered radar views
- **Display:** any X11 or Wayland desktop session (a physical monitor, VNC, or similar) — this is a GUI application, not a headless tool

---

## Installation

```bash
git clone <this-repo-url> par-avion-gui
cd par-avion-gui
chmod +x install.sh
./install.sh
```

`install.sh` will:

1. Detect whether you're on Kali Linux or Raspberry Pi OS
2. Install system packages via `apt`: `python3-tk`, `python3-venv`, and the same SDR/audio toolchain as the CLI backend (`rtl-sdr`, `sox`, `gpsd`, `dump1090`, `rtl_ais`, `multimon-ng`, etc.)
3. Create a dedicated virtual environment at `.venv/` (required on modern Debian-based systems due to [PEP 668](https://peps.python.org/pep-0668/))
4. Install all Python dependencies from `requirements.txt` into that environment
5. Optionally create a desktop launcher so **PAR AVION GUI** appears in your application menu

After installation, unplug and replug any SDR/GPS USB devices so the new udev rules take effect, and log out/in once to pick up the `plugdev` group membership.

---

## Usage

**Via the desktop launcher** (if created during install): find "PAR AVION GUI" in your application menu.

**From the command line:**

```bash
cd par-avion-gui
source .venv/bin/activate
python3 main.py
```

### Navigating the app

- Use the **sidebar** on the left to switch between Dashboard, Airplanes, Waterfalls, Radio, Maritime, ISS Tracker, SSTV, Morse/CW, and Settings.
- The **live log console** at the bottom of the window shows real-time status, warnings, and errors from whatever you're doing — colored by severity.
- Toggle **light/dark mode** any time from the sidebar switch, or from the Settings view — it applies instantly.
- Modes with rich, continuously-updating tactical displays (**Airplanes**, **Waterfalls**, **Maritime**) launch their full radar/scope view in a separate terminal window when you click **Launch**; the GUI panel itself gives you feed status and Start/Stop control before and after.
- Modes with simpler, naturally GUI-shaped output (**Radio**, **SSTV**, **Morse/CW**, **ISS Tracker**) run entirely inside the GUI with native widgets.

---

## Project Structure

```
par-avion-gui/
├── main.py                 Universal entry point — launches the GUI
├── install.sh                Automated installer (Kali / Raspberry Pi OS)
├── requirements.txt           Python dependencies
├── .gitignore
├── README.md
├── assets/
│   ├── icon.png               Application icon
│   └── par-avion-gui.desktop  Desktop launcher (generated by install.sh)
├── core/                      Original CLI backend — unmodified decoding/
│   │                          tracking logic (ADS-B, AIS, SSTV, Morse, ISS,
│   │                          spectrum, hardware detection)
│   ├── par_avion.py            Curses entry point (launched for radar/scope modes)
│   └── modules/                 All backend modules (airplanes, maritime, sstv,
│                                morse, iss, radio, waterfall, hardware, radar_ui)
└── gui/                       GUI application code
    ├── app.py                  Main window: sidebar + view switching + console
    ├── themes/
    │   └── theme.py              Light/dark color palette
    ├── components/
    │   ├── sidebar.py            Navigation rail
    │   ├── console.py            Embedded colored log/output panel
    │   └── telemetry.py          Live CPU/RAM widget
    └── views/                  One file per mode
        ├── dashboard.py
        ├── airplanes_view.py
        ├── waterfalls_view.py
        ├── radio_view.py
        ├── maritime_view.py
        ├── iss_view.py
        ├── sstv_view.py
        ├── morse_view.py
        ├── settings_view.py
        ├── launch_view.py          Shared template for terminal-launched modes
        └── base_view.py            Shared async/threading + launch helpers
```

---

## Known Limitations

- **Theme-switch cosmetic redraw:** on some display setups, a small number of labels directly inside scrollable panels may not immediately repaint after toggling light/dark mode, even though their underlying color is correctly updated (confirmed via direct widget inspection). Switching to another tab and back, or resizing the window, forces a clean redraw. This does not affect functionality.
- **Radar/scope modes** (Airplanes, Waterfalls, Maritime) open in an external terminal rather than a native canvas — this preserves the fully-verified curses rendering rather than re-deriving it, at the cost of a second window. A future release may bring these fully in-process.
- **Morse decoding** can misread the very first character of a transmission when the sender's speed is far from the initial WPM estimate; this is a backend characteristic inherited unchanged from the CLI.

---

## License

No license specified — add one appropriate for your intended distribution before publishing.
