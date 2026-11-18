"""
gui/views/settings_view.py — Station setup, data status and diagnostics.

The important control here is the station position. Range and bearing on
the radar, the centre of the map and every ISS pass time are all
measured from it. With a GPS dongle it comes from the fix; without one
it has to be entered, and until it is, the tracking modes say so rather
than plotting anything at a guessed position.

Also surfaces what the app is running on: which map layers are present,
how old the orbital elements are, and what hardware was detected.
"""

from __future__ import annotations

import os
import sys

import customtkinter as ctk

from gui.themes.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_GREEN, Theme
from gui.views.base_view import BaseView, CORE_DIR

sys.path.insert(0, CORE_DIR)


class SettingsView(BaseView):
    def __init__(self, master, theme: Theme, console, **kwargs):
        super().__init__(master, theme, console, **kwargs)
        self._build_ui()
        self.after(300, self._refresh_status)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        theme = self.theme
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        head = ctk.CTkFrame(scroll, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Settings",
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=theme.text).pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(head, text="Station setup, bundled data status and hardware "
                               "diagnostics.",
                     font=ctk.CTkFont(size=12), text_color=theme.text_dim
                     ).pack(anchor="w", pady=(0, 12))

        # --- station position --------------------------------------------
        pos = self._panel(scroll, "STATION POSITION")
        ctk.CTkLabel(pos, text="Used for range and bearing on the radar, the centre "
                              "of the map, and ISS pass times. A live GPS fix takes "
                              "priority when one is available.",
                     font=ctk.CTkFont(size=11), text_color=theme.text_dim,
                     wraplength=760, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        row = ctk.CTkFrame(pos, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(row, text="Latitude", width=70, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.lat_entry = ctk.CTkEntry(row, width=120, placeholder_text="52.3676")
        self.lat_entry.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(row, text="Longitude", width=80, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.lon_entry = ctk.CTkEntry(row, width=120, placeholder_text="4.9041")
        self.lon_entry.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(row, text="Label", width=50, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self.label_entry = ctk.CTkEntry(row, width=160, placeholder_text="Home QTH")
        self.label_entry.pack(side="left")

        btns = ctk.CTkFrame(pos, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkButton(btns, text="Save position", width=130, fg_color=ACCENT_GREEN,
                      text_color="#0a0a0a", hover_color="#2fae45",
                      command=self._save_position).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="Use current GPS fix", width=160,
                      command=self._use_gps).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="Clear", width=80, fg_color="#5c3a3a",
                      hover_color="#6d4545", command=self._clear_position).pack(side="left")

        self.position_status = ctk.CTkLabel(pos, text="", anchor="w", justify="left",
                                            font=ctk.CTkFont(size=12),
                                            text_color=ACCENT_CYAN)
        self.position_status.pack(fill="x", padx=16, pady=(0, 12))

        # --- behaviour ----------------------------------------------------
        beh = self._panel(scroll, "BEHAVIOUR")
        self.gps_switch = ctk.CTkSwitch(beh, text="Use GPS fix when available",
                                        command=self._save_toggles)
        self.gps_switch.pack(anchor="w", padx=16, pady=(0, 6))
        self.net_switch = ctk.CTkSwitch(beh, text="Allow optional network access "
                                                  "(orbital element refresh only)",
                                        command=self._save_toggles)
        self.net_switch.pack(anchor="w", padx=16, pady=(0, 4))
        ctk.CTkLabel(beh, text="Everything else runs offline. Nothing in PAR AVION "
                              "needs an account or an API key, and no mode "
                              "transmits.",
                     font=ctk.CTkFont(size=11), text_color=theme.text_dim,
                     wraplength=760, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        # --- bundled data -------------------------------------------------
        data = self._panel(scroll, "BUNDLED DATA")
        self.data_label = ctk.CTkLabel(data, text="Checking...", anchor="w",
                                       justify="left",
                                       font=ctk.CTkFont(family="Consolas", size=11),
                                       text_color=theme.text_dim)
        self.data_label.pack(fill="x", padx=16, pady=(0, 8))
        data_btns = ctk.CTkFrame(data, fg_color="transparent")
        data_btns.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(data_btns, text="Refresh orbital elements", width=190,
                      command=self._refresh_tle).pack(side="left", padx=(0, 8))
        ctk.CTkButton(data_btns, text="Re-check", width=100,
                      command=self._refresh_status).pack(side="left")

        # --- hardware -----------------------------------------------------
        hw = self._panel(scroll, "HARDWARE")
        self.hw_label = ctk.CTkLabel(hw, text="Not scanned yet.", anchor="w",
                                     justify="left",
                                     font=ctk.CTkFont(family="Consolas", size=11),
                                     text_color=theme.text_dim)
        self.hw_label.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(hw, text="Scan hardware", width=140,
                      command=self._scan_hardware).pack(anchor="w", padx=16, pady=(0, 12))

        # --- about --------------------------------------------------------
        about = self._panel(scroll, "ABOUT")
        ctk.CTkLabel(
            about,
            text=("PAR AVION — Tactical RF & Telemetry Suite\n"
                  "Appearance: dark only, by design.\n\n"
                  "Every mode is receive-only: ADS-B, AIS, SSTV, CW and the "
                  "spectrum display decode signals that are already being "
                  "broadcast, and orbital elements are published data. Nothing "
                  "here keys a transmitter.\n\n"
                  "Self-tests you can run from the repository root:\n"
                  "  python3 tools/sstv_selftest.py      SSTV decode, all modes\n"
                  "  python3 tools/iss_selftest.py       ISS tracking, offline\n"
                  "  python3 tools/update_tle.py         refresh orbital elements"),
            anchor="w", justify="left", font=ctk.CTkFont(size=12),
            text_color=theme.text_dim, wraplength=820).pack(anchor="w", padx=16,
                                                            pady=(0, 10))
        self.paths_label = ctk.CTkLabel(about, text="", anchor="w", justify="left",
                                        font=ctk.CTkFont(family="Consolas", size=10),
                                        text_color="#5c6873")
        self.paths_label.pack(fill="x", padx=16, pady=(0, 12))

    def _panel(self, parent, title: str):
        frame = ctk.CTkFrame(parent, fg_color=self.theme.panel, corner_radius=10)
        frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=self.theme.text).pack(anchor="w", padx=16, pady=(12, 6))
        return frame

    # ------------------------------------------------------------------
    # Position
    # ------------------------------------------------------------------
    def _save_position(self) -> None:
        from modules import appconfig
        lat_text = self.lat_entry.get().strip()
        lon_text = self.lon_entry.get().strip()
        if not lat_text or not lon_text:
            self.console.log("Enter both a latitude and a longitude.", "warning")
            return
        try:
            lat = float(lat_text)
            lon = float(lon_text)
        except ValueError:
            self.console.log("Latitude and longitude must be decimal degrees, "
                             "e.g. 52.3676 and 4.9041.", "error")
            return
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            self.console.log("Latitude must be -90..90 and longitude -180..180.",
                             "error")
            return
        if appconfig.set_home(lat, lon, self.label_entry.get().strip()):
            self.console.log(f"Station position saved: {lat:+.4f}, {lon:+.4f}. "
                             "Restart tracking in a mode to pick it up.", "success")
        else:
            self.console.log("Could not write the config file — check permissions "
                             f"on {appconfig.CONFIG_DIR}.", "error")
        self._refresh_status()

    def _clear_position(self) -> None:
        from modules import appconfig
        appconfig.set_home(None, None, "")
        self.lat_entry.delete(0, "end")
        self.lon_entry.delete(0, "end")
        self.label_entry.delete(0, "end")
        self.console.log("Station position cleared.", "info")
        self._refresh_status()

    def _use_gps(self) -> None:
        self.console.log("Polling GPS...", "info")
        self.run_async(self._poll_gps, self._on_gps)

    @staticmethod
    def _poll_gps():
        from modules import hardware
        return hardware.detect_gps()

    def _on_gps(self, gps, error) -> None:
        if error is not None:
            self.console.log(f"GPS poll failed: {error}", "error")
            return
        if gps is None or not gps.fix or gps.lat is None:
            detail = f" ({gps})" if gps is not None else ""
            self.console.log(f"No GPS fix available{detail}. Enter the position "
                             "by hand instead.", "warning")
            return
        self.lat_entry.delete(0, "end")
        self.lat_entry.insert(0, f"{gps.lat:.5f}")
        self.lon_entry.delete(0, "end")
        self.lon_entry.insert(0, f"{gps.lon:.5f}")
        self.console.log(f"GPS fix: {gps.lat:+.5f}, {gps.lon:+.5f}. "
                         "Press Save position to keep it.", "success")

    def _save_toggles(self) -> None:
        from modules import appconfig
        cfg = appconfig.load()
        cfg.use_gps = bool(self.gps_switch.get())
        cfg.allow_network = bool(self.net_switch.get())
        appconfig.save(cfg)
        self.console.log(
            f"Saved: GPS {'enabled' if cfg.use_gps else 'disabled'}, "
            f"network {'allowed' if cfg.allow_network else 'blocked'}.", "info")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def _refresh_status(self) -> None:
        from modules import appconfig
        cfg = appconfig.load(force=True)

        if cfg.home_lat is not None and not self.lat_entry.get().strip():
            self.lat_entry.insert(0, f"{cfg.home_lat:.5f}")
            self.lon_entry.insert(0, f"{cfg.home_lon:.5f}")
            if cfg.home_label:
                self.label_entry.insert(0, cfg.home_label)
        self.gps_switch.select() if cfg.use_gps else self.gps_switch.deselect()
        self.net_switch.select() if cfg.allow_network else self.net_switch.deselect()

        self.paths_label.configure(
            text=f"config: {appconfig.CONFIG_PATH}\n"
                 f"cache:  {appconfig.CACHE_DIR}\n"
                 f"data:   {appconfig.DATA_DIR}")
        self.run_async(self._gather_status, self._on_status)

    @staticmethod
    def _gather_status():
        from modules import appconfig, iss, mapdata
        lat, lon, source = appconfig.reference_location()

        layers = []
        total = 0
        for name in mapdata.AVAILABLE_LAYERS:
            path = mapdata.layer_path(name)
            if path and os.path.exists(path):
                size = os.path.getsize(path)
                total += size
                layers.append((name, size))
            else:
                layers.append((name, None))

        cached = iss.load_cached_tle()
        bundled = iss.load_bundled_tle()
        return lat, lon, source, layers, total, cached, bundled

    def _on_status(self, result, error) -> None:
        if error is not None:
            self.console.log(f"Status check failed: {error}", "warning")
            return
        from modules import appconfig
        lat, lon, source, layers, total, cached, bundled = result

        self.position_status.configure(
            text="In use: " + appconfig.describe_location(lat, lon, source),
            text_color=ACCENT_CYAN if lat is not None else ACCENT_AMBER)

        lines = ["Offline vector map (Natural Earth, public domain):"]
        for name, size in layers:
            lines.append(f"  {name:<18}" + (f"{size / 1024:>7.0f} KB" if size
                                            else "   MISSING"))
        lines.append(f"  {'total':<18}{total / 1024 / 1024:>7.2f} MB")
        lines.append("")
        lines.append("Orbital elements:")
        for label, tle in (("cached", cached), ("bundled", bundled)):
            if tle is None:
                lines.append(f"  {label:<18}   none")
            else:
                lines.append(f"  {label:<18}   {tle.age_text()}")
        self.data_label.configure(text="\n".join(lines))

    def _refresh_tle(self) -> None:
        self.console.log("Fetching fresh orbital elements...", "info")
        self.run_async(self._do_refresh_tle, self._on_tle_refreshed)

    @staticmethod
    def _do_refresh_tle():
        from modules import iss
        tle, message = iss.fetch_tle()
        if tle is not None:
            iss.save_cached_tle(tle)
        return tle, message

    def _on_tle_refreshed(self, result, error) -> None:
        if error is not None:
            self.console.log(f"Element refresh failed: {error}", "error")
            return
        tle, message = result
        self.console.log(message, "success" if tle is not None else "warning")
        self._refresh_status()

    # ------------------------------------------------------------------
    # Hardware
    # ------------------------------------------------------------------
    def _scan_hardware(self) -> None:
        self.console.log("Scanning for SDR / GPS / ADS-B feed...", "info")
        self.run_async(self._do_scan, self._on_scan)

    @staticmethod
    def _do_scan():
        from modules import hardware
        return hardware.full_report()

    def _on_scan(self, report, error) -> None:
        if error is not None:
            self.console.log(f"Hardware scan failed: {error}", "error")
            return
        lines = []
        if report.sdrs:
            for dev in report.sdrs:
                lines.append(f"SDR      {dev}")
        else:
            lines.append("SDR      none detected")
        lines.append(f"GPS      {report.gps if report.gps else 'none detected'}")
        lines.append(f"dump1090 {'listening on :30002' if report.dump1090_running else 'not running'}")
        self.hw_label.configure(text="\n".join(lines))
        for warning in report.errors:
            self.console.log(warning, "warning")
        self.console.log("Hardware scan complete.", "success")
