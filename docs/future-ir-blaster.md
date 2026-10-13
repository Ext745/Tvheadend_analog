# Future idea: changing a set-top box's channel with the HVR-1950 IR blaster

Status: **not started** — parked 2026-10-13 because it needs hardware set up first.
This note records what was checked so the work can pick up from here.

## The idea

A cable or satellite box feeds the HVR-1950's composite or S-Video input. Before
ntsc-tvh starts capturing a line-input channel, it sends the box the channel number
through the card's IR blaster, the way a DVR controls a set-top box. Each line-input
mux in TVHeadend would then stand for one channel on the box instead of "whatever the
box is currently showing".

## What already works (checked 2026-10-13, Ubuntu kernel 7.0, stock drivers)

The blaster needs **no driver work**. The stock kernel sets it up when the card loads:

```
ir-kbd-i2c 14-0071: Zilog/Hauppauge IR blaster firmware version 2.1.0
rc rc0: lirc_dev: driver ir_kbd_i2c registered at minor = 0, scancode receiver, raw IR transmitter
```

- The card's IR chip is a Zilog Z8F0811: receiver at I2C 0x71, transmitter at 0x70.
  pvrusb2 binds `ir_z8f0811_haup` at 0x71, and `ir-kbd-i2c` adds the transmitter at
  0x70 itself (`rc->tx_ir = zilog_tx`, carrier settable).
- The transmitter is `/dev/lirc0` (the rc device named "WinTV HVR-1950 Model 751xx").
  The minor number can change; find it by name under `/sys/class/rc/*/`.
- The receiver only reports decoded RC-5/RC-6 scancodes (Hauppauge remotes), not raw
  IR, so **the card cannot learn another remote's codes**.
- The **Pinnacle 800e** (`em28xx`, "Pinnacle PCTV HD Pro Stick") also has IR, but
  **receive only**: the kernel registers it as "scancode receiver, no transmitter"
  (`/dev/lirc1` here; protocols RC-5 and NEC). It can't blast, and it can't learn raw
  codes either. A box feeding the 800e's line input would still be driven by the
  HVR-1950's blaster (emitter placed so it reaches that box).

## What is needed before starting

1. **Hardware:** a set-top box, and the Hauppauge IR emitter cable plugged into the
   HVR-1950's IR-out jack, with the emitter stuck over the box's IR window.
2. **The box's remote codes:** from a public IR code database (LIRC remotes,
   irdb), or captured with a raw-IR receiver. The HVR-1950 cannot capture them.
3. **A sending tool:** `ir-ctl` (Ubuntu package `ir-keytable`; not installed on the
   workbench yet). First test: `ir-ctl -d /dev/lirc0 --features`, then send one
   known code.
4. **Permissions:** `/dev/lirc0` is `root:video` 0660 with no uaccess ACL, unlike the
   video nodes. The user running ntsc-tvh needs the `video` group (or a udev rule),
   and the Docker container needs the device passed through (`--device`).

## Rough design

- Per line-input channel, a config entry: box channel number and an IR code set
  (digit keys plus an optional "enter" key and per-key delay).
- In the stream path, before opening the line input: send the digits with `ir-ctl`,
  then wait for the box to settle (a few seconds) before capture starts.
- Take the existing per-card lock first, so two streams can't fight over the box.
- Failure to send is logged but doesn't block the stream: the box just stays on its
  current channel.

## Open questions

- Does the Zilog reliably send codes that aren't RC-5/RC-6 (NEC, Sony and other
  protocols most boxes use)? The driver re-encodes raw IR into the chip's own format
  and refuses signals that are too long or have too many distinct pulse/space lengths
  ("IR too long, cannot transmit"). Needs testing with the real box's codes.
- Does sending IR (same I2C bus) disturb a capture already running on the card?
