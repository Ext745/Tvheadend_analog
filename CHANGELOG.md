# Changelog

## Unreleased

### Fixes

- **FM channels on a second card without FM**: `FM_STATIONS` set in
  `/etc/ntsc-tvh.conf` or the environment also reached `--card` cards, so a raw
  card like the 800e got radio channels it can't play in its playlist, and its
  `doctor` failed. A card whose driver has no radio node now leaves them out (§9b).
- **`scan`: snow counted as a channel now and then** (800e). Snow's noise score
  wanders and one grab in a scan could dip under `SCAN_NOISE_MAX`. A channel that
  looks like a picture is now grabbed again and must pass both times (§7).

### Documentation

- **USB path advice corrected** (§2, §11): firmware2 failures came from sharing the
  tuner's USB bandwidth (an SDR on the same upstream port), not from the hub or
  the root ports, and they don't always repeat across reboots.
- **§11: a hung HVR-1950 encoder** (empty streams, firmware2 failing on reload) only
  recovers after the card loses power for ~20 s; module reloads and USB
  re-authorization don't help. Stop TVHeadend before replugging a tuner.
- **§11: silent or muffled TV audio at a stream start** on the HVR-1950: the stock
  driver sometimes misdetects SAP on a mono channel; a channel change clears it.
- **`encoder appears to be stuck`**: updated how often it shows up (about one in ten
  TV starts, most switches between FM radio and TV).
- **§9b FM radio**: stereo hiss on weak stations, fixed de-emphasis (75 µs with the
  stereo firmware, 50 µs without), and the status of FM seek.

## 0.1.0 (2026-10-22)

First public release. The README section numbers (§6, §9b, ...) refer to
[README.md](README.md).

### New and changed

- **FM radio channels** (§9b): `fm:MHZ` channel IDs and `FM_STATIONS` add FM
  stations to the playlist as audio-only radio services, on cards with a radio
  node (the HVR-1950 with the pvrusb2 FM patches). Same tuner lock and DVB guard
  as analog TV.
- **`tvh-setup` waits for new playlist channels.** TVHeadend re-reads a `file://`
  playlist about once a minute; run straight after a rescan (or new FM stations),
  `tvh-setup` used to report everything done while the new channels were still
  missing. It now waits for TVHeadend to read them, then scans and maps them.
- **Inline comments in the config file work.** `KEY=value   # note` used to keep
  the comment as part of the value, so uncommenting a line of the example config
  broke it (e.g. `SCAN_THRESHOLD` crashed `scan`).
- **The `video`/`both` scan methods honour `CAPTURE_VIA_CAT`.** The picture probe
  now reads the HW-MPEG node through `cat` like `stream` does, instead of having
  ffmpeg open it directly (empty on some ffmpeg builds, so every channel looked flat).
- **`ntsc-tvh signal input:composite`** selects the input and says there is no RF
  signal to measure, instead of crashing.
- **Raw cards get their own audio.** ALSA auto-detect used to fall back to the
  first sound card, which is usually the host's built-in audio. A Pinnacle 800e
  streamed the PC's mic input while `doctor` said "all good". It now matches the
  card via sysfs (see §11).
- **Tuning is verified.** After `--set-freq`, the tool reads the frequency back.
  A tuner that didn't move (e.g. missing xc3028 firmware) is now an error, not
  "0% signal". `scan` aborts instead of overwriting the playlist with nothing.
  Drivers that can't report the frequency are trusted as before.
- **`stream` supervises ffmpeg instead of exec'ing it.** ntsc-tvh stays the
  process TVHeadend started and runs `cat`/ffmpeg as children. On SIGTERM,
  SIGINT or SIGHUP it gives ffmpeg `STOP_GRACE` seconds, then SIGKILLs it.
  ffmpeg 6.1 (the linuxserver image) never exits on SIGTERM with an em28xx ALSA
  input: once video stops, the audio read blocks forever. Children are tied to
  ntsc-tvh with `PR_SET_PDEATHSIG`, so TVHeadend's default SIGKILL still takes
  them down at once. ffmpeg still writes straight to TVHeadend's pipe and holds
  the tuner lock until it is gone.
- **The video scan no longer mistakes snow for a channel.** It used to judge
  contrast alone, so on tuners that show snow (the 800e) every empty channel
  was "active". It now also checks spatial noise and frame-to-frame motion
  (`SCAN_NOISE_MAX`, `SCAN_MOTION_MAX`; see §7).
- **Raw cards produce standard MPEG-2 (Main profile, 4:2:0).** Raw cards capture
  4:2:2 (YUYV), and the software encode kept it, producing MPEG-2 *4:2:2 profile*:
  a studio format that GPU decoders (VAAPI) and many TVs and set-top boxes can't
  decode. It now converts to 4:2:0 like broadcast MPEG-2.
- **Raw cards keep a 4:3 picture.** The software MPEG-2 encode labelled 720×480
  analog as square pixels (3:2), so players showed it about 12% too wide. It is
  now written as 4:3 (`RAW_ASPECT`, `16:9` for anamorphic sources).
- **Hybrid-card guard (`DVB_GUARD`).** On a card whose analog and digital sides
  share one tuner, `tune`, `signal`, `stream` and every channel of `scan` refuse
  to start while the DVB side is busy, instead of wedging the card (see §11).
- **Stream profiles for analog and digital clients** (`examples/tvh-stream-profiles`, §6b):
  H.264/AAC for IPTV apps, browsers and phones, plus H.265, AV1, an upscaled
  59.94p H.264, VAAPI (GPU) versions at about a tenth of the CPU, and H.264 DVR
  profiles for recording analog at about a third of the size. The heavier
  profiles are only created if a test encode shows the machine (and GPU) can keep
  up.
