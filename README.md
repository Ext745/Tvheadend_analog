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

> **Setting up digital (ATSC) channels in TVHeadend too? Read [§6c](#6c-atsc-digital-network-in-tvheadend--get-this-right-first) first.**
> Pick only the predefined mux list **"us-ATSC-center-frequencies-8VSB"** (no date in
> the name). Adding a second, dated list gives every station duplicate muxes plus
> dozens of dead ones, and the guide (EPG) then barely fills.

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
  Recent distros ship the decoder and encoder files (Ubuntu: `linux-firmware-misc`;
  `ls /lib/firmware/v4l-cx*` may show them as `.fw.zst`). The FX2 file usually
  isn't packaged: LibreELEC's [dvb-firmware](https://github.com/LibreELEC/dvb-firmware/tree/master/firmware)
  repository has all three (`v4l-pvrusb2-73xxx-01.fw`: 8192 bytes, MD5
  `c6b01cb318b909cc52d2cf643ca269a1`). Older sources: the Debian `ivtv-firmware`
  package (`snapshot.debian.org`) or the Windows driver. Replug the tuner after
  installing.
- **A USB 2.0 path to the tuner.** This is the single biggest gotcha. The
  pvrusb2 *encoder* firmware is uploaded over USB at enumeration ("firmware2"),
  and that large transfer **fails on many xHCI / USB-3 host controllers** —
  `dmesg` shows `pvrusb2: firmware2 upload transfer failure`, and again you get a
  tuner that tunes but never streams. The fix is a real USB-2/EHCI path: a
  USB-2 port, a powered USB-2 hub in front of a USB-3 port, or a BIOS xHCI-mode
  option. **On newer PCs every port is xHCI**, the black "USB 2" ones included
  (`lspci | grep -i usb` lists only an xHCI controller; `lsusb -t` shows the card
  straight under an `xhci_hcd` root hub), so there a USB-2 port does *not* help —
  use the hub. A USB-3 hub works too: it contains a USB-2 hub, and the card runs
  through that half (`lsusb -t` then shows it under a `480M` hub). Verified on an
  Intel Tiger Lake (xHCI-only) PC: firmware2 failed on every root port, worked
  behind a VIA USB-3 hub. **Before anything else, confirm a clean enumeration:**
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

## 2b. Tested hardware and references

| | Hauppauge WinTV-HVR-1950 | Pinnacle PCTV HD Pro Stick (800e) |
|---|---|---|
| Status | production: Dell 7090 (Intel Tiger Lake, xHCI-only — behind a powered USB hub, see §2); previously a Dell OptiPlex 3020 (native USB 2.0) | dev/test, on the same Dell 7090 |
| Driver | `pvrusb2` | `em28xx` (card=17, em2882/3), USB `2304:0227` |
| Analog path | hardware MPEG-2 encoder (cx23416) → remux, via `cat` | raw YUYV (tvp5150am1 decoder) → software MPEG-2 + ALSA |
| Tuner / ATSC | single front-end; analog/DVB mutually exclusive | xc3028 tuner shared with an LGDT3303 ATSC demod; **not** exclusive (see §11) |
| Firmware | `v4l-pvrusb2-73xxx-01.fw`, `v4l-cx25840.fw`, `v4l-cx2341x-enc.fw` | `xc3028-v27.fw` |
| Audio | inside the MPEG-2 stream | ALSA card `Em28xxAudio` (auto-detected via sysfs) |

HVR-1950 behaviour seen in testing (kernel 7.0):
- With no signal it shows a **black** screen (luma 0–1), not snow; the video
  scan's contrast check rejects it. Signal reads **0 %** without an analog carrier
  and **100 %** on a real one (an RF modulator on us-bcast:4), so the `signal` scan
  method works on this card. A full us-bcast scan found exactly that channel with
  either method (`signal` 2 min, `video` ~6 min for 68 channels).
- The driver itself refuses the analog node (`Operation not permitted`) while its
  ATSC side is receiving, so it can't be wedged the way the 800e can (§11).
- Its ATSC side (Samsung S5H1411) received cleaner than the 800e's on the same
  antenna: SNR 23.5 dB with no uncorrectable blocks.
- A stream's first byte arrives ~1.5 s after the start (`PROBE_SECONDS`, §10).
- Every `analog-*` profile (on its 480i) and every `digital-*` profile (on its ATSC
  side, 1080i) from `examples/tvh-stream-profiles` plays, software and VAAPI, and
  the "Analog H.264" DVR configs record cleanly.
- `dmesg` sometimes logs `device's encoder appears to be stuck` at a stream start
  (§11); harmless when occasional.

800e behaviour seen in testing (kernel 7.0):
- It powers up in **PAL** 720×576. `tune` switches it to NTSC 720×480.
- It delivers ALSA **audio only while video is streaming**. `arecord` on its own
  gets an I/O error.
- With no signal it shows **snow** rather than a blank screen.
- It reports signal **only while capturing**. Idle, `v4l2-ctl --get-tuner` reads
  **0 %** on every channel, even one with a perfect picture; about 1.6 s into a
  capture the same channel reads 44 % (an RF modulator on us-bcast:4) and
  `--get-detected-standard` shows NTSC. So ntsc-tvh reads raw cards' signal during a
  short capture (`signal`, `scan`, `analog-check`), which takes ~3 s on an empty
  channel. Holding the device open without capturing isn't enough.
- Without `xc3028-v27.fw` the tuner silently stays at 0 MHz (see §11). The
  kernel's `extract_xc3028.pl` names a download that no longer exists:
  `steventoth.net` now serves an unrelated site. A prebuilt copy is in LibreELEC's
  [dvb-firmware](https://github.com/LibreELEC/dvb-firmware/raw/master/firmware/xc3028-v27.fw)
  repository (66 220 bytes, MD5 `293dc5e915d9a0f74a368f8a2ce3cc10`).

**Other cards.** To check what your card can do on Linux, look it up in the
LinuxTV wiki's [Hardware device information](https://www.linuxtv.org/wiki/index.php/Hardware_device_information)
list. The wiki sits behind a JavaScript bot check, so open it in a browser. What
matters for ntsc-tvh:
- **An analog (NTSC) tuner or composite/S-Video input with a V4L2 driver.**
  Digital-only (ATSC/DVB) cards don't apply; TVHeadend handles those natively.
- **Hardware MPEG encoder or raw frames.** Cards with an on-board encoder (like
  the HVR-1950) are remuxed with no CPU cost. Raw-frame cards (like the 800e)
  are encoded in software, with audio from ALSA. `ntsc-tvh doctor` reports
  which path it detected.
- **Firmware.** Many tuners need a firmware file the distro doesn't ship.
- **Hybrid analog+digital cards.** Check whether one tuner serves both sides.
  If it does, don't use analog and ATSC at the same time (see §11).

Driver pages for the cards above: [Hauppauge WinTV-HVR-1950](https://www.linuxtv.org/wiki/index.php/Hauppauge_WinTV-HVR-1950),
[Pvrusb2](https://www.linuxtv.org/wiki/index.php/Pvrusb2), and the
[pvrusb2 project page](http://www.isely.net/pvrusb2/pvrusb2.html) (Mike Isely,
driver maintainer).

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
inputs, and (for raw cards) the ALSA capture device. Then it **captures four
frames** the way `stream` does, from whatever input/channel the card is on (it
doesn't retune), and says whether it saw a picture, a flat screen or snow. A card
that tunes but sends no video fails here, with a pointer to the usual causes
(encoder firmware, USB-3 path). It skips the capture while a stream or scan holds
the tuner or the card's digital side is open; `--no-capture` skips it always.

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

Channels are addressed as **`TABLE:CHANNEL`**, **`input:NAME`** or **`fm:MHZ`**:

| ID example        | Meaning                                             |
|-------------------|-----------------------------------------------------|
| `us-bcast:7`      | OTA broadcast channel 7 (175.250 MHz)               |
| `us-cable:23`     | Cable **STD** channel 23 (217.250 MHz)              |
| `us-cable-irc:5`  | Cable **IRC** channel 5 (79.250 MHz)                |
| `us-cable-hrc:2`  | Cable **HRC** channel 2 (54.000 MHz)                |
| `input:composite` | The composite (RCA) line input — game consoles etc. |
| `input:svideo`    | The S-Video line input                              |
| `fm:101.1`        | FM radio, 101.1 MHz (cards with a radio node, §9b)  |

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

### One-command setup: `ntsc-tvh tvh-setup`

After `ntsc-tvh scan` has written the playlist, one command makes the channels appear
in TVHeadend, with no clicking through its UI. Run it where TVHeadend runs, so it sees
the same playlist path:

```bash
docker exec tvheadend ntsc-tvh tvh-setup --dry-run   # show what it would change
docker exec tvheadend ntsc-tvh tvh-setup             # do it
docker exec tvheadend ntsc-tvh tvh-setup --status    # what TVHeadend has set up, in one screen
docker exec tvheadend ntsc-tvh --card 800e tvh-setup # a second card gets its own network
```

It does what Method A below does by hand, through TVHeadend's web API:

1. Creates an **Automatic IPTV Network** called `Analog (ntsc-tvh)` (`Analog NAME
   (ntsc-tvh)` with `--card NAME`) reading `file:///var/lib/ntsc-tvh/ntsc.m3u`, with
   **Maximum # input streams = 1**. If that network exists, it fixes those two settings
   in place; if you made one by hand under another name that reads the same playlist,
   it uses that one. It never makes a second network.
2. Turns the **OTA EPG grabber off** on each of its muxes (see the warning below).
3. **Scans** the muxes that have no service yet (each tunes the card for a few seconds).
4. **Maps** the new services to channels, named as in the playlist.

Re-running it is safe: it only does what is missing, so run it again after a rescan
adds channels. `--teardown` removes the network with its muxes and services, the
channels that came only from it, and its channel tag; nothing else. If TVHeadend asks
for a login, add `--user admin --password …` (any of its login modes works: digest
MD5, SHA-256 or SHA-512-256, or plain); `--url` points it at another address (default
`http://localhost:9981`).

It is a convenience layer only: if a future TVHeadend changes its API, `tvh-setup`
stops with a message naming the field it couldn't find and the TVHeadend version, and
streaming, scanning and the playlist keep working. Set things up by hand then, below.
Tested against TVHeadend 4.3-2891, including a brand-new install with an admin login.

### Method A — Automatic IPTV Network (by hand)

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
intermittent "scan no data" / "tuner busy". The playlist does this for you: every entry
carries `tvh-epg="disable"`, which TVHeadend applies when it creates the mux and again
on every re-fetch. (By hand: each mux's **EPG scan** setting → **Disable**.)

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

## 6b. Stream profiles (analog and digital)

Analog channels reach TVHeadend as 720×480 MPEG-2 with MP2 audio. The video is
interlaced from a hardware encoder (HVR-1950), already deinterlaced from the raw
path (800e), and 4:3 either way. Kodi and VLC play that as-is. Browsers and many
phones, smart TVs and IPTV apps don't. TVHeadend's stock `webtv-*` profiles are a
poor fit: they scale to 384 lines and don't deinterlace.

`examples/tvh-stream-profiles` creates stream profiles for analog channels, and for
digital ATSC channels on the same TVHeadend (see *Digital channels* below), or
updates them if they already exist. `--sources analog` or `--sources digital`
limits it to one kind. Run it where TVHeadend's ffmpeg runs, so its speed test
measures the machine that will do the encoding:

```bash
docker cp examples/tvh-stream-profiles tvheadend:/usr/local/bin/
docker exec tvheadend tvh-stream-profiles [--user admin --password …] [--dry-run]
docker exec tvheadend tvh-stream-profiles --remove     # undo
```

| Profile | Output | For |
|---|---|---|
| *(default `htsp`/`pass`)* | MPEG-2/MP2 as received | Kodi (HTSP), VLC: no profile needed, set Kodi's deinterlacing to Auto |
| `analog-h264-aac-mpegts` | H.264 + AAC, 720×480 4:3, MPEG-TS | IPTV apps, VLC, smart TVs |
| `analog-h264-aac-matroska` | H.264 + AAC, Matroska | TVHeadend's web player |
| `analog-mobile` | H.264 + AAC 96k, 540×360, ~1 Mb/s | phones, remote viewing |
| `analog-h265-aac-mpegts` ⚡ | H.265 + AAC, MPEG-TS, about half the H.264 bitrate | Kodi, VLC, newer TVs |
| `analog-av1-opus-webm` ⚡ | AV1 + Opus, WebM | Chrome, Firefox, newer phones/TVs |
| `analog-h264-720p60` ⚡ | H.264 + AAC, 960×720 at **59.94p** | big screens with weak scalers |
| `analog-h264-vaapi-mpegts` ⚡🎮 | as `analog-h264-aac-mpegts`, encoded on the GPU | the same clients, at ~1/10 the CPU |
| `analog-h265-vaapi-mpegts` ⚡🎮 | H.265 on the GPU | as above |
| `analog-av1-vaapi-webm` ⚡🎮 | AV1 + Opus on the GPU | as above (needs an AV1-encoding GPU) |
| `analog-mobile-vaapi` ⚡🎮 | 540×360 ~1 Mb/s on the GPU | phones, remote viewing |
| `analog-h264-vaapi-720p60` ⚡🎮 | 960×720 59.94p on the GPU | big screens |

All profiles deinterlace only frames that are actually interlaced, and keep the
4:3 shape. Pick a profile per client (the `?profile=` URL parameter, or a user's or
access entry's streaming profile in TVHeadend).

**Digital channels.** ATSC arrives as 1080i, 1080p, 720p60 or 480i MPEG-2, with
AC-3 audio (often 5.1) plus second-language and description tracks. The `digital-*`
profiles keep the **main audio track only** (the first), and come in two audio
flavours:
- **`-ac3`** copies the original AC-3 untouched, so 5.1 stays 5.1, for Kodi, TVs
  and home theatres.
- **`-aac`** downmixes to stereo AAC for browsers and phones.

1080i is deinterlaced to 1080p29.97; 720p60 and other progressive formats pass
through untouched. All digital profiles are ⚡-gated, tested on a synthetic 1080i
clip (about 5× the work of analog SD), so each PC gets what it can handle.

| Profile | Output | For |
|---|---|---|
| `digital-h264-ac3-mpegts` ⚡ | H.264 + original AC-3, MPEG-TS | Kodi, TVs, IPTV apps |
| `digital-h264-aac-mpegts` ⚡ | H.264 + AAC stereo, MPEG-TS | phones, VLC, apps without AC-3 |
| `digital-h265-ac3-mpegts` ⚡ | H.265 + original AC-3 | Kodi, newer TVs |
| `digital-av1-opus-webm` ⚡ | AV1 + Opus stereo, WebM | Chrome, Firefox |
| `digital-mobile` ⚡ | H.264 960×540 ~1.5 Mb/s + AAC | phones, remote viewing |
| `digital-*-vaapi-*` ⚡🎮 | the same five, encoded on the GPU | the same clients, far less CPU |

On the i7-10700 here (real captures, times real time):
- 1080i: x264 7.8×, x265 3.7×, SVT-AV1 3.6×
- 720p60: x264 7.7×, x265 1.9×
- h264_vaapi on the UHD 630: 13.6× (1080i) and 15.3× (720p60)

A typical 4-core PC can manage H.264 in software. H.265 and AV1 at 1080 mostly
need the GPU.

**Closed captions:** the software profiles carry the station's ATSC (A/53)
captions through. The GPU profiles drop them. ffmpeg's `h264_vaapi` and
`hevc_vaapi` fail on captioned streams when asked to copy them (`Encode failed:
-28`), so the script turns that off for them.

**Making your own:** [docs/custom-profiles.md](docs/custom-profiles.md) explains
stream, codec and DVR profiles, both ways to build them (TVHeadend's transcoder or
an ffmpeg spawn profile), and the pitfalls of this TVHeadend build. It includes
tested recipes: audio-only fix, analog denoise, overscan crop, loudness, low
latency, 5.1 passthrough and GPU encoding. It also covers how to test and
measure a profile.

### Choosing a profile

Measured on this project's test PC (i7-10700 with UHD 630 graphics) with real
content. Analog: an 8 s 480i 4:3 clip with light analog-style noise at 7.8 Mb/s,
the HVR-1950's default encoder bitrate plus MP2. Digital: 8 s of WNBC 1080i at
7.4 Mb/s, video plus the main AC-3 5.1 track. How the columns were measured:
- **Size** is the output against that source, audio included. This is what you
  save on bandwidth or disk.
- **Quality** is SSIM against the deinterlaced source: ≥0.97 excellent, 0.96
  very good, 0.95 good, below that fair. For scale, re-encoding the same content
  to MPEG-2 at 7 Mb/s scores 0.975 (analog) and 0.958 (digital).
- **CPU** is CPU cores kept busy while streaming one channel.
- **Speed** is how much faster than real time this PC encodes it. The script's
  gate needs 2×.

Weaker PCs scale roughly with core count and age.

| Profile | Best for | Size | Bitrate | Quality | CPU | Speed |
|---|---|---|---|---|---|---|
| *(pass / htsp)* | Kodi, VLC | 100% | as received | original | ~0 | — |
| **Analog** | | | | | | |
| `analog-h264-aac-mpegts` | IPTV apps, VLC, TVs | 24% | 1.9 Mb/s | good (0.960) | 0.30 | 16× |
| `analog-h264-aac-matroska` | web player | ≈24% | ≈1.9 Mb/s | ≈good | ≈0.3 | — |
| `analog-mobile` | phones, remote | 13% | 1.0 Mb/s | 540×360 | 0.22 | 19× |
| `analog-h265-aac-mpegts` ⚡ | Kodi, newer TVs | 19% | 1.5 Mb/s | good (0.958) | 1.17 | 4.5× |
| `analog-av1-opus-webm` ⚡ | Chrome, Firefox | 16% | 1.3 Mb/s | fair (0.949) | 1.29 | 6.7× |
| `analog-h264-720p60` ⚡ | big screens | 47% | 3.7 Mb/s | 960×720, 59.94p | 1.22 | 6.0× |
| `analog-h264-vaapi-mpegts` 🎮 | as H.264, low CPU | 33% | 2.6 Mb/s | good (0.958) | 0.05 | 18× |
| `analog-h265-vaapi-mpegts` 🎮 | as H.265, low CPU | 25% | 2.0 Mb/s | good (0.951) | 0.05 | 12× |
| `analog-mobile-vaapi` 🎮 | phones, low CPU | 14% | 1.1 Mb/s | 540×360 | 0.04 | 19× |
| `analog-h264-vaapi-720p60` 🎮 | big screens, low CPU | 70% | 5.5 Mb/s | 960×720, 59.94p | 0.06 | 9× |
| DVR **Analog H.264** | recordings | 34% | 2.6 Mb/s | very good (0.967) | 0.50 | 12× |
| DVR **Analog H.264 (GPU)** 🎮 | recordings, low CPU | 33% | 2.6 Mb/s | good (0.958) | 0.05 | 18× |
| **Digital** | | | | | | |
| `digital-h264-ac3-mpegts` ⚡ | Kodi, TVs (5.1 kept) | 67% | 5.0 Mb/s | very good (0.967) | 2.1 | 5.4× |
| `digital-h264-aac-mpegts` ⚡ | phones, VLC, apps | 64% | 4.8 Mb/s | very good (0.967) | 2.1 | 4.9× |
| `digital-h265-ac3-mpegts` ⚡ | Kodi, newer TVs | 43% | 3.2 Mb/s | very good (0.962) | **6.1** | 1.8× |
| `digital-av1-opus-webm` ⚡ | Chrome, Firefox | 44% | 3.3 Mb/s | excellent (0.976) | **4.9** | 2.2× |
| `digital-mobile` ⚡ | phones, remote | 19% | 1.4 Mb/s | 960×540 | 0.95 | 7.9× |
| `digital-h264-vaapi-ac3-mpegts` 🎮 | Kodi, TVs, low CPU | 69% | 5.2 Mb/s | very good (0.964) | 0.03 | 11× |
| `digital-h264-vaapi-aac-mpegts` 🎮 | phones, low CPU | 66% | 4.9 Mb/s | very good (0.964) | 0.06 | 9× |
| `digital-h265-vaapi-ac3-mpegts` 🎮 | Kodi, TVs, low CPU | 60% | 4.5 Mb/s | good (0.960) | 0.07 | 4.5× |
| `digital-mobile-vaapi` 🎮 | phones, low CPU | 21% | 1.6 Mb/s | 960×540 | 0.05 | 12× |
| DVR **Digital H.264** ⚡ | recordings (5.1 kept) | 78% | 5.8 Mb/s | excellent (0.971) | 3.2 | 3.8× |
| DVR **Digital H.264 (GPU)** 🎮 | recordings, low CPU | 80% | 6.0 Mb/s | very good (0.966) | 0.03 | 11× |

Rules of thumb:
- **Smallest file or stream at good quality:** AV1, then H.265, then H.264. That
  costs CPU unless the GPU does it.
- **A GPU makes almost everything free.** VAAPI profiles use 0.03–0.07 cores
  instead of 1–6, at a few percent more size for the same quality.
- **Analog shrinks a lot** (3–7× smaller), because the HVR-1950's MPEG-2 is
  generous for SD.
- **Digital shrinks less** (20–55%), because broadcasters already squeeze their
  MPEG-2. The bigger win there is compatibility: H.264/AAC plays everywhere.
- **Software H.265 and AV1 at 1080** are heavy: 5–6 cores each here, and H.265
  measured just under the 2× gate on real content. On most PCs, use the GPU
  versions for those.

Settings were picked from a size-vs-SSIM sweep: each profile beats or matches an
MPEG-2 re-encode of the same content while saving space. One measuring trap:
MPEG-TS output decodes one frame later than its source, so SSIM must be
compared at the best frame alignment, or every TS profile looks about 0.02
worse than it is.

**Recording analog as H.264.** The script also creates two DVR profiles
(**Configuration → Recording**):
- **Analog H.264** uses the `analog-h264-rec` stream profile: x264 at the slower
  `faster` preset, CRF 20, AAC 192 kb/s, in MPEG-TS so a recording that is cut
  short stays playable.
- **Analog H.264 (GPU)** uses `analog-h264-vaapi-rec`. It is only created if the
  GPU test passes.

**Digital H.264** and **Digital H.264 (GPU)** do the same for ATSC, keeping the
original AC-3. The saving depends on the station: WNET, which shares its mux with
five subchannels, already runs lean MPEG-2 (about 5.8 Mb/s), and H.264 saved only
15–30%. Stations that give their main channel 12–15 Mb/s save much more.

On a real 480i clip, analog H.264 came out at about 28% of the MPEG-2 size (0.85 vs
3.0 Mb/s, or about 0.4 GB/h instead of 1.4 GB/h). The HVR-1950's own MPEG-2
usually runs at a higher bitrate, so the saving there is larger. New DVR profiles
copy the storage path and file-name pattern from the default DVR profile; later
runs don't touch them. Analog channels have no EPG, so record with a **timer**
(DVR → Timers, or a manual entry) and pick the DVR profile there, or make it the
DVR profile of a user or access entry. The default DVR profile still records
MPEG-2 as received, with no CPU cost and no quality loss.

🎮 **VAAPI (Intel/AMD GPU) profiles** need the GPU passed into the container
(`devices: - /dev/dri:/dev/dri`; linuxserver's init gives TVHeadend's user the
render group by itself). The linuxserver image already ships both Intel VAAPI
drivers (`iHD` for Broadwell and newer, `i965` for Haswell and older). Each one first
tries an **all-GPU** chain: hardware MPEG-2 decode, `deinterlace_vaapi` (interlaced
frames only), hardware encode. On a UHD 630 that took 0.09 s of CPU per 8 s of
video, against 0.90 s for x264. If that doesn't work, it falls back to
deinterlacing on the CPU and encoding on the GPU (0.36 s). Which codecs a GPU can
encode varies:
- Haswell (the Dell OptiPlex 3020's HD 4600): H.264 only.
- Skylake and newer: H.264 and H.265.
- AV1 needs Intel Arc or Meteor Lake and newer.

The script's test encode finds out, and skips what the GPU can't do.
`--vaapi-device` picks another render node.

⚡ **Only created on machines fast enough for them.** Before creating these, the
script runs a 4-second test encode with the profile's exact settings. It creates
the profile only if that runs at least `--min-speed` times real time (default 2,
leaving headroom for a second client or a recording). `--all` skips the test. The
test encodes a synthetic interlaced MPEG-2 clip and reads about 0.6–0.8× of the
speed seen on a real 480i capture, so it errs on the cautious side.

On an i7-10700, real 480i capture (8 s), times real time:

| | 480 lines | upscaled to 720 lines |
|---|---|---|
| H.264 (x264 veryfast) | 35× | 14× (29.97p), 9.7× (59.94p) |
| H.265 (x265 veryfast) | 10× | 5.5× |
| AV1 (SVT-AV1 preset 10) | 10× | 5.8× |

A 4-core Haswell-class box like the Dell OptiPlex 3020 is roughly 4–5× slower, so
expect H.265, AV1 and 720p60 at 480 lines to pass and the upscaled H.265/AV1
variants (not offered) to fail.

On upscaling: it adds no detail, and TVs and Kodi boxes rescale just as well. The
720p60 profile earns its place through **field-rate deinterlacing**: each of the
59.94 fields per second becomes a frame, so motion is visibly smoother than 29.97p.
Use it for clients whose own scaler or deinterlacer is poor.

Implementation notes:
- **Most profiles are TVHeadend "MPEG-TS spawn" profiles.** TVHeadend pipes the
  channel through the container's ffmpeg (already there for ntsc-tvh). Two
  reasons:
  - TVHeadend's own libav MPEG-TS muxer is broken in current linuxserver images
    (`Unable to parse option value "nit"`; the stock `webtv-h264-aac-mpegts`
    profile fails the same way).
  - Spawn reaches encoders TVHeadend's transcoder lacks, such as SVT-AV1.
- **TVHeadend codec profiles need the H.264 profile left on *auto*.** This build
  passes a numeric profile that libx264 rejects (`x264 [error]: invalid profile:
  100`).
- **x265 prints `set_mempolicy: Operation not permitted` inside Docker.** Its NUMA
  thread pool is blocked by the default seccomp profile. It's harmless, and
  TVHeadend doesn't log spawn stderr. Disabling the pool would halve x265's speed.

---

## 6c. ATSC (digital) network in TVHeadend — get this right first

This is about TVHeadend's own digital tuning, not ntsc-tvh, but it's easy to get wrong
and hard to spot afterwards.

**One command does all of the below:**

```bash
docker exec tvheadend ntsc-tvh tvh-setup --atsc --dry-run   # show what it would change
docker exec tvheadend ntsc-tvh tvh-setup --atsc             # do it (the scan takes ~3 min)
docker exec tvheadend ntsc-tvh tvh-setup --atsc --status    # network, tuners, muxes, guide
```

It creates one ATSC network from the `us-ATSC-center-frequencies-8VSB` list (or uses
the one you have; it stops if there are two), switches the tuner on and links it
(`--tuner N` picks the DVB adapter when there is more than one), turns the PSIP guide
grabber on with a 120 s timeout, scans, switches off the muxes with no station and the
duplicate twins, maps the channels and starts a guide grab. A tuner that is also an
analog card gets **power save** on, so TVHeadend lets go of it when idle and ntsc-tvh
can use it. If the scan finds no station at all, it switches nothing off and tells you
to check the antenna. `--rescan` turns the switched-off muxes back on and scans again
(a new station, a better antenna). Re-running it only does what is missing.
Outside the US, `--mux-list NAME` picks another ATSC list (the table under *NTSC-M
countries* in §12); where none is bundled, or the country isn't ATSC, set it up by hand.

**1. Pick exactly one predefined mux list: `us-ATSC-center-frequencies-8VSB`.** When you
create the ATSC network, TVHeadend offers several US lists (they come from LinuxTV's
dtv-scan-tables). The undated one is the current band, RF 2–36. The dated ones
(`-062009`, `-072020`) list the band as it was before channels were cleared, up to
RF 51; their own headers say so. Don't add them.

**2. Never add a second list to the same network.** TVHeadend only merges muxes on
identical frequencies. The current list uses whole MHz (497.000 MHz), the dated ones
add the pilot offset (497.028615 MHz), so each station gets two muxes. Both scan, both
get services, only one gets channels. To rescan later, use **Force scan** on the
network, not another list.

**3. After the first scan, disable (or delete) the muxes that failed.** A mux with
scan result FAIL and no services does nothing except slow every later scan and guide
grab.

**4. Guide data: enable the PSIP grabber, not EIT, and shorten the OTA timeout.** Under
*Configuration → Channel/EPG → EPG Grabber Modules*, enable **Over-the-air: PSIP: ATSC
Grabber** (EIT is the European/DVB one). Under *EPG Grabber*, set the **OTA timeout** to
about **120 s**: the PSIP grabber never reports a mux as complete, so with the default
600 s each mux holds the tuner for 10 minutes. PSIP itself delivers in under a minute.
Then run **Force scan** on the network once. The grabber only revisits muxes on which it
has already seen guide tables, so until every mux has been tuned with PSIP enabled, the
scheduled grab keeps going back to the one or two you happened to watch.

What happened on the test setup that taught us this: one ATSC network had both lists,
so 81 muxes for 12 stations (62 failed, 7 duplicates). The scheduled guide grab sat
10 minutes on each dead mux and filled nothing. With the junk disabled, 13 muxes
remained, and with PSIP on and a 120 s timeout one minute on a single station gave 96
guide entries; the next scheduled grab still visited only that station. A Force scan
of the 13 muxes (80 s) brought it to 705 entries on 55 channels.

On a hybrid card that shares its tuner between analog and digital (the 800e), a
scheduled guide grab can retune it under an analog stream; see §11.

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

Analog "is this channel live?" is inherently fuzzy. Four strategies:

- **`auto`** (default) — the `video` test below, sped up on cards that report
  signal: once any channel has read above 0 %, channels reading exactly 0 % are
  skipped without grabbing frames. Cards that don't report signal idle (the 800e,
  which only reports while capturing) get the full picture check on every channel,
  and their scan output shows the capture-time reading; weak channels (a few %) are still
  checked. On the HVR-1950 with one live channel in us-bcast it found the same
  channel as `signal` and `video`.
- **`signal`** — reads the tuner's signal-strength % via
  `v4l2-ctl --get-tuner` and thresholds it. Fast (a few minutes for a full cable
  walk). **Caveat:** the strength figure from these tuners (tda18271 on the
  HVR-1950) is coarse and not calibrated; a strong-but-unmodulated carrier can
  read "active." Tune `SCAN_THRESHOLD` to your plant.
- **`video`** — grabs six frames (3 s) and judges the picture on three measures:
  - **contrast** (`var`, luma std-dev): rejects the flat raster a decoder shows
    without sync (black on the HVR-1950's cx25843).
  - **spatial noise** (`noise`): difference between vertically adjacent pixels,
    relative to contrast. Real images are locally smooth; snow is not. Only the
    vertical direction is used: analog snow is streaked along the scan lines, so
    it looks deceptively smooth horizontally.
  - **motion** (`motion`): frame-to-frame change relative to contrast. Snow is new
    every frame; most pictures change little in half a second.

  A channel counts as live if it has contrast **and** is either smooth
  (`noise < SCAN_NOISE_MAX`) or steady (`motion < SCAN_MOTION_MAX`). Snow fails
  both. This matters on tuners that show snow rather than a blank screen, such
  as the Pinnacle 800e (xc3028 + tvp5150). There the old contrast-only test
  marked every empty channel live.

  Calibration: 88 snow/noise samples from an 800e (two full 68-channel scans,
  20 captures and its unconnected S-Video input) scored `noise` ≥ 0.67 and
  `motion` ≥ 1.14. 16 live ATSC channels, downscaled the same way, all passed:
  - the busiest moving picture scored `noise` 0.53
  - pictures that needed the `motion` test scored ≤ 0.80
  - a game-show board with a ring of bulb dots scored `noise` 0.81 but passed on
    `motion` 0.12

  The defaults (0.60 / 0.9) sit between the two groups. In simulation, very
  noisy, fast-moving pictures (roughly σ ≥ 10 grey levels of added noise) can be
  missed; calm ones survive σ 20–30+. First real analog picture (an RF
  modulator's colour bars on us-bcast:4, 800e): `noise` 0.17, `motion` 0.01–0.03,
  so live; its neighbours 3 and 5 (snow) scored `noise` 0.69–0.83 and `motion`
  1.3–1.4, so empty. The HVR-1950 on the same picture: `noise` 0.17, `motion`
  0.01, live; its empty channels are black, so empty. `scan` prints `var`, `noise` and `motion` for every channel,
  so the thresholds can be tuned, and `examples/analog-check` (§14) repeats this
  check for one channel.
- **`both`** — require signal AND a picture. Fewest false positives, slowest.

Recommendation: keep `auto`. Use `signal` only for the fastest walk on a card whose
signal figure you trust, with a threshold calibrated against two or three channels
you *know* are live (`ntsc-tvh signal us-cable:23`). On a raw card like the 800e
every `signal` reading needs a short capture, so `signal` is no faster than `auto`
there. There is no magic here — it's a heuristic, and
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

## 9b. FM radio

On a card with an FM radio node, FM stations become channels in TVHeadend, as
audio-only streams marked as radio services. Today that means the **HVR-1950 with the
pvrusb2 FM radio patches**, which aren't in the kernel yet: with a stock kernel the
card has no `/dev/radio0`, and `doctor` says so. The patches are under review on the
linux-media list
([v2](https://lore.kernel.org/r/20261022232548.526232-1-stealth3m@yahoo.com));
until they are merged you have to build the cx25840, tda8290 and pvrusb2 modules
from them yourself.

Without extra firmware, FM is mono. **Stereo** needs Hauppauge's HVR-1950 audio
firmware, installed as `/lib/firmware/v4l-cx25840-fmstereo.fw`: the file
`Driver73/Hcw73MlC.rom` from Hauppauge's WinTV installer (16382 bytes, sha256
`a6505aee99fa71768daece0a7ced1ac1f60bf7f8d823707640778fe0a4fd0a81`). It isn't
redistributable, so it isn't included here or in linux-firmware.

FM isn't scanned. On the HVR-1950 the signal meter reads the IF AGC, which stays
high between stations in a busy band, so a scan can't tell stations from the gaps
between them. List the stations you want instead, with an optional name each:

```ini
# /etc/ntsc-tvh.conf
FM_STATIONS=88.3,98.7=ESPN,101.1=CBS-FM
```

`ntsc-tvh m3u` (or the next `scan`) adds them to the playlist in a "Radio" group,
and TVHeadend picks them up like the analog channels: `tvh-setup` scans and maps
them, and TVHeadend lists them as radio services (DVB service type 2). Check one
first:

```bash
ntsc-tvh signal fm:101.1      # signal % and whether a stereo pilot is there
ntsc-tvh stream fm:101.1 > fm.ts
```

- **The radio node is found automatically** on the same card as `DEVICE`. In Docker,
  map it in next to the video node (`- /dev/radio0:/dev/radio0`); it has no
  `/dev/v4l/by-id` link, so `doctor` tells you if it's missing.
- **The stream is audio only**: the card's MP2 audio copied as it is (or re-encoded
  per `AUDIO_CODEC`), in an MPEG-TS marked as a radio service.
- **One tuner**: FM, analog TV, composite and ATSC share it, so the tuner lock and
  `DVB_GUARD` apply to FM exactly as to analog TV.
- **"stereo" in `signal`** is the more reliable sign that a station is there; it
  needs the FM stereo firmware on the HVR-1950 (otherwise radio is mono).

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
| `PROBE_SECONDS` / `PROBE_SIZE` | `1` / `500000`           | HW-MPEG: stream ffmpeg analyses before output (blank = ffmpeg's ~5 s) |
| `RAW_ASPECT`           | `4:3`                            | display aspect of raw-card video (`16:9` for anamorphic) |
| `SCAN_VAR_MIN`         | `12`                             | video scan: min contrast (rejects flat screens) |
| `SCAN_NOISE_MAX`       | `0.60`                           | video scan: max spatial noise of a picture |
| `SCAN_MOTION_MAX`      | `0.9`                            | video scan: max frame-to-frame change of a picture |
| `DVB_GUARD`            | `1`                              | refuse analog while the same tuner's DVB side is busy |
| `SCAN_TABLES`          | `us-bcast,us-cable`              | plans walked by `scan`                    |
| `SCAN_METHOD`          | `auto`                          | `auto`/`signal`/`video`/`both` (§7)       |
| `SCAN_THRESHOLD`       | `35`                             | signal % cutoff                           |
| `SCAN_LOCK_WAIT`       | `5`                              | seconds to wait for the tuner lock        |
| `INPUTS_ALWAYS_ACTIVE` | `1`                              | list composite/svideo as channels         |
| `FM_STATIONS`          | *(none)*                         | FM radio channels, e.g. `98.7,101.1=CBS-FM` (§9b) |
| `RADIO_DEVICE`         | *(auto)*                         | FM radio node; auto = the one on `DEVICE`'s card |
| `SELF_PATH`            | *(auto)*                         | path written into M3U pipe:// URLs         |

### More than one card

Each tuner can stream one channel at a time, so two cards give you two channels at
once. Give the second card a name and a file `/etc/ntsc-tvh.d/NAME.conf` with at
least its `DEVICE`, then pass `--card NAME` to any command:

```bash
# /etc/ntsc-tvh.d/800e.conf
DEVICE=/dev/video1

ntsc-tvh --card 800e doctor
ntsc-tvh --card 800e scan          # or: ntsc-tvh --card 800e m3u  (line inputs only)
```

- The card file is read after `/etc/ntsc-tvh.conf` and the environment, so it wins.
  Any other key can go in it too (`RAW_AUDIO_DEV`, `SCAN_TABLES`, ...).
- Its state, playlist and tuner lock live in `/var/lib/ntsc-tvh/NAME/` unless the
  file sets `STATE_FILE`, `M3U_FILE` or `LOCK_FILE`. The cards never share a lock, so
  both can stream at once; each card's own stream/scan still exclude each other.
- Its playlist's URLs carry the card (`pipe:///usr/local/bin/ntsc-tvh --card 800e
  stream input:composite`) and its channels are labelled `Composite In (800e)`. The
  first card needs no name: without `--card` nothing changes, URLs included.
- `install-timer` installs `ntsc-tvh-scan-NAME.timer` for a named card.
- In TVHeadend, add a second **IPTV Automatic Network** whose URL is
  `file:///var/lib/ntsc-tvh/NAME/ntsc.m3u`, with **Maximum # input streams = 1** like
  the first one (one stream per tuner). Then scan it once, which creates the services,
  and map them to channels. Or let `ntsc-tvh --card NAME tvh-setup` do all of that.
- In Docker, mount the folder: `- /home/docker/stacks/tvheadend/ntsc-tvh.d:/etc/ntsc-tvh.d:ro`,
  and map both video devices (by `/dev/v4l/by-id/...` so a replug doesn't swap them).
  A `by-id` path can be missing when Docker starts at boot; see §11, "Container
  doesn't start after a reboot".

Tested with an HVR-1950 (no name) and an 800e (`--card 800e`) in one container: both
streamed through TVHeadend at the same time, each under its own lock.

---

## 11. Troubleshooting

The failure modes below are roughly in the order you'd hit them on a fresh
build. Several produce the *same* surface symptom ("empty stream / mux scan
fails"), so the trick is isolating the layer — work top-down.

**Tunes and reports signal, but every channel produces an empty stream.**
`ntsc-tvh doctor` now catches this (`[!!] capture test`).
This has several distinct causes that look identical; check them in order:

1. *`dmesg | grep -i pvrusb2` shows `firmware2 upload transfer failure`* — the
   MPEG **encoder** firmware didn't upload. Almost always an **xHCI / USB-3**
   host-controller issue. Move the tuner to a USB-2 port, interpose a powered
   USB-2 hub, or find a BIOS xHCI-mode option. On an xHCI-only PC the USB-2 ports
   don't help; only the hub does (see §2). A clean enumeration ends in
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

**`dmesg`: `pvrusb2: ***WARNING*** device's encoder appears to be stuck`.** Seen on
roughly one in three HVR-1950 stream starts in testing, followed by `Giving up on
command ... normally recovered via a firmware reload`. The driver reloads the
encoder firmware and the stream starts ~0.8 s later than usual; nothing else is
affected. Only worry if it repeats on every start or the stream then stays empty.

**`Operation not permitted` opening `/dev/video0`.** Not a sandbox issue and not
"device busy" — it's the pvrusb2 **analog/digital mutual exclusion**. TVHeadend
has the DVB (ATSC) interface open (for tuning, idle adapter management, or OTA
EPG), which puts the single front-end in digital mode and denies the analog
open. Either don't map `/dev/dvb` on this box, or disable the ATSC adapter **and
the OTA EPG grabber** in TVHeadend when you want analog. One tuner can't expose
both modes at once.

**em28xx cards (Pinnacle 800e): analog + ATSC at once wedges the tuner until
replug.** Unlike pvrusb2, em28xx does *not* refuse the second user. Two ways it
went wrong in testing:
- Composite streaming while TVHeadend ran an OTA EPG grab on an ATSC mux.
- An RF analog retune while an **idle** TVHeadend merely held `frontend0` open:
  the xc3028 flipped between analog and ATSC firmware every second.
- A composite capture while an idle TVHeadend held `frontend0` open (adapter
  power save off) -- no retune at all.

Either way the stick's I2C bus wedged. `dmesg` filled with `lgdt330x ...
i2c_read_demod_bytes ... error (ret == -6)`, TVHeadend logged `FE_READ_STATUS
error No such device or address`, and every analog tune failed with `tuner did
not tune ... 0.000 MHz`. Only unplugging the stick cleared it.

`ntsc-tvh` now refuses instead (`DVB_GUARD=1`, the default): `the digital (DVB)
side of this tuner is in use: tvheadend (pid N) has the DVB frontend open ...`.
Every analog channel, composite/S-Video included, is refused while anything
holds the DVB frontend open -- so on a hybrid card, turn on TVHeadend's **Power
save** for the ATSC adapter (it closes the frontend when idle). The check runs before ntsc-tvh opens the analog node at all: on the em28xx, even
`v4l2-ctl` opening `/dev/video0` switches the card to analog mode and broke a
live ATSC stream in testing. `doctor` reports the DVB state too, and skips its
device probe while the DVB side is open.

The other direction is only partly covered. TVHeadend doesn't know about
ntsc-tvh, so it can start a DVB subscription (a recording, a client, an EPG grab)
while an analog stream is running. `stream` checks every half second and stops the
analog stream as soon as the DVB side opens (`stopped the analog stream: ...` in
TVHeadend's log), but TVHeadend's first tune has already happened by then, so this
narrows the window rather than closing it. On a shared stick, avoid scheduling ATSC
while analog is in use, or leave ATSC to another tuner. To share the stick with TVHeadend's ATSC side, set the
adapter's **Power save** on (TVHeadend then closes the tuner when idle) and turn
off **Over-the-air EPG**, **Idle scan** and **Initial scan** for it
(**Configuration → DVB Inputs → TV adapters**), or disable the global over-the-air
EPG grabber modules. Analog and ATSC still can't be used *at the same time*.

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
Raw-frame cards whose decoder only reports while capturing (the 800e) are handled:
ntsc-tvh reads their signal during a short capture. For the rest, use
`SCAN_METHOD=auto` (the default) or `video`, which judge the picture.

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

**Container doesn't start after a reboot (`Exited (255)`).** `docker ps -a` shows
the TVHeadend container exited, and `docker inspect -f '{{.State.Error}}' tvheadend`
says `error gathering device information while adding custom device
"/dev/v4l/by-id/...": no such file or directory`. Docker started the container
before udev had created that `by-id` link (a tuner behind a USB hub can take a few
seconds longer), and Docker doesn't retry. `docker start tvheadend` fixes it until
the next boot. To make it stick, add a unit that waits for the tuner, e.g.
`/etc/systemd/system/tvheadend-start.service`:

```ini
[Unit]
Description=Start TVHeadend once its USB tuner is present
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStartPre=/bin/sh -c 'for i in $$(seq 90); do [ -e /dev/v4l/by-id/usb-YOUR-TUNER-video-index0 ] && exit 0; sleep 2; done'
ExecStart=/usr/bin/docker start tvheadend

[Install]
WantedBy=multi-user.target
```

Put your own `by-id` name in (`ls /dev/v4l/by-id/`), and add another `[ -e ... ]
&&` test for each mapped tuner. Then run `sudo systemctl enable tvheadend-start`.
`docker start` on a running container does nothing, so this is harmless when
Docker's own start worked. Docker reads the `by-id` link each time it starts the
container, so the cards stay correctly assigned even when the host numbers them the
other way round after a reboot.

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
  See [NTSC-M countries](#ntsc-m-countries) below for where the video standard fits.
- **Stable-URL contract** with the Automatic Network is load-bearing — honored
  here, but don't defeat it by templating the URLs yourself.
- **No set-top box control (yet).** The HVR-1950's IR blaster already works in the
  stock kernel (`/dev/lirc0`), but changing a cable/satellite box's channel before
  capturing its line output isn't implemented. What was checked and what it would
  need: [docs/future-ir-blaster.md](docs/future-ir-blaster.md).

### NTSC-M countries

ntsc-tvh sets the tuner to **NTSC-M** (`STANDARD=ntsc`), the analog standard of the
countries below, according to [World Population Review's list](https://worldpopulationreview.com/country-rankings/ntsc-countries)
(built from a 2017 source). That list gives each country's analog standard. It does
**not** say analog is still on the air, and in many of these countries analog
broadcasting has been switched off for digital. What still matters is the video:
VCRs, camcorders, game consoles, set-top boxes and RF modulators from these
countries put out NTSC-M.

| Country | Analog standard | TVHeadend predefined mux list to pick |
|---|---|---|
| United States | NTSC-M | `us-ATSC-center-frequencies-8VSB` (antenna, see [§6c](#6c-atsc-digital-network-in-tvheadend--get-this-right-first)); cable: `us-Cable-Standard-center-frequencies-QAM256`, or the `-IRC`/`-HRC` one your cable system uses |
| Canada | NTSC-M | `us-ATSC-center-frequencies-8VSB` (its header says "USA and Canada") |
| Mexico | NTSC-M | none bundled: add muxes by hand |
| Philippines | NTSC-M | none bundled: add muxes by hand |
| Colombia | NTSC-M | `co-All` (DVB-T2, 6 MHz) |
| Peru | NTSC-M | none bundled: add muxes by hand |
| Venezuela | NTSC-M | none bundled: add muxes by hand |
| Taiwan | NTSC-M | `tw-All` (DVB-T) |
| Chile | NTSC-M | none bundled: add muxes by hand |
| Guatemala | NTSC-M | none bundled: add muxes by hand |
| Ecuador | NTSC-M | none bundled: add muxes by hand |
| Bolivia | NTSC-M | none bundled: add muxes by hand |
| Dominican Republic | NTSC-M | none bundled: add muxes by hand |
| Honduras | NTSC-M | none bundled: add muxes by hand |
| Cuba | NTSC-M | none bundled: add muxes by hand |
| Nicaragua | NTSC-M | none bundled: add muxes by hand |
| Paraguay | NTSC-M | none bundled: add muxes by hand |
| El Salvador | NTSC-M | none bundled: add muxes by hand |
| Costa Rica | NTSC-M | none bundled: add muxes by hand |
| Panama | NTSC-M | none bundled: add muxes by hand |
| Trinidad and Tobago | NTSC-M | none bundled: add muxes by hand |
| Suriname | NTSC-M | none bundled: add muxes by hand |
| Myanmar | NTSC-M | none bundled: add muxes by hand |
| Cambodia | NTSC-M | none bundled: add muxes by hand |
| Jamaica | NTSC-M | none bundled: add muxes by hand |
| Barbados | NTSC-M | none bundled: add muxes by hand |
| Guam | NTSC-M | none bundled: add muxes by hand |
| South Korea | NTSC | antenna: none bundled: add muxes by hand; cable: `kr-Cable-8VSB` |
| Haiti | NTSC | none bundled: add muxes by hand |
| Belize | NTSC | none bundled: add muxes by hand |
| Saint Lucia | NTSC | none bundled: add muxes by hand |
| Antigua and Barbuda | NTSC | none bundled: add muxes by hand |
| Greenland | NTSC / PAL-B | none bundled: add muxes by hand |
| Japan | NTSC-J (listed as NTSC-M on that page; no 7.5 IRE black setup, own channel frequencies) | none bundled: add muxes by hand |

Not NTSC, despite being in the Americas: Brazil (PAL-M), Argentina and Uruguay
(PAL-N).

**The mux list column is for TVHeadend's digital channels** (*Configuration → DVB Inputs
→ Networks → Add*, then **Pre-defined muxes**). Digital TV uses each country's digital
system, not its old analog one, so the list is picked per country, not per analog
standard. It names the files this TVHeadend build ships (LinuxTV's dtv-scan-tables, in
`/usr/share/tvheadend/data/dvb-scan/`); pick one, never two (§6c). Where it says **none
bundled**, create the network with no predefined muxes and add them under *Muxes* from
the country's official channel/frequency list.

What this means for ntsc-tvh:
- **Composite and S-Video inputs** work with NTSC-M video from any of these countries.
- **RF channels** use the US tables (`us-bcast`, `us-cable`, `us-cable-irc`,
  `us-cable-hrc`). Countries that copied the US channel plan line up; others need
  their own table, and Japan's channels sit on different frequencies entirely.

---

## 13. Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## 14. Development & tests

### How this was made

ntsc-tvh was written with an AI coding assistant (Claude, by Anthropic): it wrote
most of the code, the tests and this documentation, and ran and evaluated the
hardware tests on the cards listed in §2b. I reviewed the changes, tested them on
real hardware and am responsible for the result. Commits it helped with carry a
`Co-Authored-By: Claude` line.

### Running the tests

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

The `*-800e.txt` fixtures were captured from a real Pinnacle 800e (em28xx), and
the HVR-1950 ones (`v4l2-all-hvr1950.txt`, `v4l2-list-inputs-hvr1950.txt`,
`v4l2-get-tuner-nosignal.txt`) from the real card (kernel 7.0, tuned to us-bcast:7
with no analog carrier), and `v4l2-get-tuner-locked.txt` from it locked on an RF
modulator's test pattern on us-bcast:4 (100 %, AFC 0). `analog-check` (below) saves
the same `--get-tuner` output for any channel you check.

### Checking a real analog channel: `examples/analog-check`

The unit tests can't tell whether a real picture gets through. When you have one (a
live channel, an RF modulator, a camera on Composite), run this where ntsc-tvh runs,
with nothing else using the tuner:

```bash
docker exec -u abc tvheadend /opt/ntsc-tvh/examples/analog-check us-bcast:4
docker exec -u abc tvheadend /opt/ntsc-tvh/examples/analog-check input:composite \
    --tvh http://localhost:9981 --tvh-channel "Composite In"      # + every analog-* profile
```

It checks the device, tuning (frequency read-back, signal %), the video scan's verdict
(CHANNEL must count as a picture, its RF neighbours must not; `--empty` to pick others),
`ntsc-tvh stream` (time to first byte, the streams in it) and, with `--tvh`, each
matching stream profile through TVHeadend. A missing picture is reported but doesn't
stop the stream checks, so a black or snowy stream points at the signal; a device or
tuning error does stop them. Pass `-e DEVICE=/dev/video1` (etc.) for another card. It
writes `analog-check-<time>/` (or `--out`): `summary.json`, a still from each stream,
the scan's frames as `.gray` + a `.png` contact sheet, and the raw `v4l2-ctl
--get-tuner` output. Those `.gray` and `get-tuner-*.txt` files are ready to copy into
`tests/fixtures/` (e.g. `v4l2-get-tuner-locked.txt` once a tuner reports signal on a
live channel). Exits 1 if anything failed.

Tests marked `@unittest.expectedFailure` record known bugs. When a fix lands, the
test passes unexpectedly and the run fails until you remove the decorator.
