"""
sstv.py — Slow Scan TV (SSTV) decoder for PAR AVION.

Captures FM-demodulated audio (via rtl_fm piped from an RTL-SDR tuned to
an SSTV calling frequency, e.g. 145.800 MHz for ISS SSTV events, or from
the system microphone/line-in if fed from an external radio), detects
the VIS (Vertical Interval Signaling) header that identifies the SSTV
mode in use, then decodes the audio tone stream into an image using
real per-mode timing constants.

Supported modes: Martin 1, Martin 2, Scottie 1, Scottie 2, Scottie DX,
Robot 36, Robot 72.

TIMING SOURCE
-------------
Every constant below comes from JL Barber (N7CXI), "Proposal for SSTV
Mode Specifications" (Dayton 2000) — the de-facto SSTV spec — and is
cross-checked against the line times in windytan/slowrx's modespec.c
and the mode tables in "Image Communication on Short Waves".

Each mode is described as an explicit *segment list* (sync pulse, gaps,
and scan segments with their own durations and sample counts) rather
than a single parametric formula. That matters: the Robot modes are not
"three equal colour channels" like Martin/Scottie, and a one-size
formula silently mis-times them. Specifically:

  Robot 36 (150 ms line, 4:2:0):
    sync 9 ms | porch 3 ms | Y 88 ms | separator 4.5 ms | porch 1.5 ms
    | chroma 44 ms
    The separator tone encodes line parity: 1500 Hz on even lines
    (carrying R-Y) and 2300 Hz on odd lines (carrying B-Y). Each chroma
    scan is shared between the two lines of its pair.

  Robot 72 (300 ms line, 4:2:2):
    sync 9 ms | porch 3 ms | Y 138 ms | sep 4.5 ms | porch 1.5 ms
    | R-Y 69 ms | sep 4.5 ms | porch 1.5 ms | B-Y 69 ms
    Both chroma channels are present on every line, at half the Y
    sample rate.

Note the Y scan is *not* at the chroma pixel rate: Robot 36 spends
88 ms on 320 Y samples (0.275 ms/px) but only 44 ms on its chroma
(0.1375 ms/px). Reading Y at the chroma rate — the previous bug here —
covers only the first half of each Y scan, so the left half of the
picture is stretched across the whole width and the right half is never
seen at all.

DECODE APPROACH
---------------
  1. VIS detection scans the *whole* rolling buffer, not just its
     start, so a header that begins mid-buffer still locks: the audio
     is reduced to a coarse instantaneous-frequency track (one value
     per 2 ms) and the leader/break/leader/start-bit pattern is matched
     at every offset with vectorised numpy ops. Then 7 data bits plus
     an even-parity bit are read (1300 Hz = 0, 1100 Hz = 1) and the
     parity is verified, so a garbled header is rejected rather than
     mis-identified as a mode.
  2. Per-line decode reads each segment's pixel run from a *continuous*
     instantaneous-frequency estimate across the whole line (analytic
     signal + phase derivative, sampled at each pixel's centre), NOT a
     per-pixel-window frequency estimate — the fastest modes have pixel
     windows only a handful of samples long, far too few for a
     block-based Goertzel/FFT bin to resolve 800 Hz of bandwidth. The
     line slice is edge-padded so the estimate has settling room at
     both ends (and so the final line of a transmission still decodes
     when the audio simply stops).
  3. Each line re-acquires the 1200 Hz sync pulse within a small search
     window around where it is expected, and the correction is carried
     forward. Without this, sub-millisecond timing error accumulates
     over 240-256 lines and the picture shears.

Receive-only: decodes audio already present at the input; transmits
nothing.
"""

from __future__ import annotations

import curses
import math
import os
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    from scipy.signal import hilbert as _scipy_hilbert
    HAVE_SCIPY = True
except ImportError:
    _scipy_hilbert = None
    HAVE_SCIPY = False

from . import radar_ui

# 44.1kHz matches what real SSTV software (slowrx, MMSSTV, QSSTV) uses.
SAMPLE_RATE = 44100
CAPTURE_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures", "sstv"
)

# VIS header timing (constant across all SSTV modes)
VIS_LEADER_HZ = 1900.0
VIS_LEADER_SEC = 0.300
VIS_BREAK_HZ = 1200.0
VIS_BREAK_SEC = 0.010
VIS_START_HZ = 1200.0
VIS_START_SEC = 0.030
VIS_BIT_SEC = 0.030
VIS_BIT0_HZ = 1300.0
VIS_BIT1_HZ = 1100.0
VIS_STOP_HZ = 1200.0
VIS_STOP_SEC = 0.030
VIS_HEADER_SEC = (
    VIS_LEADER_SEC + VIS_BREAK_SEC + VIS_LEADER_SEC + VIS_START_SEC
    + VIS_BIT_SEC * 8 + VIS_STOP_SEC
)

SYNC_HZ = 1200.0
BLACK_HZ = 1500.0
WHITE_HZ = 2300.0

# Robot separator tones that encode line parity (Robot 36).
ROBOT_SEP_EVEN_HZ = 1500.0   # this line carries R-Y
ROBOT_SEP_ODD_HZ = 2300.0    # this line carries B-Y


# ---------------------------------------------------------------------------
# Mode specifications — explicit segment lists
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """One timed piece of a scan line.

    kind     "sync" (1200 Hz line sync), "gap" (porch/separator), or
             "scan" (actual picture data).
    seconds  duration.
    channel  for scan segments: "R"/"G"/"B" (Martin, Scottie), "Y",
             "C" (Robot 36's parity-dependent chroma), "RY", "BY".
    samples  how many pixel samples to take across this scan segment.
    tone_hz  expected tone for sync/gap segments; 0.0 when it varies
             (Robot 36's separator, which encodes line parity).
    """
    kind: str
    seconds: float
    channel: str = ""
    samples: int = 0
    tone_hz: float = 0.0


