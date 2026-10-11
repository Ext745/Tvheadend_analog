# Making your own TVHeadend stream and codec profiles

`examples/tvh-stream-profiles` creates a tested set of profiles (README §6b). This
guide is for making your own: a different quality, a filter (denoise, crop,
loudness), a client the set doesn't cover, or a GPU setting that suits your
hardware better. Every example command here was run on this project's test setup:
linuxserver/tvheadend 4.3, its ffmpeg 6.1, an HVR-1950-style 480i source, and
ATSC 1080i/720p.

## 1. Three kinds of profile

| Kind | Where in TVHeadend | What it decides |
|---|---|---|
| **Stream profile** | Configuration → Stream → Stream Profiles | How a channel is delivered to a client: as received (`pass`, `htsp`), through TVHeadend's own transcoder, or through an external command |
| **Codec profile** | Configuration → Stream → Codec Profiles | Encoder settings (codec, size, bitrate, deinterlacing) that a *Transcode* stream profile uses |
| **DVR profile** | Configuration → Recording → Digital Video Recorder Profiles | How recordings are made, including which stream profile they go through |

A client picks a stream profile with `?profile=NAME` on the stream URL
(`http://host:9981/stream/channelnumber/4.1?profile=NAME`). A user or access entry
can also have a default one (Configuration → Users → Access Entries → Streaming
profiles), and a recording uses its DVR profile's stream profile.

## 2. Two ways to build a transcoding profile

| | **A. Built-in transcoder** | **B. Spawn profile (ffmpeg command)** |
|---|---|---|
| Set up with | a codec profile plus a *Transcode/av-lib* stream profile | one *MPEG-TS Spawn* stream profile containing an ffmpeg command line |
| Encoders | what TVHeadend's transcoder lists (x264, x265, VAAPI, AAC, Opus…) | anything the container's ffmpeg has (adds SVT-AV1, every filter) |
| Filters | deinterlace, scale | any ffmpeg filter (denoise, crop, loudness…) |
| Output containers | Matroska works; **MPEG-TS is broken** in current linuxserver builds | anything ffmpeg writes: MPEG-TS, Matroska, WebM |
| Best for | simple Matroska profiles, set up entirely in the web UI | everything else; all shipped MPEG-TS/WebM profiles use this |

### Known problems in this TVHeadend build (4.3, linuxserver)

- **The built-in MPEG-TS output fails.** The stream returns 0 bytes, and the log
  says `MPEGTS muxer: Unable to parse option value "nit"`. The stock
  `webtv-h264-aac-mpegts` profile fails the same way. Use Matroska with the
  built-in transcoder, or a spawn profile for MPEG-TS.
- **H.264 "Profile" must stay on *auto*** in a libx264 codec profile. With High or
  Main selected, TVHeadend passes a number libx264 rejects (`x264 [error]: invalid
  profile: 100`), and the stream has no video. Auto picks High anyway.
- **GPU (VAAPI) H.264/H.265 fails on captioned ATSC** (`Encode failed: -28`): the
  encoder tries to copy the A/53 closed captions. In a spawn profile, add `-sei
  identifier+timing+recovery_point` (h264_vaapi) or `-sei hdr` (hevc_vaapi). This
  drops the captions; the software encoders keep them.

## 3. Way A: built-in transcoder, all in the web UI

