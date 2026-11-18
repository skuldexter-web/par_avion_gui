# PAR AVION

**Tactical RF & Telemetry Suite — a single GUI for ADS-B, AIS, SSTV, CW,
spectrum monitoring and satellite tracking, built around an RTL-SDR.**

Everything runs inside one window. No terminal windows pop up, no mode
shells out to a text UI, and nothing needs an API key, an account or a
cloud service. Plug in a dongle and point it at the sky.

Targets Kali Linux (x86-64 / ARM) and Raspberry Pi OS (ARM32 / ARM64).

---

## Features

| Mode | What it does |
| --- | --- |
| **Dashboard** | Hardware status at a glance — SDR, GPS, ADS-B feed — and a jump-off point for every mode. |
| **Airplanes** | ADS-B on 1090 MHz: rotating radar scope, live traffic table (callsign, altitude, speed, track, range, bearing) and an offline map. |
| **Waterfalls** | Real-time spectrum analyser and scrolling waterfall with a proper VFO: drag knob, digit-click tuning, selectable step from 100 Hz to 1 MHz. |
| **Radio** | Broadcast AM/FM tuner driving `rtl_fm` into the soundcard. |
| **Maritime** | AIS vessel tracking: scope, live table and the same offline map. |
| **ISS Tracker** | Live sub-satellite position, ground track, footprint and next passes — including whether each pass is visible to the naked eye. |
| **SSTV** | Slow-scan TV decoder: Martin 1/2, Scottie 1/2/DX, Robot 36/72, with a live line-by-line preview. |
| **Morse / CW** | CW decoder with adaptive speed tracking. |
| **Settings** | Station position, GPS and network preferences, bundled-data status, hardware scan. |

Every mode is **receive-only**. ADS-B, AIS, SSTV and CW are decoded from
transmissions already being broadcast in the clear; orbital elements are
published data. Nothing here keys a transmitter.

---

## Stand-alone by design

No part of PAR AVION requires an API key, an account, or a server to
call. Specifically:

* **The ADS-B decoder is bundled.** dump1090's source ships in
  `third_party/dump1090` (BSD-3-Clause, unmodified) and `install.sh`
  builds it. There is no dependency on the distribution packaging one —
  which matters on Kali, where `dump1090-mutability` has no installable
  candidate at all.
* **The map is bundled vector data**, not tiles: Natural Earth
  coastlines, borders, lakes and land (public domain) in `data/map/`,
  about 3.8 MB for three levels of detail. No tile server, no key, and
  it renders fast enough for a Pi.
* **Satellite tracking is local.** SGP4 propagation plus coordinate
  transforms computed in-process; no ephemeris download, no tracking
  API. A current element set is bundled in `data/tle/`, `install.sh`
  caches a fresh one, and you can paste elements by hand for a machine
  that never touches a network.

The one optional network use is refreshing orbital elements, from
CelesTrak / ARISS / AMSAT — all keyless, and switchable off in Settings.

---

## Install

```bash
git clone https://github.com/skuldexter-web/par_avion_gui.git
cd par_avion_gui
chmod +x install.sh
./install.sh
```

The installer detects Kali vs Raspberry Pi OS, installs system packages,
builds the bundled dump1090, creates a Python virtual environment in
`./venv` (no leading dot — a hidden `.venv` caused permission trouble on
Kali, and PEP 668 blocks a plain `pip install` anyway), installs the
Python dependencies, caches current orbital elements, and optionally
adds a desktop menu entry.

### Run

```bash
source venv/bin/activate
python3 main.py
```

Or launch **PAR AVION GUI** from the application menu.

### Other install options

```bash
./install.sh --dump1090-only   # just (re)build the bundled ADS-B decoder
./install.sh --no-launcher     # skip the desktop entry prompt
```

---

## First run

Open **Settings** and set your **station position**.

Range and bearing on the radar, the centre of the map and every ISS pass
time are measured from it. With a GPS dongle it is taken from the fix
automatically; without one, enter it by hand. Until it is set, the
tracking modes say so plainly — they will not plot anything at a guessed
position.

---

## Checking it works, without a radio

Both self-tests run against synthesised signals and bundled data, so
they need no hardware and no network:

