"""
waterfall.py — Spectrum analyser / waterfall back end for PAR AVION.

Reads raw IQ from an RTL-SDR via pyrtlsdr, computes an averaged FFT
power spectrum in dB, and hands it to whichever front end asked for it
(the curses TUI here, or the GUI's embedded spectrum + waterfall
canvases).

Two things matter for a waterfall that looks sharp rather than mushy:

  * Resolution. The FFT is sized to at least twice the number of output
    bins requested, so each displayed pixel column is backed by real
    data instead of being interpolated up from a short transform.
  * A stable amplitude scale. Normalising each frame to its own min/max
    — what this module used to do — makes the colours jump every frame,
    hides weak signals when a strong one appears, and turns an empty
    band into full-scale noise. Power is now reported in dB against a
    fixed reference, with the display floor and ceiling set explicitly
    by the operator (or tracked slowly from the noise floor), so a
    carrier looks the same from one second to the next.

Receive-only: this reads whatever signal is already present at the
antenna. It does not key up a transmitter.
"""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

try:
    from rtlsdr import RtlSdr
    HAVE_RTLSDR = True
except Exception:
    # Broader than ImportError on purpose: pyrtlsdr raises AttributeError
    # at import time when the installed librtlsdr.so is older than the
    # bindings expect (missing symbols like rtlsdr_set_dithering).
    RtlSdr = None
    HAVE_RTLSDR = False


# ---------------------------------------------------------------------------
# Band presets: (name, center_freq_hz, sample_rate_hz)
# ---------------------------------------------------------------------------
BAND_PRESETS = [
    ("433 MHz ISM", 433_100_000, 2_048_000),
    ("FM Broadcast", 100_000_000, 2_400_000),
    ("Airband (AM)", 124_000_000, 2_048_000),
    ("2m HAM", 145_000_000, 2_048_000),
    ("70cm HAM", 435_000_000, 2_048_000),
    ("315 MHz ISM", 315_000_000, 2_048_000),
    ("ADS-B 1090", 1_090_000_000, 2_048_000),
    ("AIS Marine", 162_000_000, 2_048_000),
    ("NOAA APT", 137_500_000, 2_048_000),
    ("Shortwave (up-converted)", 10_000_000, 2_048_000),
]

# Tuning steps offered by the VFO, smallest first.
TUNE_STEPS_HZ = [100, 500, 1_000, 2_500, 5_000, 9_000, 10_000, 12_500,
                 25_000, 50_000, 100_000, 200_000, 500_000, 1_000_000]
DEFAULT_STEP_HZ = 25_000

# RTL-SDR R820T/R860 tuning range. Direct-sampling mods go lower, so the
# floor is deliberately permissive rather than refusing to tune.
MIN_TUNE_HZ = 100_000
MAX_TUNE_HZ = 1_766_000_000

WATERFALL_GRADIENT = [
    (curses.COLOR_BLACK, " "),
    (curses.COLOR_BLUE, "░"),
    (curses.COLOR_CYAN, "▒"),
    (curses.COLOR_GREEN, "▓"),
    (curses.COLOR_YELLOW, "█"),
    (curses.COLOR_RED, "█"),
]


def init_gradient_colors(base_pair_start: int = 20) -> List[int]:
    """Initialize a run of color pairs for the waterfall gradient."""
    pairs = []
    for i, (color, _) in enumerate(WATERFALL_GRADIENT):
        pair_id = base_pair_start + i
        try:
            curses.init_pair(pair_id, color, curses.COLOR_BLACK)
        except curses.error:
            pass
        pairs.append(pair_id)
    return pairs