- **Quieter TVHeadend log.** The `ntsc-tvh: streaming …` status line is only
  printed when stderr is a terminal. TVHeadend logs every stderr line from a pipe
  at ERROR level, so each channel start used to show up as an error. Real errors
  from ntsc-tvh and ffmpeg are still logged.
- **Channels start ~4 s faster on hardware-MPEG cards.** ffmpeg analysed ~5 s of the
  HVR-1950's program stream before writing anything, so every channel change (and every
  `video`-method scan probe) waited ~6.5 s. It now analyses at most 1 s / 500 KB
  (`PROBE_SECONDS`, `PROBE_SIZE`): first byte in ~1.1 s on the HVR-1950, with the same
  MPEG-2 720×480 4:3 + MP2 streams detected.
- **Raw cards no longer crash without `alsa-utils`.** When sysfs didn't name the
  card's audio and `arecord` wasn't installed, `stream` and `doctor` died with a
  traceback; they now carry on without audio.
- **`list --tables` with a wrong name** prints `error: unknown table ...` instead
  of a Python traceback.
- **`examples/analog-check`** checks one channel that carries a real analog picture
  from end to end, and leaves a report of pictures and fixture files (§14).
- **`doctor` captures a few frames.** It used to say "all good" for a card whose
  encoder sent nothing (seen in testing: firmware2 failing on an xHCI port). It now captures
  four frames like `stream` does and fails if none arrive, or reports picture / flat
  screen / snow.
- **§6c: setting up the ATSC network in TVHeadend.** Use only the current US mux list,
  never two lists in one network, disable failed muxes, PSIP (not EIT) for the guide,
  and a 120 s OTA timeout. Learned from a test setup whose guide stayed empty.
- **More than one card (`--card NAME`).** A second tuner gets its own config file,
  state, playlist, tuner lock, timer and TVHeadend network, so both cards can stream
  at once (§10). Setups with one card are unchanged, URLs included.
- **The 800e reports signal.** Its decoder only reports a lock while capturing, so
  idle readings were always 0 %, even on a perfect picture. Raw cards' signal is now
  read during a short capture: 44 % on a test pattern, 0 % on empty channels.
  `auto` still decides "skip 0 % channels" from idle readings only, so it can't skip
  real channels on such a card.
- **New default scan method `auto`.** `signal` (the old default) found nothing on
  cards that read 0 % idle, like the 800e; `video` is right everywhere but grabs
  frames on every channel. `auto` judges the picture like `video` and skips the frame
  grab on 0 % channels once the card has shown it reports signal.
- **Stream profiles start ~4 s sooner.** Each spawn profile's ffmpeg analysed ~5 s of
  TVHeadend's stream before encoding. Bounded to 1 s (5 MB cap): through TVHeadend on the
  HVR-1950, analog profiles now send their first byte in 0.6–1.0 s (was ~4.6 s) and
  `digital-h264-aac-mpegts` in ~1.4 s. Re-run `tvh-stream-profiles` to update existing
  profiles.
- **`ntsc-tvh tvh-setup`** sets the analog network up in TVHeadend in one command
  (network, EPG off, scan, channels), with `--dry-run`, `--status` and `--teardown`
  ([§6](README.md#one-command-setup-ntsc-tvh-tvh-setup)).
- **`ntsc-tvh tvh-setup --atsc`** sets TVHeadend's own ATSC network up the way §6c
  describes: one mux list, tuner, PSIP guide, dead and duplicate muxes off, channels.
- **The playlist turns TVHeadend's OTA EPG grabber off** for every analog channel
  (`tvh-epg="disable"`), so new channels from a rescan no longer arrive with it on.

### First end-to-end bring-up (a live HVR-1950 in Docker, `linuxserver/tvheadend`)

Changes made after the first full bring-up:

- **`CAPTURE_VIA_CAT` now defaults to `1`.** On several ffmpeg builds, letting
  ffmpeg open the MPEG node directly (`-f mpeg -i /dev/video0`) yields an empty
  stream; feeding it through `cat` is reliable. The cat path is now the default
  for hardware-MPEG cards.
- **Stream cat-path rewritten.** Instead of `/bin/sh -c "cat | exec ffmpeg"`
  (whose stdout wiring and signal delivery are shell-dependent), `cat` is spawned
  as a child, its output is dup'd onto ffmpeg's stdin, and ffmpeg is `exec`'d in
  place — so ffmpeg owns the PID (TVHeadend's SIGTERM reaches it directly, tuner
  releases cleanly) and inherits the real stdout pipe to TVHeadend.
- **`LOCK_FILE` default moved** from `/run/lock/ntsc-tvh.lock` to
  `/var/lib/ntsc-tvh/ntsc-tvh.lock`, so the non-root container user owns the
  directory it lives in (`/run/lock` is commonly root-only inside containers).
- **`SCAN_LOCK_WAIT` default raised** from `0` to `5` seconds, so a stream that
  briefly loses the single-tuner race (e.g. to a background scan) waits rather
  than failing instantly.
- **New §3b Docker install** and a substantially expanded **§11 Troubleshooting**
  documenting the firmware2/xHCI requirement, the analog/DVB mutual exclusion,
  the lock permission/ownership trap, the M3U-URL trailing-newline trap, and the
  EPG-vs-scan contention — all encountered and resolved during the bring-up.
