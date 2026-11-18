# Vendored: dump1090

This directory contains the complete, unmodified source of **dump1090**,
a Mode S / ADS-B decoder for RTL-SDR devices by Salvatore Sanfilippo
(antirez), from <https://github.com/antirez/dump1090>.

It is here so PAR AVION's Airplanes mode works on a machine where
dump1090 was never installed, and so it stops depending on whether a
given distribution happens to package it. Kali's repositories, for
instance, carry no `dump1090-mutability` candidate, which is exactly the
failure this removes.

## Licence

BSD 3-Clause — see `LICENSE`. Redistribution in source and binary form
is permitted provided the copyright notice and conditions are retained,
which is why `LICENSE` and the original file headers are kept intact and
unmodified. PAR AVION's own licence does not apply to these files.

## What was changed

Nothing. The files are byte-for-byte as published upstream:

    dump1090.c   anet.c   anet.h   Makefile   LICENSE
    UPSTREAM-README.md   (renamed from README.md so it doesn't shadow
                          PAR AVION's own README)

## Building

`install.sh` builds this automatically. To do it by hand:

    cd third_party/dump1090
    make

That produces a `dump1090` binary in this directory. It needs
`librtlsdr-dev` and `pkg-config`, both of which `install.sh` installs.

PAR AVION looks for the binary here **first**, before falling back to a
`dump1090`, `dump1090-fa` or `dump1090-mutability` found on `PATH`, so a
locally built copy always wins over whatever else is on the system.

## Why this build rather than FlightAware's

FlightAware's fork has more features (and a different licence), but it
is a much larger tree with more build dependencies. This one is four
files, builds in a couple of seconds on a Raspberry Pi, and implements
everything PAR AVION asks of it: `--net --raw`, which is the TCP feed on
port 30002 that the Airplanes mode reads.