@dataclass
class SpectrumFrame:
    """One spectrum measurement."""
    power_db: np.ndarray        # per-bin power, dBFS
    norm: np.ndarray            # same data mapped to 0..1 by floor/ceiling
    center_hz: int
    span_hz: int
    simulated: bool
    floor_db: float
    ceiling_db: float

    def bin_freq_hz(self, index: int) -> float:
        n = len(self.power_db)
        if n < 2:
            return float(self.center_hz)
        return self.center_hz - self.span_hz / 2.0 + self.span_hz * index / (n - 1)

    def peak(self) -> Tuple[float, float]:
        """(frequency_hz, power_db) of the strongest bin."""
        idx = int(np.argmax(self.power_db))
        return self.bin_freq_hz(idx), float(self.power_db[idx])


@dataclass
class TunerState:
    band_index: int = 0
    center_freq_hz: int = BAND_PRESETS[0][1]
    sample_rate_hz: int = BAND_PRESETS[0][2]
    gain: str = "auto"
    step_hz: int = DEFAULT_STEP_HZ

    def apply_preset(self, index: int) -> None:
        index = index % len(BAND_PRESETS)
        _, freq, rate = BAND_PRESETS[index]
        self.band_index = index
        self.center_freq_hz = freq
        self.sample_rate_hz = rate

    def set_freq(self, hz: float) -> int:
        self.center_freq_hz = int(max(MIN_TUNE_HZ, min(MAX_TUNE_HZ, hz)))
        return self.center_freq_hz

    def nudge(self, steps: int = 1) -> int:
        """Move by whole tuning steps, snapped to the step grid so
        repeated clicks land on round frequencies."""
        target = self.center_freq_hz + steps * self.step_hz
        if self.step_hz > 1:
            target = round(target / self.step_hz) * self.step_hz
        return self.set_freq(target)

    def cycle_step(self, direction: int = 1) -> int:
        try:
            i = TUNE_STEPS_HZ.index(self.step_hz)
        except ValueError:
            i = TUNE_STEPS_HZ.index(DEFAULT_STEP_HZ)
        self.step_hz = TUNE_STEPS_HZ[(i + direction) % len(TUNE_STEPS_HZ)]
        return self.step_hz

    @property
    def span_hz(self) -> int:
        return int(self.sample_rate_hz)

    def band_name(self) -> str:
        return BAND_PRESETS[self.band_index][0]


def format_hz(hz: float) -> str:
    """Human-readable frequency, picking a sensible unit."""
    if hz >= 1e9:
        return f"{hz / 1e9:.6f} GHz"
    if hz >= 1e6:
        return f"{hz / 1e6:.4f} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3f} kHz"
    return f"{hz:.0f} Hz"


def format_step(hz: int) -> str:
    if hz >= 1_000_000:
        return f"{hz / 1e6:g} MHz"
    if hz >= 1_000:
        return f"{hz / 1e3:g} kHz"
    return f"{hz} Hz"