@dataclass
class ModeSpec:
    name: str
    vis_code: int
    line_pixels: int
    image_lines: int
    layout: str                       # "rgb" | "robot36" | "robot72"
    segments: List[Segment] = field(default_factory=list)

    # ---- derived -----------------------------------------------------
    def line_seconds(self) -> float:
        """Time from the start of one line to the start of the next."""
        return sum(s.seconds for s in self.segments)

    def sync_offset_seconds(self) -> float:
        """Offset of the sync pulse from the start of the line window.
        Martin/Robot start the line on sync (0.0); Scottie's sync sits
        mid-line, after the green and blue scans."""
        t = 0.0
        for s in self.segments:
            if s.kind == "sync":
                return t
            t += s.seconds
        return 0.0

    def sync_seconds(self) -> float:
        for s in self.segments:
            if s.kind == "sync":
                return s.seconds
        return 0.0

    def segment_offsets(self) -> List[Tuple[Segment, float]]:
        """[(segment, offset_seconds_from_line_start), ...]"""
        out = []
        t = 0.0
        for s in self.segments:
            out.append((s, t))
            t += s.seconds
        return out

    def scan_seconds(self, channel: str) -> float:
        for s in self.segments:
            if s.kind == "scan" and s.channel == channel:
                return s.seconds
        return 0.0


def _martin(name: str, vis_code: int, pixel_seconds: float) -> ModeSpec:
    """Martin M1/M2 — N7CXI: sync 4.862 ms, porch 0.572 ms, then
    G / sep / B / sep / R / sep, all separators 0.572 ms @ 1500 Hz.
    M1 line = 446.446 ms, M2 line = 226.798 ms."""
    scan = pixel_seconds * 320
    sep = 0.000572
    return ModeSpec(
        name=name, vis_code=vis_code, line_pixels=320, image_lines=256,
        layout="rgb",
        segments=[
            Segment("sync", 0.004862, tone_hz=SYNC_HZ),
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "G", 320),
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "B", 320),
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "R", 320),
            Segment("gap", sep, tone_hz=1500.0),
        ],
    )


def _scottie(name: str, vis_code: int, pixel_seconds: float) -> ModeSpec:
    """Scottie S1/S2/DX — N7CXI: sep 1.5 ms / G / sep 1.5 ms / B /
    sync 9 ms / porch 1.5 ms / R. S1 line = 428.22 ms."""
    scan = pixel_seconds * 320
    sep = 0.0015
    return ModeSpec(
        name=name, vis_code=vis_code, line_pixels=320, image_lines=256,
        layout="rgb",
        segments=[
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "G", 320),
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "B", 320),
            Segment("sync", 0.009, tone_hz=SYNC_HZ),
            Segment("gap", sep, tone_hz=1500.0),
            Segment("scan", scan, "R", 320),
        ],
    )


def _robot36() -> ModeSpec:
    """Robot 36 Color — N7CXI: 150 ms line, Y 88 ms, chroma 44 ms,
    separator 4.5 ms (1500 Hz even / 2300 Hz odd), porch 1.5 ms."""
    return ModeSpec(
        name="Robot 36", vis_code=0x08, line_pixels=320, image_lines=240,
        layout="robot36",
        segments=[
            Segment("sync", 0.009, tone_hz=SYNC_HZ),
            Segment("gap", 0.003, tone_hz=1500.0),
            Segment("scan", 0.088, "Y", 320),
            Segment("gap", 0.0045, tone_hz=0.0),   # parity separator
            Segment("gap", 0.0015, tone_hz=1900.0),
            Segment("scan", 0.044, "C", 160),
        ],
    )


def _robot72() -> ModeSpec:
    """Robot 72 Color — N7CXI: 300 ms line, Y 138 ms, R-Y 69 ms,
    B-Y 69 ms, separators 4.5 ms, porches 1.5 ms."""
    return ModeSpec(
        name="Robot 72", vis_code=0x0C, line_pixels=320, image_lines=240,
        layout="robot72",
        segments=[
            Segment("sync", 0.009, tone_hz=SYNC_HZ),
            Segment("gap", 0.003, tone_hz=1500.0),
            Segment("scan", 0.138, "Y", 320),
            Segment("gap", 0.0045, tone_hz=1500.0),
            Segment("gap", 0.0015, tone_hz=1900.0),
            Segment("scan", 0.069, "RY", 160),
            Segment("gap", 0.0045, tone_hz=1500.0),
            Segment("gap", 0.0015, tone_hz=1900.0),
            Segment("scan", 0.069, "BY", 160),
        ],
    )


MODE_SPECS: List[ModeSpec] = [
    _martin("Martin 1", 0x2C, 0.0004576),
    _martin("Martin 2", 0x28, 0.0002288),
    _scottie("Scottie 1", 0x3C, 0.0004320),
    _scottie("Scottie 2", 0x38, 0.0002752),
    _scottie("Scottie DX", 0x4C, 0.0010800),
    _robot36(),
    _robot72(),
]

VIS_LOOKUP = {m.vis_code: m for m in MODE_SPECS}


# ---------------------------------------------------------------------------
# DSP helpers
# ---------------------------------------------------------------------------

def _goertzel_mag(samples, sample_rate: int, target_hz: float) -> float:
    """Goertzel algorithm: magnitude of the DFT bin nearest target_hz.
    Used only where windows are long (VIS bits are 30 ms = 1323 samples
    at 44.1 kHz); never for per-pixel decoding."""
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + (n * target_hz) / sample_rate)
    w = 2 * math.pi * k / n
    coeff = 2 * math.cos(w)
    s_prev, s_prev2 = 0.0, 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    power = s_prev2 ** 2 + s_prev ** 2 - coeff * s_prev * s_prev2
    return math.sqrt(max(0.0, power))


