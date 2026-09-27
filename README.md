# LapseCam — Arch Linux

Webcam-only timelapse recorder. Samples the camera at the selected
time-compression rate and streams the selected frames straight into a
compressed H.264 MP4. No audio, no normal-speed source video, no frame folders.

## Run

    python TimelapseRecorder.py

Shortcuts: Ctrl+R start · Esc stop · F11 fullscreen preview.

## Speed and frame rate

The output plays at the frame rate you pick — **24, 30, or 60 fps** — and
LapseCam selects source frames at:

`real-time capture interval = speed ÷ fps`

Time compression stays equal to `speed` at any fps; a higher fps just samples
more densely (smoother motion, roughly proportionally larger files).

| Speed (30 fps) | Source sampling | One real minute becomes |
|---:|---:|---:|
| 2× | 15 frames/sec | 30 sec of video |
| 10× | 3 frames/sec | 6 sec of video |
| 100× | 1 frame every 3.33 sec | 0.6 sec of video |

- **Speed is changeable mid-recording**; the new rate applies to the next
  selected frame.
- **Frame rate is fixed per recording** (the encoder's input rate is chosen
  when it starts). Change it between recordings.
- The end-of-day recap re-samples all sessions to 30 fps; each clip's
  duration is preserved exactly.

## Storage behaviour

- No audio, no raw video, no still-image folders — every selected frame is
  compressed in memory and handed straight to the H.264 encoder.
- Output is a fragmented MP4 while recording; finalized and renamed on stop.
- While recording, LapseCam holds a systemd-logind inhibitor
  (`systemd-inhibit --list` shows it) so the machine stays awake, and stops
  safely if the selected drive falls below 1 GB free.

A leftover `__recording.mp4` (after a crash/kill) can often be recovered:
`ffmpeg -i file__recording.mp4 -c copy recovered.mp4`.

## Camera checklist

1. `ls -l /dev/video*` — many webcams expose two nodes (e.g. `video0` capture,
   `video1` metadata); OpenCV's "Camera N" maps to the Nth capture-capable node.
2. `v4l2-ctl --list-devices` (v4l-utils) shows which node belongs to which camera.
3. Permissions: desktop sessions usually get access via systemd-logind's
   uaccess ACL. Otherwise: `sudo usermod -aG video $USER` and re-login.
4. Close other apps holding the camera (browsers, OBS, Cheese, guvcview).

## Linux notes

- The app prefers the system `ffmpeg`; the `imageio-ffmpeg` wheel is only a
  fallback when no system ffmpeg is found.
- Wayland: Tk runs through XWayland, so everything works; tray/fullscreen
  quirks there are the usual XWayland caveats.
- Exposure lock: on V4L2 the slider is in milliseconds. Locking manual
  exposure is also the fix for dim-room webcams that drop to ~10 fps.

## Colour schemes

Six schemes under **Appearance → Colour scheme**: Midnight (default), Nord,
Dracula, Gruvbox, Solarized Dark, and Paper (light). Switching applies live
and is remembered in `~/.lapsecam/config.json`.