1. **Codec profile:** Configuration → Stream → Codec Profiles → Add, then pick the
   codec (`libx264`).
   - *Height:* `0` keeps the source size. A number scales, keeping the aspect.
   - *Deinterlace:* on. *Deinterlace fields only:* **Enable**, so already
     progressive video (720p, the 800e's output) passes through untouched.
   - *Deinterlace rate type:* Frame Rate (1080i becomes 29.97p). Field Rate gives
     59.94p: smoother motion, about twice the CPU.
   - *Profile:* auto (see above). *Preset:* `veryfast`. *Constant Rate Factor:* 22
     for analog SD, 25 for HD. Lower is better quality and bigger.
   - *GOP size:* 60 (a keyframe every 2 s: quick start and seeking).
2. **Audio codec profile:** Add → `aac`. *Channel layout* stereo, *Bitrate* 160,
   *Limit audio tracks* 1.
3. **Stream profile:** Configuration → Stream → Stream Profiles → Add → **Transcode/
   av-lib**. *Container:* **Matroska (mkv)/built-in**. *Video codec profile* and
   *Audio codec profile:* the two above.
4. Test it (§7).

## 4. Way B: spawn profile (ffmpeg)

Configuration → Stream → Stream Profiles → Add → **MPEG-TS Spawn/built-in**.

- **Command line:** the ffmpeg command (rules below).
- **Mime type:** `video/mp2t` for MPEG-TS, `video/x-matroska` for Matroska,
  `video/webm` for WebM.

TVHeadend pipes the channel's MPEG-TS into the command's stdin and sends its
stdout to the client. Rules for the command line:

- **Read `pipe:0`, write `pipe:1`.**
- **No shell and no quotes.** TVHeadend splits the command on spaces itself. A
  filter like `bwdif=0:-1:1,scale=-2:540` is fine. Anything needing quotes isn't.
  To set an environment variable, prefix the command with `env NAME=value`.
- **Start with** `ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0`.
- **`-ac 2`** downmixes to stereo. Without it, AAC keeps a digital channel's 5.1.
- **`-map 0:v:0 -map 0:a:0?`** takes the video and the first (main) audio track. The
  `?` keeps it working on a channel with no audio. Use `-map 0:a` instead for all
  audio tracks.
- **TVHeadend doesn't log the command's stderr,** so test the command by hand
  first (§7).

Template:

```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf FILTERS VIDEO-OPTIONS AUDIO-OPTIONS -f mpegts pipe:1
```

## 5. Recipes

All were tested as spawn command lines. Each is one line in the *Command line*
field.

**Fix audio only, leave the video untouched.** This suits a player that shows the
picture but has no sound (no MP2 or AC-3 decoder). It costs almost no CPU and
loses no picture quality:
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -c:v copy -c:a aac -ac 2 -b:a 160k -f mpegts pipe:1
```

**Analog: denoise weak reception.** `hqdn3d` before the encoder removes snow
that would otherwise waste bits. It made a lightly noisy clip 6% smaller, and
noisier reception gains more. Raise the numbers for stronger denoising, which
eventually smears detail:
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf bwdif=0:-1:1,hqdn3d=2:1:3:3 -c:v libx264 -preset veryfast -crf 22 -g 60 -pix_fmt yuv420p -c:a aac -ac 2 -b:a 160k -f mpegts pipe:1
```

**Analog: crop overscan.** Analog pictures have noisy edges, and VBI lines at
the top, that a CRT hid. This crops 8 pixels each side (704×464). The picture
keeps its correct shape: what's left is slightly narrower than 4:3, exactly as
it was on the original screen.
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf bwdif=0:-1:1,crop=704:464:8:8 -c:v libx264 -preset veryfast -crf 22 -g 60 -pix_fmt yuv420p -c:a aac -ac 2 -b:a 160k -f mpegts pipe:1
```

**Even out loudness** between channels and adverts (EBU R128 target −23 LUFS).
The video is copied:
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -c:v copy -af loudnorm=I=-23:TP=-2:LRA=11 -c:a aac -ac 2 -ar 48000 -b:a 160k -f mpegts pipe:1
```

**Low latency** for channel surfing. Expect bigger output at the same CRF:
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf bwdif=0:-1:1 -c:v libx264 -preset veryfast -tune zerolatency -crf 25 -g 30 -pix_fmt yuv420p -c:a aac -ac 2 -b:a 160k -f mpegts pipe:1
```

**Matroska for a browser** (mime type `video/x-matroska`):
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf bwdif=0:-1:1 -c:v libx264 -preset veryfast -crf 25 -g 60 -pix_fmt yuv420p -c:a aac -ac 2 -b:a 160k -f matroska pipe:1
```

**Keep 5.1 surround** on digital channels by copying the AC-3 (Kodi, TVs, home
theatres):
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 -map 0:v:0 -map 0:a:0? -vf bwdif=0:-1:1 -c:v libx264 -preset veryfast -crf 25 -g 60 -pix_fmt yuv420p -c:a copy -f mpegts pipe:1
```

**Encode on the GPU (VAAPI).** This needs `/dev/dri` passed into the container.
All on the GPU (decode, deinterlace, encode), it takes about a tenth of x264's CPU:
```
ffmpeg -hide_banner -loglevel error -fflags +genpts -hwaccel vaapi -hwaccel_device /dev/dri/renderD128 -hwaccel_output_format vaapi -i pipe:0 -map 0:v:0 -map 0:a:0? -vf deinterlace_vaapi=auto=1 -c:v h264_vaapi -sei identifier+timing+recovery_point -qp 28 -g 60 -c:a copy -f mpegts pipe:1
```
If your GPU can't deinterlace, deinterlace on the CPU and upload:
`-vaapi_device /dev/dri/renderD128 -i pipe:0 … -vf bwdif=0:-1:1,format=nv12,hwupload -c:v h264_vaapi …`.
To scale on the GPU, add `,scale_vaapi=w=-2:h=540` after the deinterlacer.

### Quality knobs, at a glance

| Encoder | Quality setting | Sensible range (lower = better and bigger) | Speed setting |
|---|---|---|---|
| x264 | `-crf` | analog 20–23, HD 23–27 | `-preset veryfast` (live), `faster` (recordings) |
| x265 | `-crf` | +3 over x264 for similar quality | `-preset veryfast`; add `-x265-params log-level=error` |
| SVT-AV1 | `-crf` | 35–40 | `-preset 10` (live; 12 is faster) |
| h264_vaapi / hevc_vaapi | `-qp` | about x264 CRF + 3 | (fixed) |
| bitrate cap, any encoder | `-b:v 1500k -maxrate 2000k -bufsize 3000k` | for mobile and remote viewing | |

## 6. A DVR profile that uses your stream profile

Configuration → Recording → Digital Video Recorder Profiles → Add. Set *Stream
profile* to yours, copy *Recording system path* and *Filename* from your default
DVR profile, and save. Pick the DVR profile when you create a timer or recording,
or make it the default for a user or access entry. Use MPEG-TS output for
recordings, so a recording that is cut short stays playable.

## 7. Testing a profile

1. **Test the command by hand** inside the container, on a few seconds of a real
   channel. Feed it the way TVHeadend will:
   (`curl --max-time` always ends a live stream with exit code 28; that's expected.)
   ```bash
   curl -s --max-time 10 -o sample.ts 'http://localhost:9981/stream/channelnumber/4.1?profile=pass'
   docker cp sample.ts tvheadend:/tmp/
   docker exec -u abc tvheadend sh -c 'ffmpeg -hide_banner -loglevel error -fflags +genpts -i pipe:0 … -f mpegts pipe:1 < /tmp/sample.ts > /tmp/out.ts; echo rc=$?'
   docker exec tvheadend ffprobe -v error -show_entries stream=codec_name,width,height,display_aspect_ratio,avg_frame_rate,channels -of compact /tmp/out.ts
   ```
   Errors appear here, where TVHeadend would hide them. Lines like `Invalid
   frame dimensions 0x0` or `MVs not available` at the very start are normal: the
   sample begins mid-stream.
2. **Test it through TVHeadend:**
   ```bash
   curl -s --max-time 10 -o out.ts 'http://localhost:9981/stream/channelnumber/4.1?profile=YOUR-PROFILE'
   ffprobe out.ts
   ```
   0 bytes means the profile failed. Look at `docker logs tvheadend`, and for spawn
   profiles run the command by hand (step 1).
3. **Measure it** if you care about size, CPU or quality:
   - Size: output bytes against `?profile=pass` bytes for the same seconds.
   - CPU: add `-benchmark` and change `-loglevel error` to `info` in a hand run.
     It prints `utime=` (CPU seconds) and `rtime=` (wall seconds).
   - Quality: SSIM against the deinterlaced source,
     `ffmpeg -i out.ts -i ref.mkv -lavfi "[0:v][1:v]ssim" -f null -`. MPEG-TS output
     decodes one frame later than its source, so compare at a frame offset of 0–3
     (`trim=start_frame=N` on the reference) and keep the best. Otherwise every TS
     profile looks about 0.02 worse than it is.

## 8. Adding a profile to `tvh-stream-profiles`

To make your profile part of the scripted set, so it is created, updated and
removed with the others and gated on speed, add an entry to `SPAWN` in
`examples/tvh-stream-profiles`:

```python
{"name": "analog-h264-denoise", "gated": False, "mime": "video/mp2t", "fmt": "mpegts",
 "comment": "Analog: denoised H.264/AAC (weak reception)",
 "vf": "bwdif=0:-1:1,hqdn3d=2:1:3:3", "venc": f"{X264} -crf 22 -g 60", "aenc": AAC.format(160)},
```

- `"gated": True` creates it only if a test encode runs at `--min-speed` times
  real time.
- `"source": "digital"` makes it a digital profile (tested on a 1080i clip, and
  skipped with `--sources analog`).
- `"variants": [...]` lists alternatives to try in order (see the VAAPI entries).
- To record through it, add an entry to `DVR`.

Run `--dry-run` first, then for real, then run `python3 -m unittest discover -s
tests`. The tests check every spawn command follows the rules in §4.

Without the script, the same thing through TVHeadend's API:
```bash
curl -s --data-urlencode 'class=profile-mpegts-spawn' \
     --data-urlencode 'conf={"name":"my-profile","comment":"…","mime":"video/mp2t","enabled":true,"cmdline":"ffmpeg -hide_banner … pipe:1"}' \
     http://localhost:9981/api/profile/create
```
To change an existing profile through the API (`api/idnode/save`), send **every
list-type field** of the node. Fields left out of a save are reset; this cleared a
DVB adapter's network list in testing.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Stream is 0 bytes, log: `Unable to parse option value "nit"` | built-in MPEG-TS muxer bug | Matroska container, or a spawn profile |
| No video, log: `x264 [error]: invalid profile` | codec profile *Profile* set to High/Main | set it to auto |
| `Encode failed: -28` (VAAPI) | captions on ATSC | `-sei identifier+timing+recovery_point` (h264) / `-sei hdr` (hevc) |
| `set_mempolicy: Operation not permitted` | x265 NUMA pool inside Docker | harmless; TVHeadend doesn't log spawn stderr |
| VAAPI: `Impossible to convert between the formats` | source isn't 4:2:0 MPEG-2 (e.g. 4:2:2) or hardware decode unsupported | use the CPU-deinterlace + `hwupload` variant |
| Picture too wide or too narrow | source aspect wrong, or scaled to fixed square-pixel sizes | keep `-2` in scale widths; check `display_aspect_ratio` with ffprobe |
| Interlace combing on motion | no deinterlacer, or "fields only" off on a progressive-flagged source | `bwdif=0:-1:1` / `deinterlace_vaapi=auto=1`; check `field_order` |