class SDRReader:
    """Wraps pyrtlsdr; falls back to a synthetic spectrum when no
    hardware is present so the UI stays usable and testable without a
    dongle attached."""

    def __init__(self, tuner: TunerState, averages: int = 4):
        self.tuner = tuner
        self.sdr: Optional["RtlSdr"] = None
        self.simulated = not HAVE_RTLSDR
        self.averages = max(1, averages)
        self.last_error = ""
        # Display range in dBFS. These defaults suit an RTL-SDR's noise
        # floor; the UI can move them, and auto-floor tracking nudges the
        # bottom of the range toward the measured noise.
        self.floor_db = -75.0
        self.ceiling_db = -15.0
        self.auto_floor = True
        self._floor_estimate: Optional[float] = None

        if HAVE_RTLSDR:
            try:
                self.sdr = RtlSdr()
                self._apply_tuning()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.sdr = None
                self.simulated = True

    # -- hardware ------------------------------------------------------
    def _apply_tuning(self) -> None:
        if self.sdr is None:
            return
        self.sdr.sample_rate = self.tuner.sample_rate_hz
        self.sdr.center_freq = self.tuner.center_freq_hz
        self.sdr.gain = "auto" if self.tuner.gain == "auto" else float(self.tuner.gain)

    def retune(self) -> None:
        try:
            self._apply_tuning()
        except Exception as e:
            self.last_error = f"retune failed: {e}"

    def close(self) -> None:
        if self.sdr is not None:
            try:
                self.sdr.close()
            except Exception:
                pass
            self.sdr = None

    # -- acquisition ---------------------------------------------------
    @staticmethod
    def _fft_size_for(n_bins: int) -> int:
        """At least two FFT bins per displayed column, rounded up to a
        power of two — interpolating a short transform up to the display
        width is what makes a waterfall look blocky."""
        target = max(256, n_bins * 2)
        return 1 << (int(target - 1).bit_length())

    def read_frame(self, n_bins: int) -> SpectrumFrame:
        n_bins = max(16, int(n_bins))
        if self.simulated or self.sdr is None:
            power_db = self._simulate_db(n_bins)
        else:
            try:
                power_db = self._measure_db(n_bins)
            except Exception as e:
                self.last_error = f"read failed: {e}"
                self.simulated = True
                power_db = self._simulate_db(n_bins)

        floor, ceiling = self._display_range(power_db)
        span = max(1e-6, ceiling - floor)
        norm = np.clip((power_db - floor) / span, 0.0, 1.0)
        return SpectrumFrame(
            power_db=power_db, norm=norm,
            center_hz=self.tuner.center_freq_hz, span_hz=self.tuner.span_hz,
            simulated=self.simulated, floor_db=floor, ceiling_db=ceiling,
        )

    def _measure_db(self, n_bins: int) -> np.ndarray:
        nfft = self._fft_size_for(n_bins)
        window = np.hanning(nfft)
        # Welch-style averaging: several short transforms averaged in the
        # power domain. Each average cuts the noise variance, which is
        # what lets a weak carrier stand clear of the grass instead of
        # flickering in and out frame to frame.
        accum = np.zeros(nfft)
        for _ in range(self.averages):
            samples = self.sdr.read_samples(nfft)
            if len(samples) < nfft:
                samples = np.pad(samples, (0, nfft - len(samples)))
            spectrum = np.fft.fftshift(np.fft.fft(samples[:nfft] * window))
            accum += np.abs(spectrum) ** 2
        accum /= self.averages

        # Normalise by window gain and FFT length so the dB scale means
        # the same thing at any FFT size.
        scale = (np.sum(window) ** 2)
        power_db = 10.0 * np.log10(accum / scale + 1e-20)
        return self._rebin(power_db, n_bins)

    @staticmethod
    def _rebin(power_db: np.ndarray, n_bins: int) -> np.ndarray:
        """Reduce the transform to the display width by taking the peak
        of each group of bins. Peak-hold rather than averaging keeps
        narrow carriers visible when several FFT bins share one pixel —
        averaging would bury a single-bin signal in its neighbours."""
        n = len(power_db)
        if n == n_bins:
            return power_db
        if n < n_bins:
            return np.interp(np.linspace(0, n - 1, n_bins), np.arange(n), power_db)
        group = n // n_bins
        usable = group * n_bins
        return power_db[:usable].reshape(n_bins, group).max(axis=1)

    def _display_range(self, power_db: np.ndarray) -> Tuple[float, float]:
        if not self.auto_floor:
            return self.floor_db, self.ceiling_db
        # Track the noise floor slowly from a low percentile, so the
        # colour mapping follows gain/band changes without flickering
        # frame to frame the way per-frame min/max normalisation does.
        measured = float(np.percentile(power_db, 20)) - 3.0
        if self._floor_estimate is None:
            self._floor_estimate = measured
        else:
            self._floor_estimate += 0.15 * (measured - self._floor_estimate)
        floor = self._floor_estimate
        return floor, floor + (self.ceiling_db - self.floor_db)

    def _simulate_db(self, n_bins: int) -> np.ndarray:
        """Synthetic noise floor plus a few carriers, used only when no
        SDR hardware is connected so the UI still runs and can be tested."""
        t = time.time()
        x = np.linspace(-1.0, 1.0, n_bins)
        noise = np.random.normal(-68.0, 2.0, n_bins)
        carriers = [
            (0.30 * np.sin(t * 0.25), 0.0012, 45.0),
            (-0.50, 0.0006, 30.0),
            (0.62, 0.02, 14.0),
            (-0.12 + 0.05 * np.sin(t * 0.9), 0.0003, 22.0),
        ]
        spectrum = noise.copy()
        for center, width, amplitude in carriers:
            spectrum += amplitude * np.exp(-((x - center) ** 2) / width)
        return spectrum

    # -- compatibility --------------------------------------------------
    def read_power_spectrum(self, n_bins: int) -> np.ndarray:
        """0..1 normalised spectrum (used by the curses UI)."""
        return self.read_frame(n_bins).norm


