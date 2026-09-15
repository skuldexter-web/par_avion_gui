# PAR AVION GUI

**A modern, mouse-driven graphical interface for the PAR AVION Tactical RF, ADS-B, Maritime AIS, Satellite & Signal Decoding Suite.**

PAR AVION GUI wraps the original terminal-based PAR AVION toolkit in a responsive [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) desktop application: a sidebar navigation rail, live system telemetry, an embedded color-coded log console, and a fully native, embedded widget for every single mode — a live rotating-sweep radar canvas for Airplanes and Maritime, a real-time spectrum + scrolling waterfall for Waterfalls, native audio controls for Radio, a live image canvas for SSTV, a live decoded-text stream for Morse, and pass-prediction readouts for the ISS Tracker.

**No mode ever opens an external terminal window.** Every decoder, tracker, and hardware controller runs as a background thread inside the GUI process itself, streaming its output directly onto on-screen canvases and text widgets.

The application is **strictly dark mode** — a black/dark-slate tactical console aesthetic with neon green, cyan, and blue accents. There is no light mode.

Every mode is **receive-only**. ADS-B, AIS, satellite TLE data, and amateur SSTV/CW transmissions on their conventional calling frequencies are all publicly broadcast or published information — the same sources behind sites like FlightRadar24, MarineTraffic, and N2YO. Nothing in this application transmits on any RF interface.

![screenshot placeholder](assets/screenshot-dashboard.png)
*(Screenshot: Dashboard view — hardware status and quick launch)*

---

## Features

- **Sidebar navigation rail** — one-click access to every mode and live CPU/RAM telemetry
- **Embedded live log console** — color-coded success/warning/error/info output, streamed in real time from background worker threads, with Clear and Copy buttons
- **Fully asynchronous backend** — every hardware scan, decoder, or subprocess call runs on a background thread; the GUI never freezes
- **Every mode is a native embedded widget, not a terminal launch:**
  - **Airplanes** — a live rotating-sweep circular radar canvas (concentric range rings, compass markers, plotted aircraft blips) plus an embedded "LIVE AIR TRAFFIC" data table (Callsign, Alt, Speed, Dist, Track), driving `dump1090` directly
  - **Maritime** — the same radar canvas in AIS mode, with a "LIVE MARITIME TRAFFIC" table (Name, SOG, COG, Dist), driving `rtl_ais` directly
  - **Waterfalls** — a live FFT spectrum line plot and a real-time scrolling color-gradient waterfall/spectrogram, both embedded Tkinter canvases
  - **Radio** — frequency entry, band toggle, presets, volume slider, Play/Stop, driving `rtl_fm -> sox play` directly
  - **SSTV** — a live-updating image preview canvas as the picture decodes line-by-line, Start/Stop/Save/Reset
  - **Morse/CW** — a live decoded-text stream with adaptive WPM estimate
  - **ISS Tracker** — sub-satellite position and next-pass prediction from entered coordinates
  - **Dashboard** — hardware status cards (SDR/GPS/dump1090) and a quick-open grid for every mode
- **Cross-platform installer** — detects Kali Linux vs. Raspberry Pi OS, installs system + Python dependencies into an isolated virtual environment (`venv/`), and can register a desktop application menu entry

---

## Prerequisites

- **OS:** Kali Linux (Debian x86_64/ARM) or Raspberry Pi OS / Raspbian (ARM32/ARM64)
- **Python:** 3.9 or newer
- **Hardware (optional but recommended):** an RTL-SDR dongle (v3/v4, Nooelec, etc.) for live Airplanes/Waterfalls/Radio/Maritime/SSTV/Morse data, and a USB GPS dongle for auto-centered radar views
- **Display:** any X11 or Wayland desktop session (a physical monitor, VNC, or similar) — this is a GUI application, not a headless tool

---

## Installation

```bash
git clone https://github.com/skuldexter-web/par-avion-gui.git
cd par-avion-gui
chmod +x install.sh
./install.sh
```

`install.sh` will:

1. Detect whether you're on Kali Linux or Raspberry Pi OS
2. Install system packages via `apt`: `python3-tk`, `python3-venv`, and the same SDR/audio toolchain as the CLI backend (`rtl-sdr`, `sox`, `gpsd`, `dump1090`, `rtl_ais`, `multimon-ng`, etc.)
3. Create a dedicated virtual environment at `venv/` (required on modern Debian-based systems due to PEP 668) — the installer also fixes directory ownership automatically if it detects the project folder isn't owned by your user, which is the most common cause of a `Permission denied` error at this step
4. Install all Python dependencies from `requirements.txt` into that environment
5. Optionally create a desktop launcher so **PAR AVION GUI** appears in your application menu

After installation, unplug and replug any SDR/GPS USB devices so the new udev rules take effect, and log out/in once to pick up the `plugdev` group membership.

---

## Usage

**Via the desktop launcher** (if created during install): find "PAR AVION GUI" in your application menu.

**From the command line:**

```bash
cd par-avion-gui
source venv/bin/activate
python3 main.py
```

### Navigating the app

- Use the **sidebar** on the left to switch between Dashboard, Airplanes, Waterfalls, Radio, Maritime, ISS Tracker, SSTV, Morse/CW, and Settings.
- The **live log console** at the bottom of the window shows real-time status, warnings, and errors — colored by severity.
- Every mode runs entirely inside the GUI. Click **Start Tracking** / **Start** / **Play** / **Start Listening** in a mode's own panel to begin — nothing opens in a separate window.
- The **Dashboard**'s quick-open cards jump straight to any mode's tab.

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
│   ├── par_avion.py            Original curses entry point (kept for reference/
│   │                          standalone CLI use; the GUI does not launch it)
│   └── modules/                 All backend modules (airplanes, maritime, sstv,
│                                morse, iss, radio, waterfall, hardware, radar_ui)
└── gui/                       GUI application code
    ├── app.py                  Main window: sidebar + view switching + console
    ├── themes/
    │   └── theme.py              Fixed dark tactical color palette
    ├── components/
    │   ├── sidebar.py            Navigation rail
    │   ├── console.py            Embedded colored log/output panel
    │   ├── telemetry.py          Live CPU/RAM widget
    │   ├── radar_canvas.py       Reusable rotating-sweep radar (Airplanes/Maritime)
    │   └── waterfall_canvas.py    Reusable spectrum + scrolling waterfall (Waterfalls)
    └── views/                  One file per mode — every mode is a fully
        ├── dashboard.py           embedded native widget; none spawn an
        ├── airplanes_view.py       external terminal or subprocess window.
        ├── waterfalls_view.py
        ├── radio_view.py
        ├── maritime_view.py
        ├── iss_view.py
        ├── sstv_view.py
        ├── morse_view.py
        ├── settings_view.py
        └── base_view.py            Shared background-thread helper
```

---

## Known Limitations

- **Morse decoding** can misread the very first character of a transmission when the sender's speed is far from the initial WPM estimate; this is a backend characteristic inherited unchanged from the CLI.
- **`core/par_avion.py`** (the original curses interface) is kept in the repository for reference and standalone terminal use, but the GUI never invokes it — every mode's live display is now a native Tkinter widget driven directly from `core/modules/`.

---

## License

No license specified — add one appropriate for your intended distribution before publishing.
