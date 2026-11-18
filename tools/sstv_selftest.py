#!/usr/bin/env python3
"""
tools/sstv_selftest.py — End-to-end SSTV decoder verification, no radio
hardware required.

Renders a known test image to SSTV audio with the module's own encoder
(which walks the same per-mode segment table the decoder reads), pushes
that audio through the real decoder exactly as a live capture would,
and reports the per-pixel error against the original image.

    python3 tools/sstv_selftest.py              # all modes
    python3 tools/sstv_selftest.py "Robot 36"   # one mode
    python3 tools/sstv_selftest.py --noisy      # add noise + offset

Exit status is non-zero if any mode fails its error budget, so this
doubles as a regression test.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")))

from modules import sstv  # noqa: E402

# Mean absolute error budget per channel (0-255), clean signal. Chroma
# is transmitted at half resolution and averaged over line pairs in the
# Robot modes, so a colour test pattern can never round-trip exactly.
ERROR_BUDGET = {
    "Martin 1": 3.0,
    "Martin 2": 3.0,
    "Scottie 1": 3.0,
    "Scottie 2": 3.0,
    "Scottie DX": 2.0,
    "Robot 36": 5.0,
    "Robot 72": 3.0,
}

# Budgets for --noisy (signal + 6% RMS noise, ~24 dB SNR, and a header
# that starts part-way into the buffer). These scale with how short each
# mode's pixel window is: fewer samples per pixel means less averaging
# to suppress the phase noise an FM discriminator produces, so Martin 2
# and Scottie 2 are legitimately noisier than Martin 1 / Scottie DX.
NOISY_BUDGET = {
    "Martin 1": 8.0,
    "Martin 2": 14.0,
    "Scottie 1": 8.0,
    "Scottie 2": 12.0,
    "Scottie DX": 4.0,
    "Robot 36": 20.0,
    "Robot 72": 13.0,
}


def make_test_image(width: int, height: int) -> np.ndarray:
    """A pattern that exercises luminance ramps, saturated primaries and
    hard edges — the things a mis-timed decoder gets wrong."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    x = np.linspace(0, 255, width)
    for row in range(height):
        band = int(row * 8 / height)
        if band == 0:                      # horizontal grey ramp
            img[row, :, :] = x[:, None]
        elif band == 1:                    # vertical grey ramp
            img[row, :, :] = int(row * 255 / height)
        elif band == 2:                    # colour bars
            bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
                    (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
            for i, c in enumerate(bars):
                img[row, i * width // 8:(i + 1) * width // 8] = c
        elif band == 3:                    # red ramp
            img[row, :, 0] = x
        elif band == 4:                    # green ramp
            img[row, :, 1] = x
        elif band == 5:                    # blue ramp
            img[row, :, 2] = x
        elif band == 6:                    # vertical stripes (edges)
            img[row, :, :] = np.where((np.arange(width) // 16) % 2 == 0, 230, 25)[:, None]
        else:                              # flat mid grey with a marker
            img[row, :, :] = 110
            img[row, width // 2 - 4:width // 2 + 4] = (255, 255, 255)
    return img


def run_mode(mode: sstv.ModeSpec, noisy: bool, chunk_sec: float = 0.25) -> dict:
    src = make_test_image(mode.line_pixels, mode.image_lines)
    audio = sstv.encode_image(
        src, mode,
        noise=0.06 if noisy else 0.0,
        leading_silence_sec=0.4 if noisy else 0.0,
    )

    decoder = sstv.SstvDecoder()
    # Feed in realistic chunks, interleaving process() calls the way the
    # GUI's poll timer does — this is what a live capture looks like.
    chunk = int(chunk_sec * sstv.SAMPLE_RATE)
    for i in range(0, audio.size, chunk):
        decoder.feed(audio[i:i + chunk])
        decoder.process()
    for _ in range(4):           # drain
        decoder.process()

    state, got_mode, image, rows = decoder.snapshot()
    result = {
        "mode": mode.name,
        "state": state,
        "detected": got_mode.name if got_mode else None,
        "rows": rows,
        "expected_rows": mode.image_lines,
        "line_ms": mode.line_seconds() * 1000.0,
        "sync_locked": decoder.sync_locked,
    }
    if image is None or got_mode is None:
        result["ok"] = False
        result["reason"] = "no VIS lock"
        return result

    decoded = image.pixels.astype(np.float64)
    original = src.astype(np.float64)
    # Robot 36's first and last rows have no pair partner to average
    # chroma with; skip a couple of edge rows in the comparison.
    sl = slice(2, mode.image_lines - 2)
    diff = np.abs(decoded[sl] - original[sl])
    result["mae"] = float(diff.mean())
    result["p99"] = float(np.percentile(diff, 99))
    # Luminance-only error isolates timing problems from chroma
    # subsampling loss.
    lum_d = decoded[sl].mean(axis=2)
    lum_o = original[sl].mean(axis=2)
    result["lum_mae"] = float(np.abs(lum_d - lum_o).mean())

    budget = (NOISY_BUDGET if noisy else ERROR_BUDGET).get(mode.name, 15.0)
    result["budget"] = budget
    result["ok"] = (
        state == "done"
        and got_mode.name == mode.name
        and rows == mode.image_lines
        and result["mae"] <= budget
    )
    return result


def main(argv) -> int:
    noisy = "--noisy" in argv
    names = [a for a in argv[1:] if not a.startswith("--")]
    modes = [m for m in sstv.MODE_SPECS if not names or m.name in names]
    if not modes:
        print(f"No such mode. Known: {', '.join(m.name for m in sstv.MODE_SPECS)}")
        return 2

    print(f"SSTV decoder self-test  (sample rate {sstv.SAMPLE_RATE} Hz, "
          f"{'noisy' if noisy else 'clean'} signal)")
    print(f"{'MODE':<12}{'LINE ms':>9}{'VIS':>6}{'ROWS':>10}{'MAE':>8}"
          f"{'LUM MAE':>9}{'BUDGET':>8}  RESULT")
    print("-" * 78)

    failures = 0
    for mode in modes:
        r = run_mode(mode, noisy)
        if not r["ok"]:
            failures += 1
        vis = "ok" if r.get("detected") == mode.name else "FAIL"
        rows = f"{r['rows']}/{r['expected_rows']}"
        mae = f"{r['mae']:.2f}" if "mae" in r else "--"
        lmae = f"{r['lum_mae']:.2f}" if "lum_mae" in r else "--"
        budget = f"{r.get('budget', 0):.1f}"
        verdict = "PASS" if r["ok"] else f"FAIL ({r.get('reason', 'error too high')})"
        print(f"{mode.name:<12}{r['line_ms']:>9.2f}{vis:>6}{rows:>10}"
              f"{mae:>8}{lmae:>9}{budget:>8}  {verdict}")

    print("-" * 78)
    print("All modes passed." if failures == 0 else f"{failures} mode(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