def _power_to_gradient_index(value: float) -> int:
    idx = int(value * (len(WATERFALL_GRADIENT) - 1))
    return max(0, min(len(WATERFALL_GRADIENT) - 1, idx))


def run(stdscr) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(80)
    curses.start_color()
    curses.use_default_colors()
    gradient_pairs = init_gradient_colors()

    tuner = TunerState()
    tuner.apply_preset(0)
    reader = SDRReader(tuner)

    height, width = stdscr.getmaxyx()
    header_h = 3
    spectrum_h = 6
    waterfall_h = max(1, height - header_h - spectrum_h - 1)
    n_bins = max(16, width - 2)

    waterfall_history: List[np.ndarray] = []

    try:
        while True:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            elif key == curses.KEY_LEFT:
                tuner.nudge(-1)
                reader.retune()
            elif key == curses.KEY_RIGHT:
                tuner.nudge(1)
                reader.retune()
            elif key in (ord("["), ord(",")):
                tuner.cycle_step(-1)
            elif key in (ord("]"), ord(".")):
                tuner.cycle_step(1)
            elif key in (curses.KEY_UP, curses.KEY_DOWN):
                delta = 1 if key == curses.KEY_UP else -1
                tuner.apply_preset(tuner.band_index + delta)
                reader.retune()

            frame = reader.read_frame(n_bins)
            spectrum = frame.norm
            waterfall_history.insert(0, spectrum)
            if len(waterfall_history) > waterfall_h:
                waterfall_history.pop()

            stdscr.erase()

            mode_tag = "SIMULATED (no SDR detected)" if frame.simulated else "LIVE"
            peak_hz, peak_db = frame.peak()
            stdscr.addstr(0, 1, f" PAR AVION — Waterfalls  [{mode_tag}] ", curses.A_BOLD)
            stdscr.addstr(1, 1,
                          f" {tuner.band_name()}   {format_hz(tuner.center_freq_hz)}   "
                          f"span {format_hz(tuner.span_hz)}   step {format_step(tuner.step_hz)}   "
                          f"peak {format_hz(peak_hz)} @ {peak_db:.0f} dB ")
            stdscr.addstr(2, 1,
                          " <-/-> tune   [ ] step size   up/down band preset   Q quit ")

            for col in range(min(n_bins, spectrum.shape[0])):
                bar_height = int(spectrum[col] * spectrum_h)
                idx = _power_to_gradient_index(spectrum[col])
                for row in range(bar_height):
                    y = header_h + spectrum_h - 1 - row
                    try:
                        stdscr.addch(y, col + 1, WATERFALL_GRADIENT[idx][1],
                                     curses.color_pair(gradient_pairs[idx]))
                    except curses.error:
                        pass

            for row_i, row_spectrum in enumerate(waterfall_history):
                y = header_h + spectrum_h + row_i
                if y >= height - 1:
                    break
                for col in range(min(n_bins, row_spectrum.shape[0])):
                    idx = _power_to_gradient_index(row_spectrum[col])
                    try:
                        stdscr.addch(y, col + 1, WATERFALL_GRADIENT[idx][1],
                                     curses.color_pair(gradient_pairs[idx]))
                    except curses.error:
                        pass

            stdscr.refresh()
    finally:
        reader.close()
