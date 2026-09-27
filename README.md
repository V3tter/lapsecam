# LapseCam — Arch Linux

Webcam-only timelapse recorder. Samples the camera at the selected
time-compression rate and streams the selected frames straight into a
compressed H.264 MP4. No audio, no normal-speed source video, no frame folders.

## Install

Arch Linux. The git clone *is* the installation — updates are just `git pull`.

### One-liner

~~~bash
curl -fsSL https://raw.githubusercontent.com/V3etter/lapsecam/main/install.sh | bash
~~~

Clones to `~/LapseCam`, installs the required pacman packages (asks first,
uses `sudo`), and sets up the `lapsecam` terminal command plus an
application-menu entry.

### From a clone

~~~bash
git clone https://github.com/V3etter/lapsecam.git
cd lapsecam
./install.sh
~~~

### Options

| Option | Effect |
|---|---|
| `--target DIR` | Install somewhere other than `~/LapseCam` |
| `--no-deps` | Skip pacman entirely |
| `--no-optional` | Skip the optional-extras and tray prompts |
| `--with-tray` | Non-interactive: also set up the tray-icon venv |

Options work with both methods, e.g. `curl -fsSL … | bash -s -- --no-deps`.

### What ends up where

| Path | What |
|---|---|
| `~/LapseCam/` | The app (this clone) |
| `~/.local/bin/lapsecam` | Terminal launcher — `lapsecam`, or `lapsecam -d` to detach |
| `~/.local/share/applications/lapsecam.desktop` | Application-menu entry |
| `~/.lapsecam/` | Your history & settings (survives updates and uninstall) |
| `~/Videos/Timelapses/` | Recordings |

Required packages (installed by the script):
`python tk python-opencv python-numpy python-pillow ffmpeg xdg-utils`.
Optional: `ttf-dejavu` (overlay fonts), `libcanberra` (session chime),
`v4l-utils` (camera debugging). The tray icon additionally needs
`python-gobject gtk3 libayatana-appindicator` + `pystray` — the installer
sets all of that up in a private venv if you say yes.

### Update

~~~bash
cd ~/LapseCam && git pull && ./install.sh --no-deps
~~~

### Uninstall

~~~bash
rm -rf ~/LapseCam \
       ~/.local/bin/lapsecam \
       ~/.local/share/applications/lapsecam.desktop
~~~

Recordings and `~/.lapsecam` data are left untouched.

## Run

~~~bash
lapsecam        # terminal launcher; -d detaches
~~~

or without installing: `python TimelapseRecorder.py`

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

## Colour schemes

Six schemes under **Appearance → Colour scheme**: Midnight (default), Nord,
Dracula, Gruvbox, Solarized Dark, and Paper (light). Switching applies live
and is remembered in `~/.lapsecam/config.json`.

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