def _analytic_inst_freq(samples, sample_rate: int):
    """Instantaneous frequency (Hz) per sample via the analytic signal's
    phase derivative. Falls back to a pure-numpy analytic signal if
    scipy isn't installed, so SSTV still works without scipy."""
    x = np.asarray(samples, dtype=np.float64)
    n = x.size
    if n < 4:
        return np.full(max(n, 1), BLACK_HZ, dtype=np.float64)

    if HAVE_SCIPY:
        analytic = _scipy_hilbert(x)
    else:
        # Equivalent to scipy.signal.hilbert: zero the negative-frequency
        # half of the spectrum and double the positive half.
        spec = np.fft.fft(x)
        h = np.zeros(n)
        if n % 2 == 0:
            h[0] = h[n // 2] = 1.0
            h[1:n // 2] = 2.0
        else:
            h[0] = 1.0
            h[1:(n + 1) // 2] = 2.0
        analytic = np.fft.ifft(spec * h)

    # Phase difference between consecutive samples, wrapped to (-pi, pi].
    prod = analytic[1:] * np.conj(analytic[:-1])
    dphase = np.angle(prod)
    freq = dphase / (2.0 * np.pi) * sample_rate
    return np.concatenate([freq, freq[-1:]])


def line_instantaneous_freq_curve(samples, sample_rate: int, smooth_n: int = 3):
    """Continuous instantaneous-frequency curve across an audio segment,
    lightly smoothed. Accurate down to a few samples per pixel because
    it never re-estimates frequency from an isolated short window. The
    first/last few samples are less reliable (edge artifacts), so
    callers pass a padded segment and avoid sampling right at the ends."""
    if not HAVE_NUMPY or len(samples) < 4:
        return np.full(len(samples), BLACK_HZ, dtype=np.float64) if HAVE_NUMPY else []

    inst_freq = _analytic_inst_freq(samples, sample_rate)
    if smooth_n > 1 and inst_freq.size >= smooth_n:
        kernel = np.ones(smooth_n) / smooth_n
        inst_freq = np.convolve(inst_freq, kernel, mode="same")
    # A sample-by-sample phase derivative amplifies additive noise: a
    # few hundredths of a radian of phase error is hundreds of Hz of
    # apparent frequency error. Clamping to a little either side of the
    # SSTV tone range stops individual noise spikes from dragging a
    # pixel's averaged value far off, without biasing clean signals
    # (which never leave 1200-2300 Hz).
    return np.clip(inst_freq, 800.0, 2800.0)


def freq_to_luminance(freq_hz: float) -> int:
    """Map an SSTV tone frequency to a 0-255 pixel value (1500Hz=black,
    2300Hz=white, linear in between)."""
    frac = (freq_hz - BLACK_HZ) / (WHITE_HZ - BLACK_HZ)
    return max(0, min(255, int(round(frac * 255))))


def _freq_array_to_values(freqs):
    """Vectorised freq_to_luminance for a numpy array."""
    vals = (np.asarray(freqs, dtype=np.float64) - BLACK_HZ) / (WHITE_HZ - BLACK_HZ) * 255.0
    return np.clip(np.rint(vals), 0, 255)


# ---------------------------------------------------------------------------
# VIS header detection
# ---------------------------------------------------------------------------

VIS_TRACK_WIN_SEC = 0.002   # coarse frequency track resolution


class VisDetector:
    """Scans a rolling audio buffer for the SSTV VIS header anywhere in
    the buffer (not only at its start) and returns the decoded ModeSpec
    together with the sample index where the picture data begins."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.last_vis_code: Optional[int] = None
        self.last_reject_reason = ""

    # -- coarse track -------------------------------------------------
    def _track(self, buf):
        win_n = max(1, int(VIS_TRACK_WIN_SEC * self.sample_rate))
        usable = (len(buf) // win_n) * win_n
        if usable < win_n * 8:
            return None, win_n
        freqs = _analytic_inst_freq(buf[:usable], self.sample_rate)
        track = freqs.reshape(-1, win_n).mean(axis=1)
        return track, win_n

    def try_detect(self, buf) -> Optional[Tuple[ModeSpec, int]]:
        """Returns (mode, data_start_sample) or None."""
        if not HAVE_NUMPY:
            return None
        track, win_n = self._track(buf)
        if track is None:
            return None

        def wins(seconds: float) -> int:
            return max(1, int(round(seconds / VIS_TRACK_WIN_SEC)))

        n_leader = wins(VIS_LEADER_SEC)
        n_break = wins(VIS_BREAK_SEC)
        n_start = wins(VIS_START_SEC)
        n_bit = wins(VIS_BIT_SEC)
        n_header = n_leader + n_break + n_leader + n_start + n_bit * 9  # +stop

        if track.size < n_header:
            return None

        tol = 150.0
        near_leader = (np.abs(track - VIS_LEADER_HZ) < tol).astype(np.float64)
        near_1200 = (np.abs(track - VIS_BREAK_HZ) < tol).astype(np.float64)

        def frac(flags, length: int):
            """Rolling mean of `flags` over `length` windows, aligned so
            index i is the mean of flags[i:i+length]."""
            c = np.concatenate([[0.0], np.cumsum(flags)])
            return (c[length:] - c[:-length]) / length

        f_leader = frac(near_leader, n_leader)
        f_break = frac(near_1200, n_break)
        f_start = frac(near_1200, n_start)

        max_start = track.size - n_header
        if max_start <= 0:
            return None
        idx = np.arange(max_start + 1)

        off_l1 = idx
        off_bk = idx + n_leader
        off_l2 = idx + n_leader + n_break
        off_st = idx + n_leader + n_break + n_leader

        ok = (
            (f_leader[off_l1] >= 0.80)
            & (f_break[off_bk] >= 0.50)
            & (f_leader[off_l2] >= 0.80)
            & (f_start[off_st] >= 0.60)
        )
        candidates = np.flatnonzero(ok)
        if candidates.size == 0:
            return None

        for cand in candidates:
            header_start = int(cand) * win_n
            result = self._read_bits(buf, header_start)
            if result is not None:
                return result
        return None

    def _read_bits(self, buf, header_start: int) -> Optional[Tuple[ModeSpec, int]]:
        """Read the 7 data bits + parity at a confirmed header position."""
        sr = self.sample_rate
        pos = header_start + int(
            (VIS_LEADER_SEC + VIS_BREAK_SEC + VIS_LEADER_SEC + VIS_START_SEC) * sr
        )
        bit_n = int(VIS_BIT_SEC * sr)
        if pos + bit_n * 9 > len(buf):
            return None

        bits = []
        for i in range(8):  # 7 data bits (LSB first) + 1 parity bit
            # Use the middle 60% of each bit window: the edges contain
            # the frequency transition between adjacent bits.
            lo = pos + i * bit_n + int(bit_n * 0.2)
            hi = pos + i * bit_n + int(bit_n * 0.8)
            window = buf[lo:hi]
            f0 = _goertzel_mag(window, sr, VIS_BIT0_HZ)
            f1 = _goertzel_mag(window, sr, VIS_BIT1_HZ)
            bits.append(1 if f1 > f0 else 0)

        data_bits, parity_bit = bits[:7], bits[7]
        vis_code = sum(b << i for i, b in enumerate(data_bits))
        self.last_vis_code = vis_code

        # SSTV uses even parity across the 7 data bits + parity bit.
        if (sum(data_bits) + parity_bit) % 2 != 0:
            self.last_reject_reason = f"VIS parity error (code {vis_code})"
            return None

        mode = VIS_LOOKUP.get(vis_code)
        if mode is None:
            self.last_reject_reason = f"unsupported VIS code {vis_code}"
            return None

        data_start = header_start + int(VIS_HEADER_SEC * sr)
        return mode, data_start


# ---------------------------------------------------------------------------
# Image buffer
# ---------------------------------------------------------------------------

class SstvImageBuffer:
    """Accumulates decoded RGB rows as they arrive, for live preview and
    final save."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.pixels = np.zeros((height, width, 3), dtype=np.uint8) if HAVE_NUMPY else None
        self.rows_completed = 0
        self.last_save_was_png = False

    def set_row_rgb(self, row: int, r_row, g_row, b_row) -> None:
        if not HAVE_NUMPY or self.pixels is None or row < 0 or row >= self.height:
            return
        self.pixels[row, :, 0] = np.clip(np.asarray(r_row), 0, 255).astype(np.uint8)
        self.pixels[row, :, 1] = np.clip(np.asarray(g_row), 0, 255).astype(np.uint8)
        self.pixels[row, :, 2] = np.clip(np.asarray(b_row), 0, 255).astype(np.uint8)
        self.rows_completed = max(self.rows_completed, row + 1)

    def set_row_gray(self, row: int, y_row) -> None:
        self.set_row_rgb(row, y_row, y_row, y_row)

    def save_png(self, path: str) -> bool:
        if not HAVE_NUMPY or self.pixels is None:
            return False
        try:
            try:
                from PIL import Image
                Image.fromarray(self.pixels, "RGB").save(path)
                self.last_save_was_png = True
                return True
            except ImportError:
                ppm_path = path.rsplit(".", 1)[0] + ".ppm"
                with open(ppm_path, "wb") as f:
                    f.write(f"P6\n{self.width} {self.height}\n255\n".encode())
                    f.write(self.pixels.tobytes())
                self.last_save_was_png = False
                return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

def _yuv_to_rgb(y, cr, cb):
    """Full-range BT.601 YCbCr -> RGB, as used by SSTV decoders.
    y/cr/cb are numpy arrays of 0..255 values (128 = zero chroma)."""
    y = np.asarray(y, dtype=np.float64)
    dr = np.asarray(cr, dtype=np.float64) - 128.0
    db = np.asarray(cb, dtype=np.float64) - 128.0
    r = y + 1.402 * dr
    g = y - 0.344136 * db - 0.714136 * dr
    b = y + 1.772 * db
    return (np.clip(r, 0, 255), np.clip(g, 0, 255), np.clip(b, 0, 255))


class SstvDecoder:
    """
    Runs VIS detection then per-mode line decoding against a live audio
    stream. Feed raw float audio samples via `feed()`; poll `process()`
    to advance decoding and `snapshot()` for the current partial image.
    """

    # How far either side of the expected position to hunt for each
    # line's sync pulse, and how much of that correction to apply per
    # line (damped, so one noisy line can't yank the whole raster).
    SYNC_SEARCH_SEC = 0.005
    SYNC_DAMPING = 0.5
    SYNC_MAX_MEAN_ERR_HZ = 220.0

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_seconds: float = 90.0):
        self.sample_rate = sample_rate
        self.vis_detector = VisDetector(sample_rate)
        self._lock = threading.Lock()
        self._buf = np.zeros(0, dtype=np.float32) if HAVE_NUMPY else None
        self._buf_chunks: List = []
        self._max_len = int(sample_rate * buffer_seconds)
        self._dropped = 0          # samples trimmed off the front so far

        self.state = "listening"   # listening | decoding | done
        self.mode: Optional[ModeSpec] = None
        self.image: Optional[SstvImageBuffer] = None
        self.current_row = 0
        self.last_error = ""
        self.sync_locked = False
        self.lines_without_sync = 0

        self._pos = 0.0            # absolute sample index of next line start
        self._reset_pair_state()

    # -- lifecycle -----------------------------------------------------
    def _reset_pair_state(self) -> None:
        """Robot 36 decodes chroma once per line pair, so the first line
        of a pair is held until its partner arrives."""
        self._pending_row: Optional[int] = None
        self._pending_y = None
        self._pending_chroma = None
        self._pending_parity: Optional[str] = None

    def reset(self) -> None:
        """Start listening for a fresh image."""
        with self._lock:
            self.state = "listening"
            self.mode = None
            self.image = None
            self.current_row = 0
            self.sync_locked = False
            self.lines_without_sync = 0
            self._pos = 0.0
            self._reset_pair_state()
            self._buf = np.zeros(0, dtype=np.float32)
            self._buf_chunks = []
            self._dropped = 0

    # -- audio input ---------------------------------------------------
    def feed(self, samples) -> None:
        """Append audio. Accepts a numpy array or any float sequence.
        Thread-safe; called from the capture thread."""
        if not HAVE_NUMPY:
            return
        arr = np.asarray(samples, dtype=np.float32).ravel()
        if arr.size == 0:
            return
        with self._lock:
            self._buf_chunks.append(arr)

    def _consolidate(self):
        """Merge queued chunks into the working buffer and trim history.
        Keeping incoming audio as a list of arrays and concatenating once
        per process() call avoids rebuilding a multi-million-element
        array on every callback."""
        if self._buf_chunks:
            self._buf = np.concatenate([self._buf] + self._buf_chunks)
            self._buf_chunks = []
        if self._buf.size > self._max_len:
            trim = self._buf.size - self._max_len
            # Never trim past the point we still need to decode from.
            keep_from = int(self._pos) - self._dropped
            if self.state == "decoding":
                trim = min(trim, max(0, keep_from - int(0.05 * self.sample_rate)))
            if trim > 0:
                self._buf = self._buf[trim:]
                self._dropped += trim
        return self._buf

    # -- main loop -----------------------------------------------------
    def process(self) -> None:
        """Call periodically (e.g. once per UI tick) to advance decoding."""
        if not HAVE_NUMPY:
            return
        with self._lock:
            buf = self._consolidate()
            state = self.state

        if buf.size == 0:
            return

        if state == "listening":
            found = self.vis_detector.try_detect(buf)
            if found is not None:
                mode, data_start = found
                with self._lock:
                    self.mode = mode
                    self.image = SstvImageBuffer(mode.line_pixels, mode.image_lines)
                    self.current_row = 0
                    self._pos = float(data_start + self._dropped)
                    self._reset_pair_state()
                    self.sync_locked = False
                    self.lines_without_sync = 0
                    self.state = "decoding"
            else:
                # Bound the search window, but keep enough history that a
                # header spanning two process() calls is still found.
                with self._lock:
                    keep = int(self.sample_rate * 3)
                    if self._buf.size > keep:
                        trim = self._buf.size - keep
                        self._buf = self._buf[trim:]
                        self._dropped += trim
            return

        if state == "decoding" and self.mode is not None and self.image is not None:
            self._decode_available_lines(buf)

    # -- line decoding -------------------------------------------------
    def _decode_available_lines(self, buf) -> None:
        mode = self.mode
        sr = self.sample_rate
        line_n = mode.line_seconds() * sr
        pad = int(self.SYNC_SEARCH_SEC * sr) + 16

        while self.current_row < mode.image_lines:
            start_abs = self._pos - self._dropped
            start = int(round(start_abs))
            end = int(round(start_abs + line_n))
            if start < 0:
                # History was trimmed out from under us (very long stall).
                self.last_error = "audio buffer underrun — decode position lost"
                self.state = "done"
                return
            if end > buf.size:
                # A line that is only a hair short is decoded anyway with
                # the tail edge-padded: an off-air capture that stops
                # mid-line, or one whose last line lands a few samples
                # past the final sample, would otherwise lose that line
                # entirely. Anything more than ~1% short waits for audio.
                shortfall = end - buf.size
                if shortfall > line_n * 0.01:
                    break

            lo = start - pad
            hi = end + pad
            clip_lo = max(0, lo)
            clip_hi = min(buf.size, hi)
            segment = buf[clip_lo:clip_hi]
            # Edge-pad rather than requiring real samples on both sides,
            # so the final line of a transmission still decodes when the
            # audio simply stops.
            pad_before = clip_lo - lo
            pad_after = hi - clip_hi
            if pad_before > 0 or pad_after > 0:
                segment = np.pad(segment, (pad_before, pad_after), mode="edge")
            line_offset = start - lo   # == pad

            self._decode_one_line(segment, line_offset, mode, self.current_row, sr)
            self.current_row += 1

        if self.current_row >= mode.image_lines:
            self._flush_pending()
            self.state = "done"

    def _align_sync(self, curve, line_offset: int, mode: ModeSpec, sr: int) -> int:
        """Find this line's 1200 Hz sync pulse near where it is expected
        and return the correction in samples (0 if not confidently
        found). Keeps the raster from shearing over hundreds of lines."""
        sync_n = int(mode.sync_seconds() * sr)
        if sync_n < 4:
            return 0
        expected = line_offset + int(mode.sync_offset_seconds() * sr)
        search = int(self.SYNC_SEARCH_SEC * sr)

        lo = max(0, expected - search)
        hi = min(curve.size - sync_n, expected + search)
        if hi <= lo:
            return 0

        # Mean |f - 1200| over a sync-length window, for every candidate
        # offset in the search range — vectorised via cumulative sums.
        err = np.abs(curve - SYNC_HZ)
        c = np.concatenate([[0.0], np.cumsum(err)])
        window_means = (c[lo + sync_n: hi + sync_n + 1] - c[lo: hi + 1]) / sync_n
        best_rel = int(np.argmin(window_means))
        best_err = float(window_means[best_rel])
        if best_err > self.SYNC_MAX_MEAN_ERR_HZ:
            self.sync_locked = False
            self.lines_without_sync += 1
            return 0

        self.sync_locked = True
        self.lines_without_sync = 0
        drift = (lo + best_rel) - expected
        return int(round(drift * self.SYNC_DAMPING))

    def _decode_one_line(self, segment, line_offset: int, mode: ModeSpec,
                         row: int, sr: int) -> None:
        curve = line_instantaneous_freq_curve(segment, sr)
        if curve is None or len(curve) == 0:
            return

        correction = self._align_sync(curve, line_offset, mode, sr)
        base = line_offset + correction
        # Carry the correction forward so the next line starts from the
        # re-acquired position rather than re-accumulating the same error.
        self._pos += mode.line_seconds() * sr + correction

        # Cumulative sum of the frequency curve, so a pixel's mean
        # frequency over any window is two lookups.
        cum = np.concatenate([[0.0], np.cumsum(curve)])

        def read_scan(seg: Segment, offset_sec: float):
            """Read `seg.samples` pixel values across a scan segment.

            Each pixel is the *mean* frequency over the middle of its
            own window, not a single sample at its centre: averaging
            cuts the phase-noise variance by the number of samples
            averaged, which on a real off-air signal is the difference
            between a clean picture and a speckled one. The outer ~15%
            of each window is skipped because it holds the frequency
            transition to the neighbouring pixel.
            """
            start = base + offset_sec * sr
            step = (seg.seconds * sr) / seg.samples
            edges = start + np.arange(seg.samples + 1) * step
            inset = step * 0.15
            lo = np.rint(edges[:-1] + inset).astype(np.int64)
            hi = np.rint(edges[1:] - inset).astype(np.int64)
            lo = np.clip(lo, 0, curve.size - 1)
            hi = np.clip(hi, 0, curve.size)
            # Windows too short to average (fast modes) collapse to a
            # single sample rather than an empty slice.
            hi = np.maximum(hi, lo + 1)
            means = (cum[hi] - cum[lo]) / (hi - lo)
            return _freq_array_to_values(means)

        def gap_tone(seg: Segment, offset_sec: float) -> float:
            """Mean frequency during a gap/separator segment."""
            lo = int(round(base + offset_sec * sr))
            hi = int(round(base + (offset_sec + seg.seconds) * sr))
            lo = max(0, min(curve.size - 1, lo))
            hi = max(lo + 1, min(curve.size, hi))
            return float(np.mean(curve[lo:hi]))

        channels = {}
        sep_tone = None
        for seg, offset in mode.segment_offsets():
            if seg.kind == "scan":
                channels[seg.channel] = read_scan(seg, offset)
            elif seg.kind == "gap" and seg.tone_hz == 0.0:
                sep_tone = gap_tone(seg, offset)

        w = mode.line_pixels

        if mode.layout == "rgb":
            self.image.set_row_rgb(row, channels.get("R"), channels.get("G"),
                                   channels.get("B"))
            return

        if mode.layout == "robot72":
            y = channels["Y"]
            cr = self._upsample(channels["RY"], w)
            cb = self._upsample(channels["BY"], w)
            r, g, b = _yuv_to_rgb(y, cr, cb)
            self.image.set_row_rgb(row, r, g, b)
            return

        if mode.layout == "robot36":
            self._decode_robot36_line(row, channels, sep_tone, w)

    @staticmethod
    def _upsample(values, width: int):
        """Nearest-neighbour upsample a half-width chroma scan to full
        image width."""
        values = np.asarray(values)
        if values.size == width:
            return values
        idx = np.clip((np.arange(width) * values.size) // width, 0, values.size - 1)
        return values[idx]

    def _decode_robot36_line(self, row: int, channels, sep_tone, w: int) -> None:
        """Robot 36 carries R-Y on even lines and B-Y on odd lines, each
        shared between the two lines of its pair. The separator tone
        (1500 Hz even / 2300 Hz odd) identifies which — more robust than
        assuming parity from the row counter, since decoding can start
        part-way into a transmission."""
        y = channels["Y"]
        chroma = self._upsample(channels["C"], w)

        if sep_tone is None:
            parity = "even" if row % 2 == 0 else "odd"
        else:
            d_even = abs(sep_tone - ROBOT_SEP_EVEN_HZ)
            d_odd = abs(sep_tone - ROBOT_SEP_ODD_HZ)
            parity = "even" if d_even <= d_odd else "odd"

        if parity == "even":
            self._flush_pending()
            # Show luminance straight away; colour lands when the odd
            # line of this pair arrives with the other chroma channel.
            self.image.set_row_gray(row, y)
            self._pending_row = row
            self._pending_y = y
            self._pending_chroma = chroma
            self._pending_parity = "even"
            return

        # Odd line: pair it with the held even line if we have one.
        if self._pending_parity == "even" and self._pending_row == row - 1:
            cr = self._pending_chroma
            cb = chroma
            r, g, b = _yuv_to_rgb(self._pending_y, cr, cb)
            self.image.set_row_rgb(self._pending_row, r, g, b)
            r, g, b = _yuv_to_rgb(y, cr, cb)
            self.image.set_row_rgb(row, r, g, b)
            self._reset_pair_state()
        else:
            # Unpaired odd line (decode started mid-pair): luminance only.
            self.image.set_row_gray(row, y)
            self._reset_pair_state()

    def _flush_pending(self) -> None:
        """Emit a held Robot 36 even line as luminance if its partner
        never arrived, so no row is silently left blank."""
        if self._pending_parity == "even" and self._pending_y is not None:
            self.image.set_row_gray(self._pending_row, self._pending_y)
        self._reset_pair_state()

    # -- status --------------------------------------------------------
    def snapshot(self):
        return self.state, self.mode, self.image, self.current_row

    def status_text(self) -> str:
        if self.mode is None:
            return "listening for VIS header"
        pct = int(100 * self.current_row / self.mode.image_lines) if self.mode.image_lines else 0
        lock = "SYNC LOCK" if self.sync_locked else "no sync lock"
        return f"{self.mode.name}  {self.current_row}/{self.mode.image_lines} ({pct}%)  [{lock}]"


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------

class AudioCaptureController:
    """
    Captures audio for SSTV decoding via one of two paths:
      - rtl_fm piped from an SDR tuned to an SSTV frequency.
      - the system's default input device via `sounddevice`, for feeding
        audio from an external radio's speaker/headphone output.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.rtl_fm_proc: Optional[subprocess.Popen] = None
        self._stream = None
        self._stop_flag = threading.Event()
        self.mode = "none"  # "rtl_fm" | "mic" | "none"
        self.last_error = ""

    def start_rtl_fm(self, freq_hz: int, decoder: SstvDecoder) -> bool:
        self.stop()
        self._stop_flag.clear()
        cmd = [
            "rtl_fm", "-f", str(freq_hz), "-M", "fm",
            "-s", str(self.sample_rate), "-l", "0", "-E", "deemp", "-",
        ]
        try:
            self.rtl_fm_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.last_error = "rtl_fm not found on PATH (package: rtl-sdr)"
            return False
        except Exception as e:
            self.last_error = f"rtl_fm: {e}"
            return False

        self.mode = "rtl_fm"

        def _pump():
            proc = self.rtl_fm_proc
            bytes_per_sample = 2  # rtl_fm outputs signed 16-bit PCM
            chunk_samples = 4096
            while proc is not None and proc.poll() is None and not self._stop_flag.is_set():
                raw = proc.stdout.read(chunk_samples * bytes_per_sample)
                if not raw:
                    break
                count = len(raw) // bytes_per_sample
                if count == 0:
                    continue
                if HAVE_NUMPY:
                    arr = np.frombuffer(raw[: count * bytes_per_sample],
                                        dtype="<i2").astype(np.float32) / 32768.0
                    decoder.feed(arr)
                else:
                    ints = struct.unpack(f"<{count}h", raw[: count * bytes_per_sample])
                    decoder.feed([v / 32768.0 for v in ints])

        threading.Thread(target=_pump, daemon=True).start()
        return True

    def start_mic(self, decoder: SstvDecoder) -> bool:
        self.stop()
        self._stop_flag.clear()
        try:
            import sounddevice as sd
        except ImportError:
            self.last_error = "sounddevice not installed (apt: python3-sounddevice)"
            return False

        try:
            def _callback(indata, frames, time_info, status):
                decoder.feed(indata[:, 0])

            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=1, callback=_callback
            )
            self._stream.start()
            self.mode = "mic"
            return True
        except Exception as e:
            self.last_error = f"microphone input: {e}"
            return False

    def start_wav(self, path: str, decoder: SstvDecoder) -> bool:
        """Decode a recorded .wav file — useful for testing without
        hardware, and for recovering an image from an off-air recording."""
        self.stop()
        self._stop_flag.clear()
        try:
            import wave
            with wave.open(path, "rb") as wf:
                channels = wf.getnchannels()
                width = wf.getsampwidth()
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
        except Exception as e:
            self.last_error = f"cannot read {os.path.basename(path)}: {e}"
            return False

        if not HAVE_NUMPY:
            self.last_error = "numpy required to decode a wav file"
            return False
        dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}.get(width)
        if dtype is None:
            self.last_error = f"unsupported wav sample width ({width * 8}-bit)"
            return False
        data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
        if width == 1:
            data = (data - 128.0) / 128.0
        else:
            data /= float(1 << (8 * width - 1))
        if channels > 1:
            data = data.reshape(-1, channels)[:, 0]
        if rate != self.sample_rate and data.size:
            # Linear resample to the decoder's rate.
            n_out = int(data.size * self.sample_rate / rate)
            data = np.interp(np.linspace(0, data.size - 1, n_out),
                             np.arange(data.size), data).astype(np.float32)
        self.mode = "wav"
        decoder.feed(data)
        return True

    def stop(self) -> None:
        self._stop_flag.set()
        if self.rtl_fm_proc is not None:
            try:
                self.rtl_fm_proc.terminate()
                try:
                    self.rtl_fm_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.rtl_fm_proc.kill()
            except Exception:
                pass
            self.rtl_fm_proc = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self.mode = "none"


# ---------------------------------------------------------------------------
# Synthetic encoder (self-test / offline verification)
# ---------------------------------------------------------------------------

def encode_image(rgb, mode: ModeSpec, sample_rate: int = SAMPLE_RATE,
                 noise: float = 0.0, with_header: bool = True,
                 leading_silence_sec: float = 0.0, seed: int = 0):
    """
    Render an RGB image (numpy HxWx3 uint8) to SSTV audio for `mode`,
    walking the same segment table the decoder reads. Used by
    tools/sstv_selftest.py to verify decoding end-to-end without
    hardware, and handy for generating test audio.

    Phase is kept continuous across segments, as a real modulator does.
    """
    if not HAVE_NUMPY:
        raise RuntimeError("numpy required")

    rng = np.random.default_rng(seed)
    sr = sample_rate
    freqs: List[np.ndarray] = []
    # Segment lengths are derived from a running float time base rather
    # than rounding each duration independently — rounding per segment
    # loses a sample or so per line, which over a few hundred lines
    # shortens the transmission enough to truncate the last line.
    clock = {"t": 0.0, "n": 0}

    def _advance(seconds: float) -> int:
        clock["t"] += seconds
        target = int(round(clock["t"] * sr))
        n = target - clock["n"]
        clock["n"] = target
        return max(0, n)

    def tone(hz: float, seconds: float) -> None:
        n = _advance(seconds)
        if n > 0:
            freqs.append(np.full(n, float(hz)))

    def scan(values, seconds: float) -> None:
        """values: 0..255 array, rendered across `seconds`."""
        n = _advance(seconds)
        if n <= 0:
            return
        v = np.asarray(values, dtype=np.float64)
        idx = np.clip((np.arange(n) * v.size) // n, 0, v.size - 1)
        freqs.append(BLACK_HZ + (v[idx] / 255.0) * (WHITE_HZ - BLACK_HZ))

    if leading_silence_sec > 0:
        tone(700.0, leading_silence_sec)

    if with_header:
        tone(VIS_LEADER_HZ, VIS_LEADER_SEC)
        tone(VIS_BREAK_HZ, VIS_BREAK_SEC)
        tone(VIS_LEADER_HZ, VIS_LEADER_SEC)
        tone(VIS_START_HZ, VIS_START_SEC)
        code = mode.vis_code
        ones = 0
        for i in range(7):
            bit = (code >> i) & 1
            ones += bit
            tone(VIS_BIT1_HZ if bit else VIS_BIT0_HZ, VIS_BIT_SEC)
        tone(VIS_BIT1_HZ if ones % 2 else VIS_BIT0_HZ, VIS_BIT_SEC)  # even parity
        tone(VIS_STOP_HZ, VIS_STOP_SEC)

    h = min(mode.image_lines, rgb.shape[0])
    w = mode.line_pixels

    def row_rgb(r):
        px = rgb[min(r, rgb.shape[0] - 1)]
        if px.shape[0] != w:
            idx = np.clip((np.arange(w) * px.shape[0]) // w, 0, px.shape[0] - 1)
            px = px[idx]
        return px[:, 0].astype(np.float64), px[:, 1].astype(np.float64), px[:, 2].astype(np.float64)

    def row_ycc(r):
        rr, gg, bb = row_rgb(r)
        y = 0.299 * rr + 0.587 * gg + 0.114 * bb
        cb = 128.0 - 0.168736 * rr - 0.331264 * gg + 0.5 * bb
        cr = 128.0 + 0.5 * rr - 0.418688 * gg - 0.081312 * bb
        return y, cr, cb

    def halve(v):
        return v.reshape(-1, 2).mean(axis=1) if v.size % 2 == 0 else v[::2]

    for row in range(h):
        if mode.layout == "rgb":
            rr, gg, bb = row_rgb(row)
            chan = {"R": rr, "G": gg, "B": bb}
            for seg in mode.segments:
                if seg.kind == "scan":
                    scan(chan[seg.channel], seg.seconds)
                else:
                    tone(seg.tone_hz or SYNC_HZ, seg.seconds)
        elif mode.layout == "robot72":
            y, cr, cb = row_ycc(row)
            chan = {"Y": y, "RY": halve(cr), "BY": halve(cb)}
            for seg in mode.segments:
                if seg.kind == "scan":
                    scan(chan[seg.channel], seg.seconds)
                else:
                    tone(seg.tone_hz or SYNC_HZ, seg.seconds)
        else:  # robot36
            y, cr, cb = row_ycc(row)
            even = (row % 2 == 0)
            # Chroma is averaged over the line pair in the real spec.
            partner = min(row + 1, h - 1) if even else max(row - 1, 0)
            _, cr2, cb2 = row_ycc(partner)
            chroma = halve((cr + cr2) / 2.0) if even else halve((cb + cb2) / 2.0)
            for seg in mode.segments:
                if seg.kind == "scan":
                    scan(y if seg.channel == "Y" else chroma, seg.seconds)
                elif seg.tone_hz == 0.0:
                    tone(ROBOT_SEP_EVEN_HZ if even else ROBOT_SEP_ODD_HZ, seg.seconds)
                else:
                    tone(seg.tone_hz, seg.seconds)

    freq_track = np.concatenate(freqs) if freqs else np.zeros(0)
    phase = np.cumsum(2.0 * np.pi * freq_track / sr)
    audio = np.sin(phase).astype(np.float32)
    if noise > 0:
        audio = audio + rng.normal(0.0, noise, audio.size).astype(np.float32)
    return audio


# ---------------------------------------------------------------------------
# Curses UI (standalone CLI mode)
# ---------------------------------------------------------------------------

_PREVIEW_SHADES = " .:-=+*#%@"


def _render_ascii_preview(win, image: SstvImageBuffer, rows_done: int) -> None:
    """Coarse ASCII/grayscale preview of the image-so-far."""
    win_h, win_w = win.getmaxyx()
    win_h = max(1, win_h - 2)
    win_w = max(1, win_w - 2)
    if not HAVE_NUMPY or image.pixels is None or rows_done == 0:
        return

    for out_row in range(win_h):
        src_row = int(out_row * rows_done / win_h)
        if src_row >= rows_done:
            break
        for out_col in range(win_w):
            src_col = int(out_col * image.width / win_w)
            r, g, b = image.pixels[src_row, src_col]
            lum = int(0.299 * r + 0.587 * g + 0.114 * b)
            shade = _PREVIEW_SHADES[min(len(_PREVIEW_SHADES) - 1,
                                        lum * len(_PREVIEW_SHADES) // 256)]
            try:
                win.addch(out_row + 1, out_col + 1, shade)
            except curses.error:
                pass


def run(stdscr) -> None:
    """
    Keys: [Q] back to menu   [M] switch input source (rtl_fm SDR / mic)
          [F] set rtl_fm frequency (cycles common SSTV calling freqs)
          [R] reset/restart listening for a new VIS header
          [S] save current image to captures/sstv/
    """
    curses.curs_set(0)
    radar_ui.init_colors()

    capture_dir_error = ""
    try:
        os.makedirs(CAPTURE_DIR_DEFAULT, exist_ok=True)
    except OSError as e:
        capture_dir_error = f"Cannot create captures dir: {e}"

    decoder = SstvDecoder()
    capture = AudioCaptureController()

    sstv_freqs = [
        ("14.230 MHz (HF SSTV calling)", 14_230_000),
        ("145.800 MHz (ISS SSTV)", 145_800_000),
        ("144.500 MHz (2m SSTV calling, IARU R1)", 144_500_000),
    ]
    freq_idx = 0
    input_source = "rtl_fm"  # or "mic"

    def _start_capture() -> None:
        if input_source == "rtl_fm":
            capture.start_rtl_fm(sstv_freqs[freq_idx][1], decoder)
        else:
            capture.start_mic(decoder)

    if not HAVE_NUMPY:
        capture.last_error = "numpy not installed — SSTV decoding unavailable"
    else:
        _start_capture()

    height, width = stdscr.getmaxyx()
    preview_win = curses.newwin(height - 6, width, 0, 0)
    status_win = curses.newwin(6, width, height - 6, 0)

    stdscr.nodelay(True)
    stdscr.timeout(150)

    saved_message = ""

    try:
        while True:
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            elif key in (ord("m"), ord("M")):
                input_source = "mic" if input_source == "rtl_fm" else "rtl_fm"
                _start_capture()
            elif key in (ord("f"), ord("F")):
                freq_idx = (freq_idx + 1) % len(sstv_freqs)
                if input_source == "rtl_fm":
                    _start_capture()
            elif key in (ord("r"), ord("R")):
                decoder.reset()
                saved_message = ""
            elif key in (ord("s"), ord("S")):
                if capture_dir_error:
                    saved_message = capture_dir_error
                elif decoder.image is not None:
                    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                    mode_slug = (decoder.mode.name.lower().replace(" ", "")
                                 if decoder.mode else "sstv")
                    path = os.path.join(CAPTURE_DIR_DEFAULT, f"{mode_slug}-{ts}.png")
                    if decoder.image.save_png(path):
                        actual = path if decoder.image.last_save_was_png \
                            else path.rsplit(".", 1)[0] + ".ppm"
                        saved_message = f"Saved: {os.path.basename(actual)}"
                    else:
                        saved_message = "Save FAILED — check captures/sstv/ permissions"
                else:
                    saved_message = "Nothing to save yet"

            decoder.process()
            state, mode, image, current_row = decoder.snapshot()

            preview_win.erase()
            preview_win.border()
            if image is not None:
                _render_ascii_preview(preview_win, image, current_row)
            else:
                try:
                    preview_win.addstr(2, 2, "Listening for VIS header (1900Hz leader tone)...")
                except curses.error:
                    pass
            preview_win.noutrefresh()

            status_win.erase()
            status_win.border()
            src_label = (f"rtl_fm @ {sstv_freqs[freq_idx][0]}"
                         if input_source == "rtl_fm" else "microphone")
            try:
                status_win.addstr(1, 2, f" Input: {src_label} ",
                                  curses.color_pair(radar_ui.PAIR_CYAN))
                if mode is not None:
                    status_win.addstr(2, 2, " " + decoder.status_text() + " ",
                                      curses.color_pair(radar_ui.PAIR_GREEN))
                    if state == "done":
                        status_win.addstr(3, 2, " DECODE COMPLETE — press S to save ",
                                          curses.color_pair(radar_ui.PAIR_YELLOW) | curses.A_BOLD)
                elif capture.last_error:
                    status_win.addstr(2, 2, f" {capture.last_error[:width-4]} ",
                                      curses.color_pair(radar_ui.PAIR_RED))
                if saved_message:
                    color = radar_ui.PAIR_RED if "FAILED" in saved_message else radar_ui.PAIR_GREEN
                    status_win.addstr(4, 2, f" {saved_message[:width-4]} ",
                                      curses.color_pair(color))
                status_win.addstr(5, 2,
                                  " [M] Input  [F] Freq  [R] Reset  [S] Save  [Q] Quit ",
                                  curses.color_pair(radar_ui.PAIR_CYAN))
            except curses.error:
                pass
            status_win.noutrefresh()

            curses.doupdate()
    finally:
        capture.stop()
