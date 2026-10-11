# ntsc-tvh — Analog NTSC in TVHeadend via pipe:// muxes

A modern replacement for the old [ivtv-utils pipe-mux script](https://tvheadend.org/d/3480-script-for-analog-v4l2-device-support-in-tvh-via-pipe-mux).
It uses **`v4l2-ctl`** for tuning (no `ivtv-tune`), carries its own NTSC/CATV
frequency tables so you select channels by name, can **scan** the band and save
only the channels that are live, and can keep that list refreshed on a **daily
timer or not at all**. Built for over-the-air NTSC, analog cable, and composite/
S-Video sources (VCRs, game consoles, retro computers).

Designed against the **Hauppauge WinTV-HVR-1950** (`pvrusb2` driver, on-board
hardware MPEG-2 encoder). On that card the analog node already emits an MPEG-2
program stream — you can literally `cat /dev/video0` — so streaming is just a
tune + a remux to MPEG-TS, with **no software video encoding and no separate
audio capture**. The tool auto-detects raw-frame cards (e.g. HVR-950Q) and falls
back to software encoding for them.

---

## 1. How it fits together

```
            ┌─────────────── ntsc-tvh (this tool) ───────────────┐
TVHeadend   │  v4l2-ctl: set input + NTSC-M + frequency           │
 IPTV mux ──┤  ffmpeg:  -f mpeg -i /dev/video0 -c copy -f mpegts  ├──▶ MPEG-TS
 pipe://    │  flock:    one tuner -> one user at a time          │     to stdout
            └─────────────────────────────────────────────────────┘
```

TVHeadend's `pipe://` mux runs a command and reads MPEG-TS from its stdout. Each
analog "channel" is one such mux whose command is `ntsc-tvh stream <CHANNEL>`.
You can create those muxes by hand, or — recommended — let an **Automatic IPTV
Network** build them from an M3U this tool generates.

---

## 2. Requirements

- **Linux** with the `pvrusb2` driver loaded for the HVR-1950 (in-tree; verify
  with `dmesg | grep -i pvrusb2` → *"WinTV HVR-1950 ... MPEG2 Encoder/Tuner"*).
- **HVR-1950 firmware** in `/lib/firmware/`: `v4l-pvrusb2-73xxx-01.fw` (FX2
  controller, ~8 KB), `v4l-cx25840.fw` (A/V decoder, ~16 KB), **and
  `v4l-cx2341x-enc.fw` (the MPEG-2 *encoder*, 376836 bytes)**. The encoder blob
  is the one people miss — without a working encoder you get a device that
  tunes and reports signal but produces an *empty stream on every channel*.
  It's non-redistributable (not in `linux-firmware`); get it from the Debian
  `ivtv-firmware` package (`snapshot.debian.org`) or extract it from the Windows
  driver.
- **A USB 2.0 path to the tuner.** This is the single biggest gotcha. The
  pvrusb2 *encoder* firmware is uploaded over USB at enumeration ("firmware2"),
  and that large transfer **fails on many xHCI / USB-3 host controllers** —
  `dmesg` shows `pvrusb2: firmware2 upload transfer failure`, and again you get a
  tuner that tunes but never streams. The fix is a real USB-2/EHCI path: a
  USB-2 port, a powered USB-2 hub in front of a USB-3 port, or a BIOS xHCI-mode
  option. **Before anything else, confirm a clean enumeration:**
  `dmesg | grep -i pvrusb2` must show *"Device initialization completed
  successfully"* with **no** `firmware2 upload transfer failure`.
- **`v4l-utils`** (provides `v4l2-ctl`) and **`ffmpeg`** (with the `mpegts`
  muxer; for raw cards it also needs `v4l2` + `alsa` input support, which the
  distro builds have).
- **Python 3.6+** (standard library only — no pip installs).
- TVHeadend with the **IPTV** input enabled (it is, by default).

```bash
sudo apt install v4l-utils ffmpeg          # Debian/Ubuntu
```

> Note: the obsolete `ivtv-utils` is **not** needed and should not be installed.

---

## 3. Install

```bash
sudo install -m 755 ntsc-tvh        /usr/local/bin/ntsc-tvh
sudo install -m 644 examples/ntsc-tvh.conf /etc/ntsc-tvh.conf     # optional, edit to taste
sudo mkdir -p /var/lib/ntsc-tvh
```

**Permissions matter.** TVHeadend usually runs as user `hts` (or `tvheadend`).
That user must be able to open the video node and write the state/lock dir:

```bash
sudo usermod -aG video hts
sudo chown -R hts:hts /var/lib/ntsc-tvh
```

The lock file now defaults to `/var/lib/ntsc-tvh/ntsc-tvh.lock` (inside the
state dir) rather than `/run/lock`, specifically so the runtime user always owns
the directory it lives in — `/run/lock` is frequently root-only, which produced
a `Permission denied` on the lock and a failure to stream. If you relocate
`LOCK_FILE`, make sure that directory is writable by the TVHeadend user.

Then sanity-check the box:

```bash
ntsc-tvh doctor
```

`doctor` confirms `ffmpeg`/`v4l2-ctl` are present, the device is readable, the
driver, whether it's a **hardware MPEG** node or a **raw** node, the available
inputs, and (for raw cards) the ALSA capture device.

---

## 3b. Install in Docker (linuxserver/tvheadend)

The pipe:// command is executed *inside* the TVHeadend container, so the
container — not the host — needs `python3`, `v4l-utils`, `ffmpeg`, the device
node, and this script. Firmware is loaded by the **host** kernel (the container
only sees the resulting `/dev/video0`), so the firmware + USB-2 requirements from
§2 are host-side and must be satisfied on the host *before* Docker is involved.

A working `docker-compose.yml` (analog-only; see the shipped `examples/docker-compose.yml`
for the annotated version):

```yaml
services:
  tvheadend:
    image: lscr.io/linuxserver/tvheadend:latest
    container_name: tvheadend
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
      # install the runtime deps into the container at start (idempotent):
      - DOCKER_MODS=linuxserver/mods:universal-package-install
      - INSTALL_PACKAGES=python3|v4l-utils|ffmpeg
    volumes:
      - ./data:/config
      - ./recordings:/recordings
      - ./ntsc-tvh:/usr/local/bin/ntsc-tvh:ro          # the script (chmod +x on host)
      - ./ntsc-state:/var/lib/ntsc-tvh                  # state + M3U + lock (persistent)
      # - ./ntsc-tvh.conf:/etc/ntsc-tvh.conf:ro         # only if overriding defaults
    ports:
      - 9981:9981
      - 9982:9982
    devices:
      - /dev/video0:/dev/video0                         # analog V4L2 node
    group_add:
      - "44"      # host 'video' GID — VERIFY: getent group video
    restart: unless-stopped
```

Three container-specific things that will bite if missed:

1. **`group_add` with the host `video` GID.** linuxserver runs TVHeadend as the
   non-root `abc` user (PUID/PGID). `/dev/video0` is `root:video 0660` on the
   host, so without the matching group the process can't open the device. Find it
   with `getent group video` and put the number here (often 44, verify).
2. **The state dir must be writable by PUID.** The lock and M3U live in
   `/var/lib/ntsc-tvh`; `chown -R 1000:1000 ./ntsc-state` on the host (matching
   PUID). A root-owned state dir gives `Permission denied` on the lock.
3. **Bind-mount the script to exactly `/usr/local/bin/ntsc-tvh`** — that's the
   path baked into the generated M3U's pipe:// URLs.

Provision and verify:

```bash
mkdir -p data recordings ntsc-state
cp /path/to/ntsc-tvh ./ntsc-tvh && chmod +x ./ntsc-tvh
chown -R 1000:1000 ./ntsc-state
docker compose up -d
docker exec -it tvheadend ntsc-tvh doctor          # expect 'status: all good'
```

> **Single tuner, ATSC vs. analog:** the HVR-1950 is one front-end. The pvrusb2
> driver enforces mutual exclusion between its V4L (analog) and DVB (ATSC)
> interfaces — they cannot both be open at once, and merely having TVHeadend's
> OTA EPG grabber active on the DVB side locks analog out (you'll see
> `Operation not permitted` on the analog open). If you also map `/dev/dvb` for
> ATSC on the same box, you're choosing analog *or* ATSC, not both live; the
> cleanest layout is one tuner per box. See Troubleshooting.

---

## 4. Quick start

```bash
ntsc-tvh list                       # every channel in every plan + the line inputs
ntsc-tvh list --tables us-bcast     # just OTA broadcast
ntsc-tvh signal us-bcast:7          # tune ch7, print signal %/AFC (sanity test)

ntsc-tvh scan                       # walk us-bcast + us-cable, save active channels
ntsc-tvh status                     # what the last scan found
ntsc-tvh m3u --stdout               # the playlist of active channels (+ inputs)
```

Test a stream straight to a file before wiring up TVHeadend:

```bash
ntsc-tvh stream us-bcast:7 > test.ts    # Ctrl-C after a few seconds
ffprobe test.ts                          # expect mpeg2video + mp2/ac3 in an mpegts
```

---

## 5. Channel IDs and frequency plans

Channels are addressed as **`TABLE:CHANNEL`** or **`input:NAME`**:

| ID example        | Meaning                                             |
|-------------------|-----------------------------------------------------|
| `us-bcast:7`      | OTA broadcast channel 7 (175.250 MHz)               |
| `us-cable:23`     | Cable **STD** channel 23 (217.250 MHz)              |
| `us-cable-irc:5`  | Cable **IRC** channel 5 (79.250 MHz)                |
| `us-cable-hrc:2`  | Cable **HRC** channel 2 (54.000 MHz)                |
| `input:composite` | The composite (RCA) line input — game consoles etc. |
| `input:svideo`    | The S-Video line input                              |

Four built-in plans (visual/picture-carrier frequencies):

- **`us-bcast`** — FCC 73.603 over-the-air, channels 2–69.
- **`us-cable`** — EIA-542 **STD**, channels 1–135. *Start here* for most US cable.
- **`us-cable-irc`** — **IRC**: identical to STD except channels 5 and 6 sit
  2 MHz higher.
- **`us-cable-hrc`** — **HRC**: all carriers 1.25 MHz below STD, except 5/6 which
  are 0.75 MHz above STD.

**Which cable plan?** Most US analog cable used STD or IRC; HRC systems are less
common. The honest answer is to just scan more than one plan and keep whichever
locks — see the scanner notes. The tuner's AFC easily covers the ±12.5 kHz
fine-offsets in the real EIA-542 tables, so the rounded values here tune fine.

Composite/S-Video are **line inputs**, not RF — no tuning, no scan. They're
listed as always-available channels because you plug a console in on demand.

---

## 6. Wiring it into TVHeadend

### Method A — Automatic IPTV Network (recommended)

One network, auto-built from the generated M3U, auto-refreshed.

1. Generate the playlist (the scanner does this for you; or run it directly):
   ```bash
   ntsc-tvh m3u            # writes /var/lib/ntsc-tvh/ntsc.m3u
   ```
2. In TVHeadend: **Configuration → DVB Inputs → Networks → Add → Automatic IPTV
   Network**.
   - **URL:** `file:///var/lib/ntsc-tvh/ntsc.m3u`
   - **Maximum # input streams:** `1` (you have one tuner — this is important).
   - **Re-fetch period (mins):** match your rescan cadence, e.g. `1440` for daily.
   - Leave **Scan after creation** on for the first run so services are probed.
3. Save. TVHeadend creates one mux per playlist entry, scans each, and a service
   appears per channel. Map services → channels under **Services** as usual.

**Critical detail (by design):** in an Automatic IPTV Network the **URL is the
mux's identity**. If a URL changes between playlist reads, TVHeadend treats it as
a *new* mux and deletes the old service/channel mapping. This tool therefore
keeps each channel's `pipe://` URL completely stable across rescans
(`pipe:///usr/local/bin/ntsc-tvh stream us-bcast:7` and nothing volatile in it).
A rescan only adds or removes whole entries; the channels that stay active keep
their mappings.

> Known cosmetic quirk on some 4.3-dev builds: a `pipe://` mux may show the URL
> as its name instead of `tvg-name` until it's scanned. The service name resolves
> correctly after scanning; just map the service to a sensibly-named channel.

**Turn off the OTA EPG grabber on this network.** Analog pipe muxes carry no
EIT/PSIP, so EPG grabbing finds nothing — but it *subscribes to the mux*, which
on a single tuner races the scan and live viewing for the device and causes
intermittent "scan no data" / "tuner busy". Network/EPG settings → disable
over-the-air EPG for the analog network.

**When typing the `file://` URL, don't let a trailing newline in.** If the IPTV
network reports it can't open the file and the error shows the path with the
closing quote on a new line, the URL field captured a stray `\n` (from pressing
Enter in the field or pasting). Clear it, retype `file:///var/lib/ntsc-tvh/ntsc.m3u`
(three slashes), and click **Save** rather than pressing Enter.

### Method B — Manual per-channel muxes (the proven, no-surprises route)

Exactly like the original forum approach, minus ivtv. Create a plain **IPTV
Network**, then **Add Mux** for each channel with URL:

```
pipe:///usr/local/bin/ntsc-tvh stream us-bcast:7
pipe:///usr/local/bin/ntsc-tvh stream us-cable:23
pipe:///usr/local/bin/ntsc-tvh stream input:composite
```

Let each mux scan; map the resulting service to a channel. More clicks, but
zero auto-network churn — good for a small, fixed lineup.

---

## 7. Scanning

```bash
ntsc-tvh scan                                  # defaults from config
ntsc-tvh scan --tables us-bcast                # OTA only (fast)
ntsc-tvh scan --tables us-cable,us-cable-irc   # compare two cable plans
ntsc-tvh scan --method both --threshold 45     # stricter
```

The scan tunes each channel, lets it settle (`--dwell`), decides active/idle,
and saves the live set to the state file **and** regenerates the M3U (unless
`--no-m3u`). It runs under the same tuner lock as streaming, so it won't fight a
live viewer: by default it **aborts immediately if the tuner is busy**
(`SCAN_LOCK_WAIT=0`); pass `--wait 30` to queue instead.

### Detection methods — and the honest caveats

Analog "is this channel live?" is inherently fuzzy. Three strategies:

- **`signal`** (default) — reads the tuner's signal-strength % via
  `v4l2-ctl --get-tuner` and thresholds it. Fast (a few minutes for a full cable
  walk). **Caveat:** the strength figure from these tuners (tda18271 on the
  HVR-1950) is coarse and not calibrated; a strong-but-unmodulated carrier can
  read "active." Tune `SCAN_THRESHOLD` to your plant.
- **`video`** — grabs a few frames and measures luma spread. The cx25840 decoder
  emits a flat blue/black raster when it has no sync, so a near-zero spread
  reliably means "no channel." **Caveat:** it can't distinguish a real picture
  from analog *snow* (both have high spread), and it's slower (extra seconds per
  channel). Good at killing dead channels, not at certifying live ones.