```bash
source venv/bin/activate
python3 tools/sstv_selftest.py          # all 7 SSTV modes, clean signal
python3 tools/sstv_selftest.py --noisy  # ... and with noise + offset
python3 tools/iss_selftest.py           # ISS tracking, fully offline
python3 tools/update_tle.py --status    # which orbital elements are in use
```

`sstv_selftest.py` renders a test image to SSTV audio, decodes it back
through the real decoder and reports the per-pixel error against the
original. `iss_selftest.py` checks the propagator against physical
reality — orbital altitude, speed, period, pass geometry — with
networking disabled.

---

## Hardware

* **RTL-SDR** (RTL2832U — v3, v4, Nooelec and similar). Required for
  anything over the air.
* **GPS receiver** (optional) via `gpsd` or a serial dongle. Without
  one, set the station position by hand in Settings.
* **HackRF** is detected if present, though the receive paths are
  written against the RTL-SDR.

Frequencies worth knowing: ADS-B 1090 MHz · AIS 161.975 / 162.025 MHz ·
SSTV 14.230 MHz (HF) and 145.800 MHz (ISS) · 2 m and 70 cm amateur bands.

---

## Project layout

```
par-avion-gui/
├── main.py                 Entry point — launches the GUI
├── install.sh              Automated installer
├── requirements.txt        Python dependencies
├── assets/                 Icon and generated desktop launcher
├── core/                   Back end, usable headless
│   ├── par_avion.py        Curses CLI (kept working alongside the GUI)
│   └── modules/
│       ├── airplanes.py    ADS-B decode, dump1090 supervision
│       ├── maritime.py     AIS decode (AIVDM)
│       ├── sstv.py         SSTV decoder + encoder
│       ├── morse.py        CW decoder
│       ├── waterfall.py    Spectrum acquisition
│       ├── radio.py        Broadcast AM/FM tuner
│       ├── iss.py          SGP4 propagation, passes, ground track
│       ├── mapdata.py      Offline vector map loader
│       ├── hardware.py     SDR / GPS / feed detection
│       ├── radar_ui.py     Curses radar primitives
│       └── appconfig.py    Station position and preferences
├── gui/
│   ├── app.py              Window shell, sidebar, view switching
│   ├── components/         Radar, map, spectrum, waterfall, VFO, console
│   ├── views/              One per mode
│   └── themes/             Fixed dark palette
├── data/
│   ├── map/                Natural Earth vector layers (public domain)
│   └── tle/                Bundled ISS orbital elements
├── third_party/
│   └── dump1090/           Vendored ADS-B decoder source (BSD-3-Clause)
└── tools/                  Self-tests and data builders
```

---

## Notes and limitations

* **Appearance is dark only**, deliberately. There is no light mode and
  no theme toggle.
* **SSTV decode quality follows the signal.** Against a clean signal the
  decoder is within ~1/255 per channel on every mode; at about 24 dB SNR
  it is within 2–6 % depending on mode. Martin 2 and Scottie 2 are
  noisier than the rest because their pixels are shortest, which leaves
  the least to average over.
* **CW decoding** is reliable at roughly 10–25 WPM. Well above that, the
  first character of a transmission can be misread while the speed
  estimator calibrates; it shows as `?` rather than a wrong letter.
* **Orbital elements age.** Pass times drift by roughly a minute after a
  couple of weeks on stale elements. The ISS tab always shows the age
  and says what that means — refresh when you can.
* **Map detail** is coastline, border and lake level, not streets.
  Street-level offline coverage would mean gigabytes of tiles; this is
  a few megabytes and tells you where things are relative to the coast
  and to you.
* **Raspberry Pi**: a Pi 4 or 5 handles everything comfortably. On a
  Pi 3 the SSTV and CW decoders are the heavy paths; reduce spectrum
  averaging in the Waterfalls tab if the display feels sluggish.

---

## Credits and licences

* **dump1090** — Salvatore Sanfilippo (antirez), BSD-3-Clause. Vendored
  unmodified in `third_party/dump1090`; see the licence there.
* **Natural Earth** — public domain map data, naturalearthdata.com.
* **Orbital elements** — published by US Space Force, distributed via
  CelesTrak, ARISS and AMSAT.
* SSTV timing follows JL Barber (N7CXI), *Proposal for SSTV Mode
  Specifications* (Dayton, 2000).
