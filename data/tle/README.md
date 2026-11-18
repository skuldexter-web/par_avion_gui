# Bundled orbital elements

`iss.tle` is a two-line element set for the International Space Station
(NORAD catalogue number 25544), published by the US Space Force via
AMSAT's public keplerian bulletin. Element sets are factual orbital
measurements, are published without licence or API key, and both lines
here pass the standard TLE checksum.

**Epoch: 2026-321.20556304 (17 November 2026, 04:56 UTC).**

This file exists so PAR AVION can track the ISS on a machine with no
internet connection at all. It is a *fallback*, not a substitute for
fresh elements: TLE accuracy decays as the real orbit drifts from the
modelled one, and for something in low Earth orbit like the ISS that is
roughly a few kilometres of along-track error per day — about a minute
of pass-time error after a couple of weeks.

PAR AVION refreshes automatically when it can reach the network, and
caches what it fetches in `~/.par_avion/cache/iss.tle`, which takes
priority over this file. To refresh by hand:

    python3 tools/update_tle.py

Or paste a fresh element set straight into the ISS tab's "Manual TLE"
box — that path needs no network at all. Sources used, none of which
require an account or key:

  * https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE
  * https://live.ariss.org/iss.txt
  * https://www.amsat.org/tle/current/nasabare.txt