- **`both`** — require signal AND non-flat video. Fewer false positives, slowest.

Recommendation: start with `signal` and a threshold you calibrate against two or
three channels you *know* are live (`ntsc-tvh signal us-cable:23`), then switch to
`both` if you get false positives. There is no magic here — it's a heuristic, and
the tool is explicit about that rather than pretending otherwise.

---

## 8. Periodic rescan (24 h or none)

```bash
sudo ntsc-tvh install-timer --period 24h --user hts   # daily systemd timer
sudo ntsc-tvh install-timer --period none             # remove it
ntsc-tvh status                                        # shows timer state
```

`--period 24h` installs `ntsc-tvh-scan.timer` (`OnCalendar=daily`, `Persistent`
so a missed run catches up, with a 5-min jitter) plus a oneshot service that runs
`scan --wait 30` and refreshes the M3U as user `hts`. `--period none` disables
and removes both units. Pair the cadence with the Automatic Network's re-fetch
period (1440 min) so TVHeadend re-reads the refreshed list.

**Cron alternative**, if you don't use systemd:

```cron
17 4 * * *  hts  /usr/local/bin/ntsc-tvh scan --wait 30 >/var/log/ntsc-scan.log 2>&1
```

**In Docker** there's no systemd, so `install-timer` doesn't apply — drive the
rescan from a **host** cron that execs into the container:

```cron
17 4 * * *  root  docker exec tvheadend ntsc-tvh scan --wait 30
```

Given the single-tuner contention, an infrequent off-hours rescan (or fully
manual — just run `docker exec tvheadend ntsc-tvh scan` when you need it) avoids
colliding with recordings or live viewing. The state dir is a persistent volume,
so scan results survive container recreation; you don't need to rescan after an
image update.

---

## 9. Game consoles / retro computers (composite & S-Video)

Plug the device into the HVR-1950's RCA or S-Video jack and select the matching
line-input channel:

```bash
ntsc-tvh stream input:composite > snes.ts     # or input:svideo
```

In TVHeadend these are just two more pipe muxes (already in the generated M3U
when `INPUTS_ALWAYS_ACTIVE=1`). No tuning happens — the hardware encoder digitizes
whatever is on that input. If a source is 240p/non-standard (common on retro
hardware), the cx25840 still locks NTSC timing in most cases; if a particular
console rolls or loses colour, that's an analog-sync limitation of the decoder,
not the script.

---

## 10. Configuration reference

All keys live in `/etc/ntsc-tvh.conf` (or as env vars), one `KEY=VALUE` per line;
a `# comment` after whitespace is ignored. The most useful:

| Key                    | Default                          | Purpose                                   |
|------------------------|----------------------------------|-------------------------------------------|
| `DEVICE`               | `/dev/video0`                    | analog V4L2 node                          |
| `LOCK_FILE`            | `/var/lib/ntsc-tvh/ntsc-tvh.lock`| tuner lock (in state dir, not `/run/lock`)|
| `AUDIO_CODEC`          | `copy`                           | `copy`/`mp2`/`ac3` (copy = native, 0 CPU) |
| `VIDEO_BITRATE`        | *(driver default)*               | pin HW MPEG-2 bitrate, e.g. `6000000`     |
| `CAPTURE_VIA_CAT`      | `1`                              | `1` = `cat dev \| ffmpeg` (reliable read; stream + video scan) |
| `STOP_GRACE`           | `1`                              | seconds ffmpeg gets after SIGTERM before SIGKILL |
| `SCAN_TABLES`          | `us-bcast,us-cable`              | plans walked by `scan`                    |
| `SCAN_METHOD`          | `signal`                        | `signal`/`video`/`both`                   |
| `SCAN_THRESHOLD`       | `35`                             | signal % cutoff                           |
| `SCAN_LOCK_WAIT`       | `5`                              | seconds to wait for the tuner lock        |
| `INPUTS_ALWAYS_ACTIVE` | `1`                              | list composite/svideo as channels         |
| `SELF_PATH`            | *(auto)*                         | path written into M3U pipe:// URLs         |

---

## 11. Troubleshooting

The failure modes below are roughly in the order you'd hit them on a fresh
build. Several produce the *same* surface symptom ("empty stream / mux scan
fails"), so the trick is isolating the layer — work top-down.

**Tunes and reports signal, but every channel produces an empty stream.**
This has several distinct causes that look identical; check them in order:

1. *`dmesg | grep -i pvrusb2` shows `firmware2 upload transfer failure`* — the
   MPEG **encoder** firmware didn't upload. Almost always an **xHCI / USB-3**
   host-controller issue. Move the tuner to a USB-2 port, interpose a powered
   USB-2 hub, or find a BIOS xHCI-mode option. A clean enumeration ends in
   *"Device initialization completed successfully"* with no firmware2 error.
   This is deterministic (fails every boot), not flaky — if it reproduces across
   power-cycles, suspect the controller, not a loose cable. (Test the device on
   a second machine with native USB-2 to confirm it's the host and not the card.)
2. *`dmesg` is clean but the stream is still empty* — prove the encoder raw:
   `v4l2-ctl -d /dev/video0 --set-input=0 --set-freq=67.25 && timeout 4 cat /dev/video0 | wc -c`.
   Megabytes = encoder fine, problem is downstream (ffmpeg/tool); near-zero with
   `--get-tuner` showing 0% signal = no carrier on that channel (try a known-live
   channel or the composite input).
3. *Raw `cat` gives megabytes but `ntsc-tvh stream` gives 0 bytes* — this is the
   ffmpeg-opens-the-device-directly failure. Ensure `CAPTURE_VIA_CAT=1` (now the
   default). On some ffmpeg builds `-f mpeg -i /dev/video0` yields no stream while
   `cat /dev/video0 | ffmpeg -f mpeg -i pipe:0` is reliable.

**`Operation not permitted` opening `/dev/video0`.** Not a sandbox issue and not
"device busy" — it's the pvrusb2 **analog/digital mutual exclusion**. TVHeadend
has the DVB (ATSC) interface open (for tuning, idle adapter management, or OTA
EPG), which puts the single front-end in digital mode and denies the analog
open. Either don't map `/dev/dvb` on this box, or disable the ATSC adapter **and
the OTA EPG grabber** in TVHeadend when you want analog. One tuner can't expose
both modes at once.

**`PermissionError: [Errno 13] ... '/run/lock/ntsc-tvh.lock'`** (or any lock
path). The user TVHeadend runs as can't create the lock file. The default
`LOCK_FILE` now lives in `/var/lib/ntsc-tvh/` for this reason; make sure that dir
is owned by the TVHeadend user (`chown -R 1000:1000 ntsc-state` for linuxserver's
default PUID). Don't hand-test with `docker exec` as **root** while the container
is live — that plants a root-owned lock the `abc` user then can't open; test with
`docker exec -u abc`.

**IPTV network finds 0 muxes / `unable to open file '...m3u\n'`.** A trailing
newline in the network's **URL** field (the error message shows the closing quote
on a new line). Clear the URL field, retype `file:///var/lib/ntsc-tvh/ntsc.m3u`
by hand (three slashes), and click Save — don't press Enter in the field, don't
paste a string with a trailing newline. Also confirm the container can read the
file: `docker exec tvheadend cat /var/lib/ntsc-tvh/ntsc.m3u`.

**Muxes scan but immediately fail with `scan no data`, or `tuner is busy`.**
Lock contention on the single tuner — usually TVHeadend's **OTA EPG grabber**
racing the scan, or several signal-less muxes cycling the tuner. Turn **off**
OTA EPG on the analog network (analog pipes carry no EPG anyway), set **Maximum #
input streams = 1**, and disable muxes that have no live signal. `SCAN_LOCK_WAIT`
(default 5s) gives a stream a brief grace period to acquire the tuner instead of
failing instantly.

**`tuner did not tune: asked for X MHz, device reports 0.000 MHz`.** The driver
accepted the frequency but the tuner never moved. This is almost always tuner
firmware that failed to load. For example, the Pinnacle 800e (em28xx + xc3028)
needs `xc3028-v27.fw` in `/lib/firmware/`, which Ubuntu's `linux-firmware` does not
ship. `dmesg` shows `Could not load firmware xc3028-v27.fw`. Composite and S-Video
still work without it. `scan` aborts on this error and keeps the previous
state/M3U, so it never saves an empty channel list.

**`signal` always reads 0%.** Some driver/tuner combos don't populate the field.
Switch `SCAN_METHOD=video` (or `both`) and lean on the picture heuristic.

**No audio / wrong audio.** On the HVR-1950 audio is baked into the MPEG-2 PS;
`AUDIO_CODEC=copy` is correct. Switch to `ac3` only if a downstream player
insists. For raw cards audio comes from ALSA. Auto-detect picks the sound card on the same
USB/PCI device as the video node (via `/sys`), falling back to an `arecord -l` card
named like the tuner or driver. If neither matches, it uses **no** audio and `doctor`
flags it, so it never captures the host's own sound chip. Set `RAW_AUDIO_DEV=hw:N,D`
to choose explicitly (e.g. a card cabled into the PC's line-in).

**Auto-network keeps recreating muxes / losing channel maps.** Something is
changing the pipe:// URLs between playlist reads — the URL is the mux's identity.
Don't hand-edit the M3U structure; let the tool own it, and keep `SELF_PATH`
fixed so the baked-in path never moves.

**"Tuner busy" on a quick channel change.** The previous subscription's stream
hasn't finished stopping. `ntsc-tvh stream` supervises ffmpeg (and `cat`) as
child processes, so it stops promptly with either **Kill signal (pipe)**
setting on the mux:
- **SIGKILL** (TVHeadend's default): the kernel kills ffmpeg and `cat` along
  with ntsc-tvh (`PR_SET_PDEATHSIG`), and the lock is freed at once.
- **SIGTERM / SIGINT / SIGHUP**: ffmpeg gets SIGTERM and `STOP_GRACE` seconds
  (default 1) to exit, then SIGKILL. Some ffmpeg builds (e.g. 6.1 in the Alpine
  image) never exit on SIGTERM with an ALSA input, which raw cards like the 800e
  use.

The next stream waits up to `SCAN_LOCK_WAIT` (5 s) for the lock, which covers
either case. If you still see this, check for a stray ffmpeg holding the device
(`docker exec tvheadend pgrep -a ffmpeg`).

---

## 12. Design notes & limitations (the honest list)

- **One tuner, one stream.** The HVR-1950 has a single analog path. Concurrent
  analog viewing/recording isn't possible; the flock makes the contention
  explicit instead of letting two readers corrupt each other. The digital
  (ATSC/QAM) side is a *separate* DVB adapter and is untouched by this tool.
- **Activity detection is heuristic**, not a real demod lock indicator. See §7.
- **HW path is a remux, not a transcode** — near-zero CPU, but you're at the
  mercy of the on-board encoder's quality/bitrate. `VIDEO_BITRATE` nudges it;
  there's no deinterlace on this path (the stream is already interlaced MPEG-2,
  which is correct for analog SD — let the client deinterlace).
- **Frequency tables are US/NTSC-M.** PAL/SECAM or European cable channel-letter
  schemes aren't included; the table functions are trivial to extend if needed.
- **Stable-URL contract** with the Automatic Network is load-bearing — honored
  here, but don't defeat it by templating the URLs yourself.

---

## 13. Changelog — v2 (validated against a live HVR-1950 in Docker)

Changes folded in after a full end-to-end bring-up on `linuxserver/tvheadend`:

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

### Since v2

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

---

## 14. Development & tests

The tests need no tuner and no pip installs (stdlib `unittest` only). They load the
script as a module and replace `v4l2-ctl`, `ffmpeg` and `cat` with fakes that read
canned output from `tests/fixtures/`. `tests/test_supervisor.py` runs the real script
with real signals, using a fake ffmpeg (`tests/fakebin/`) that can ignore SIGTERM:

```bash
python3 -m unittest discover -s tests -v
```

They cover the frequency plans, channel-ID parsing, config precedence, `v4l2-ctl`
output parsing, the ffmpeg command line, stopping a stream on SIGTERM/SIGINT/SIGHUP/
SIGKILL with nothing left holding the tuner, scan → state → M3U, and the exact
`pipe://` URL format. CI
(`.github/workflows/test.yml`) runs them on Python 3.10–3.14 on every push.

The `*-800e.txt` fixtures were captured from a real Pinnacle 800e (em28xx). The
HVR-1950 ones are modelled on `v4l2-ctl` output, not captured from the real box. To
pin them to the real hardware, capture these on the OptiPlex and replace the
matching `tests/fixtures/*.txt` files:

```bash
v4l2-ctl -d /dev/video0 --all          > v4l2-all-hvr1950.txt
v4l2-ctl -d /dev/video0 --list-inputs  > v4l2-list-inputs-hvr1950.txt
v4l2-ctl -d /dev/video0 --get-tuner    > v4l2-get-tuner-locked.txt   # tuned to a live channel
```

Tests marked `@unittest.expectedFailure` record known bugs. When a fix lands, the
test passes unexpectedly and the run fails until you remove the decorator.
