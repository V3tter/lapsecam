"""LapseCam: a webcam-only timelapse recorder for Linux (Arch) and Windows.

Frames are sampled from the webcam at a rate derived from the selected time
compression, then streamed straight into H.264.  The app never keeps a normal
speed source video or a folder full of still images on disk.

Also includes: a selectable output frame rate (24/30/60 fps), timed study
sessions, a session history with streak tracking, a focus clock burned into
the preview, end-of-day recap videos, an optional system-tray icon, and a
fullscreen preview.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
try:
    from imageio_ffmpeg import get_ffmpeg_exe
except Exception:  # Optional on Linux when the system ffmpeg is installed.
    get_ffmpeg_exe = None
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont

try:
    import pystray
    HAVE_PYSTRAY = True
except Exception:  # Tray is optional; the app runs fine without it.
    HAVE_PYSTRAY = False


APP_NAME = "LapseCam"
OUTPUT_FPS = 30                      # default output frame rate
OUTPUT_FPS_CHOICES = (24, 30, 60)
MIN_SPEED = 2.0
MAX_SPEED = 10_000.0
MIN_FREE_BYTES = 1 * 1024**3
PREVIEW_SIZE = (720, 405)
# Camera frames are pre-shrunk to at most this in the camera thread, so the
# UI thread never processes a full 1080p frame just to draw a preview.
PREVIEW_SOURCE_MAX = (1280, 720)
HISTORY_PATH = Path.home() / ".lapsecam" / "history.json"
CONFIG_PATH = Path.home() / ".lapsecam" / "config.json"
# A day keeps the streak alive only if at least this much was recorded.
MIN_STREAK_MINUTES = 5.0

QUALITY = {
    "Storage Saver": {"crf": 30, "jpeg": 84, "estimate_mbps": 1.15},
    "Balanced": {"crf": 24, "jpeg": 89, "estimate_mbps": 2.05},
    "Detail": {"crf": 19, "jpeg": 94, "estimate_mbps": 4.0},
}

# ---------------------------------------------------------------------------
# Colour schemes. "Midnight" is the original LapseCam look; the rest are
# built from well-known palettes plus one light scheme. To add your own,
# copy any entry and swap the hex values — every key must exist.
# ---------------------------------------------------------------------------
THEMES: dict[str, dict[str, str]] = {
    "Midnight": {
        "root": "#101827", "panel": "#172236", "preview": "#0a1020",
        "entry_bg": "#101a2a",
        "button": "#243550", "button_active": "#304664", "button_disabled": "#1b293d",
        "button_fg": "#dce8f7",
        "accent": "#36c997", "accent_active": "#51d9aa",
        "accent_disabled": "#2c5960", "accent_fg": "#071912", "accent_fg_disabled": "#b3d3ca",
        "danger": "#db5964", "danger_active": "#ed6e78", "danger_disabled": "#71353d",
        "danger_fg": "#ffffff",
        "title": "#f7fbff", "subtitle": "#9fb0c8", "section": "#dce8f7", "body": "#e7edf7",
        "preview_text": "#a8bbd2", "status": "#b8c8dc",
        "field_fg": "#ecf4ff", "arrow": "#c7d8ed", "insert": "#ecf4ff",
        "recording": "#46d6a2", "paused": "#f3bf5a", "ready": "#9fb0c8",
    },
    "Nord": {
        "root": "#2E3440", "panel": "#3B4252", "preview": "#262B36",
        "entry_bg": "#2A2F3A",
        "button": "#434C5E", "button_active": "#4C566A", "button_disabled": "#353C4A",
        "button_fg": "#D8DEE9",
        "accent": "#88C0D0", "accent_active": "#97CBDA",
        "accent_disabled": "#4E5A66", "accent_fg": "#23272F", "accent_fg_disabled": "#9FB6C0",
        "danger": "#BF616A", "danger_active": "#CF7880", "danger_disabled": "#6E454D",
        "danger_fg": "#ECEFF4",
        "title": "#ECEFF4", "subtitle": "#92A0B6", "section": "#D8DEE9", "body": "#E5E9F0",
        "preview_text": "#A7B3C6", "status": "#B8C4D6",
        "field_fg": "#ECEFF4", "arrow": "#C7D2E2", "insert": "#D8DEE9",
        "recording": "#A3BE8C", "paused": "#EBCB8B", "ready": "#92A0B6",
    },
    "Dracula": {
        "root": "#282A36", "panel": "#343746", "preview": "#1E2029",
        "entry_bg": "#22242E",
        "button": "#44475A", "button_active": "#565B73", "button_disabled": "#363948",
        "button_fg": "#F8F8F2",
        "accent": "#50FA7B", "accent_active": "#6BFB8D",
        "accent_disabled": "#3E6B4E", "accent_fg": "#10241A", "accent_fg_disabled": "#A9E5B9",
        "danger": "#FF5555", "danger_active": "#FF6E6E", "danger_disabled": "#7A3A3A",
        "danger_fg": "#F8F8F2",
        "title": "#F8F8F2", "subtitle": "#9AA5D1", "section": "#F8F8F2", "body": "#E9E9E4",
        "preview_text": "#A9B1D6", "status": "#B6BDDC",
        "field_fg": "#F8F8F2", "arrow": "#C3C9E8", "insert": "#F8F8F2",
        "recording": "#50FA7B", "paused": "#F1FA8C", "ready": "#9AA5D1",
    },
    "Gruvbox": {
        "root": "#282828", "panel": "#3C3836", "preview": "#1D2021",
        "entry_bg": "#32302F",
        "button": "#504945", "button_active": "#665B54", "button_disabled": "#3C3836",
        "button_fg": "#EBDBB2",
        "accent": "#B8BB26", "accent_active": "#C6C94A",
        "accent_disabled": "#5F611E", "accent_fg": "#1D2021", "accent_fg_disabled": "#B5B776",
        "danger": "#FB4913", "danger_active": "#FC663F", "danger_disabled": "#7A2C17",
        "danger_fg": "#FBF1C7",
        "title": "#FBF1C7", "subtitle": "#A89984", "section": "#D5C4A1", "body": "#EBDBB2",
        "preview_text": "#BDAE93", "status": "#C9B99F",
        "field_fg": "#FBF1C7", "arrow": "#D5C4A1", "insert": "#EBDBB2",
        "recording": "#B8BB26", "paused": "#FABD2F", "ready": "#A89984",
    },
    "Solarized Dark": {
        "root": "#002B36", "panel": "#073642", "preview": "#002028",
        "entry_bg": "#01323D",
        "button": "#0F4652", "button_active": "#135260", "button_disabled": "#0B3A44",
        "button_fg": "#93A1A1",
        "accent": "#2AA198", "accent_active": "#35B1A6",
        "accent_disabled": "#1D5E59", "accent_fg": "#002B36", "accent_fg_disabled": "#7FC5BE",
        "danger": "#DC322F", "danger_active": "#E44845", "danger_disabled": "#6E2523",
        "danger_fg": "#FDF6E3",
        "title": "#FDF6E3", "subtitle": "#809AA5", "section": "#EEE8D5", "body": "#93A1A1",
        "preview_text": "#8FA6A6", "status": "#9BB0B5",
        "field_fg": "#EEE8D5", "arrow": "#93A1A1", "insert": "#93A1A1",
        "recording": "#859900", "paused": "#B58900", "ready": "#809AA5",
    },
    "Paper": {
        "root": "#E8ECF3", "panel": "#F4F7FB", "preview": "#D8DEE9",
        "entry_bg": "#FFFFFF",
        "button": "#D7DEE9", "button_active": "#C6D0DF", "button_disabled": "#E4E8EF",
        "button_fg": "#2A3342",
        "accent": "#12805C", "accent_active": "#0E6B4D",
        "accent_disabled": "#A8CFC2", "accent_fg": "#FFFFFF", "accent_fg_disabled": "#6E9C8D",
        "danger": "#C0392B", "danger_active": "#D24E41", "danger_disabled": "#B99C9B",
        "danger_fg": "#FFFFFF",
        "title": "#1B2432", "subtitle": "#5A6B82", "section": "#22304A", "body": "#2A3342",
        "preview_text": "#5A6B82", "status": "#47556E",
        "field_fg": "#1B2432", "arrow": "#5A6B82", "insert": "#1B2432",
        "recording": "#0E8A5F", "paused": "#B07908", "ready": "#5A6B82",
    },
}

_ACTIVE_THEME = "Midnight"


def theme() -> dict[str, str]:
    """The active colour scheme (module-level so the static drawing helpers
    — preview overlay, recap title, tray icon — can read it too)."""
    return THEMES.get(_ACTIVE_THEME, THEMES["Midnight"])


def set_active_theme(name: str) -> None:
    global _ACTIVE_THEME
    _ACTIVE_THEME = name if name in THEMES else "Midnight"


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


IS_WINDOWS = os.name == "nt"

# Manual-exposure slider. Windows drivers use a negative log2-seconds value;
# V4L2 (Linux) takes 100 µs units, so the Linux slider is in milliseconds
# and gets ×10 when applied to the camera.
if IS_WINDOWS:
    EXPOSURE_MIN, EXPOSURE_MAX, EXPOSURE_DEFAULT = -11, -1, -6
else:
    EXPOSURE_MIN, EXPOSURE_MAX, EXPOSURE_DEFAULT = 1, 50, 15

# Fonts for Pillow-drawn overlays / recap title. Arch paths first
# (ttf-dejavu, noto-fonts, liberation-fonts), Windows names last so the
# file stays dual-platform.
FONT_FILES_REGULAR = (
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
    "arial.ttf",
)
FONT_FILES_BOLD = (
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
)


def load_font(size: int, bold: bool = False):
    for path in (FONT_FILES_BOLD if bold else FONT_FILES_REGULAR):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def find_ffmpeg() -> str:
    """Prefer Arch's system ffmpeg; fall back to the imageio-ffmpeg wheel."""
    which = shutil.which("ffmpeg")
    if which:
        return which
    if get_ffmpeg_exe is not None:
        try:
            return get_ffmpeg_exe()
        except Exception:
            pass
    raise RuntimeError("ffmpeg was not found. Install it with: sudo pacman -S ffmpeg")


def open_with_default_app(target: Path | str) -> None:
    """Open a file or folder with the desktop default (xdg-open / startfile)."""
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
        return
    try:
        subprocess.Popen(
            ["xdg-open", str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def default_videos_folder() -> Path:
    """XDG Videos directory when it exists, else ~/Videos, ~/Pictures, ~/."""
    try:
        result = subprocess.run(
            ["xdg-user-dir", "VIDEOS"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            candidate = Path(result.stdout.strip())
            if candidate.is_dir():
                return candidate / "Timelapses"
    except Exception:
        pass
    home = Path.home()
    for base in (home / "Videos", home / "Pictures"):
        if base.is_dir():
            return base / "Timelapses"
    return home / "Timelapses"


if IS_WINDOWS:
    _LIBC = None
else:
    try:
        _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _LIBC = None


def _set_pdeathsig() -> None:
    """preexec_fn for the systemd-inhibit child: make it die together with
    the LapseCam process so the inhibitor lock can never outlive the app
    (Linux PR_SET_PDEATHSIG)."""
    if _LIBC is None:
        return
    try:
        _LIBC.prctl(1, signal.SIGTERM)   # 1 == PR_SET_PDEATHSIG
    except Exception:
        pass


def format_speed(speed: float) -> str:
    """Format a time-compression value without visual noise."""
    return f"{speed:g}×"


def capture_interval_seconds(speed: float, output_fps: int = OUTPUT_FPS) -> float:
    """How long to wait in real time before selecting the next source frame."""
    return speed / output_fps


def pretty_bytes(value: int | float) -> str:
    value = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def pretty_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def pretty_hours(minutes: float) -> str:
    minutes = max(0.0, float(minutes))
    if minutes < 60:
        return f"{int(round(minutes))} min"
    hours = int(minutes // 60)
    remainder = int(round(minutes % 60))
    if remainder:
        return f"{hours} h {remainder:02d} min"
    return f"{hours} h"


def unique_destination(folder: Path, stem: str) -> Path:
    """Return an MP4 path that cannot overwrite an existing timelapse."""
    candidate = folder / f"{stem}.mp4"
    serial = 2
    while candidate.exists() or candidate.with_name(candidate.stem + "__recording.mp4").exists():
        candidate = folder / f"{stem}_{serial}.mp4"
        serial += 1
    return candidate


def _subprocess_hide_window() -> tuple[subprocess.STARTUPINFO | None, int]:
    """Windows: run ffmpeg without flashing a console window."""
    if os.name != "nt":
        return None, 0
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo, subprocess.CREATE_NO_WINDOW


class FfmpegWriter:
    """Feeds individual, in-memory JPEGs to a live H.264 fragmented MP4 encoder."""

    def __init__(
        self,
        destination: Path,
        width: int,
        height: int,
        crf: int,
        jpeg_quality: int,
        fps: int = OUTPUT_FPS,
    ) -> None:
        self.destination = destination
        self.partial_path = destination.with_name(destination.stem + "__recording.mp4")
        self.width = width
        self.height = height
        self.fps = fps
        self.jpeg_quality = jpeg_quality
        self.frames_written = 0
        self.input_bytes = 0
        self._closed = False

        ffmpeg = find_ffmpeg()
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+empty_moov+default_base_moof+frag_every_frame",
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            str(self.partial_path),
        ]
        startupinfo, creationflags = _subprocess_hide_window()
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RuntimeError("The video encoder could not be started.") from exc

    def write(self, frame: np.ndarray) -> None:
        """Resize and submit exactly one selected webcam frame; no backlog is kept."""
        if self._closed:
            raise RuntimeError("The video encoder is already closed.")
        if self.process.poll() is not None:
            raise RuntimeError(self._encoder_error("The video encoder stopped unexpectedly."))
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            interpolation = cv2.INTER_AREA if frame.shape[1] >= self.width else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (self.width, self.height), interpolation=interpolation)
        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:
            raise RuntimeError("A webcam frame could not be compressed.")
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(encoded.tobytes())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(self._encoder_error("The video encoder stopped unexpectedly.")) from exc
        self.frames_written += 1
        self.input_bytes += int(encoded.size)

    def output_bytes(self) -> int:
        try:
            return self.partial_path.stat().st_size
        except OSError:
            return 0

    def close(self) -> Path:
        """Finish the fragmented MP4 and atomically promote it to its final name."""
        if self._closed:
            return self.destination
        self._closed = True
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            result = self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                result = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                result = self.process.wait(timeout=5)
        if result != 0:
            raise RuntimeError(self._encoder_error("The encoder could not finalize the video."))
        if self.frames_written == 0:
            raise RuntimeError("No webcam frames were captured. Check the camera and try again.")
        try:
            os.replace(self.partial_path, self.destination)
        except OSError as exc:
            raise RuntimeError("The video was encoded but could not be renamed.") from exc
        return self.destination

    def _encoder_error(self, fallback: str) -> str:
        try:
            if self.process.stderr is not None:
                details = self.process.stderr.read().decode("utf-8", errors="replace").strip()
                if details:
                    last_line = details.splitlines()[-1]
                    return f"{fallback}\n\nEncoder detail: {last_line}"
        except OSError:
            pass
        return fallback


class LapseCam(tk.Tk):
    """Small, single-purpose interface around a direct-to-video recorder."""

    def __init__(self) -> None:
        super().__init__()
        self.title("LapseCam — Webcam Timelapses")
        self.minsize(980, 560)
        self.geometry("1180x760")
        self.configure(background=theme()["root"])

        self._frame_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._camera_stop = threading.Event()
        self._camera_thread: threading.Thread | None = None
        self._latest_frame: np.ndarray | None = None
        self._latest_frame_id = 0
        self._shown_frame_id = -1
        self._camera: cv2.VideoCapture | None = None
        self._ui_events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.recording = False
        self.paused = False
        self.finalizing = False
        self.writer: FfmpegWriter | None = None
        self.started_at = 0.0
        self.next_capture_at = 0.0
        self.frame_count = 0
        self.dropped_ticks = 0
        self._speed = 10.0
        # Frame rate is fixed for the lifetime of one recording (the ffmpeg
        # input rate is chosen when the encoder starts), so snapshot it here.
        self._recording_fps = OUTPUT_FPS
        self._last_disk_check = 0.0
        self._auto_stopping = False
        self._last_output: Path | None = None
        self._closing_after_save = False

        # Preview pipeline / diagnostics.
        self._preview_frame: np.ndarray | None = None
        self._preview_failures = 0
        self._tick_count = 0
        self._preview_fps = 0
        self._preview_fps_frames = 0
        self._preview_fps_window = time.monotonic()
        self._last_tick_error: str | None = None

        # Sidebar scrolling (coalescing + tearing mitigation).
        self._wheel_accumulator = 0.0
        self._pending_scroll_pixels = 0
        self._scroll_apply_scheduled = False
        self._scrolling_until = 0.0
        self._last_mousewheel = 0.0      # X11 MouseWheel/Button-4/5 dedup

        # Camera-startup staging: "opened but still no frames" (strong hint)
        # is different from "driver still opening" (normal on slow webcams).
        self._camera_thread_started_at = 0.0
        # Manual exposure lock (re-applied after every camera (re)open).
        self._exposure_locked = False
        self._exposure_value = EXPOSURE_DEFAULT  # Windows: driver slider; Linux: ms
        self._exposure_apply_pending = False
        self._camera_open_confirmed = False
        self._camera_opened_at = 0.0
        self._shown_no_frame_hint = False
        self._shown_opening_note = False

        # Timed study sessions.
        self._session_ends_at: float | None = None
        self._session_auto_stopping = False
        self._session_completed = False
        self._recording_started_wall: datetime | None = None
        self._recording_label = "Timelapse"
        self._pending_history_entry: dict[str, Any] | None = None

        # History / recap / tray / fullscreen.
        self._building_recap = False
        self._inhibit_process: subprocess.Popen | None = None  # systemd-inhibit
        self._tray_icon: Any = None
        self._tray_hidden_hint_shown = False
        self._window_icon_photo: ImageTk.PhotoImage | None = None
        self._fs_window: tk.Toplevel | None = None
        self._fs_label: ttk.Label | None = None
        self._fs_last_motion = 0.0
        self._fs_cursor_hidden = False

        default_folder = default_videos_folder()

        self._active_theme = self._load_config().get("theme", "Midnight")
        if self._active_theme not in THEMES:
            self._active_theme = "Midnight"
        set_active_theme(self._active_theme)

        self.camera_var = tk.StringVar(value="Camera 0")
        self.speed_var = tk.StringVar(value="10")
        self.fps_var = tk.StringVar(value=str(OUTPUT_FPS))
        self.theme_var = tk.StringVar(value=self._active_theme)
        self.resolution_var = tk.StringVar(value="1280×720")
        self.quality_var = tk.StringVar(value="Balanced")
        self.folder_var = tk.StringVar(value=str(default_folder))
        self.name_var = tk.StringVar(value="Timelapse")
        self.session_var = tk.StringVar(value="Until I stop")
        self.status_var = tk.StringVar(value="Opening your webcam…")
        self.capture_info_var = tk.StringVar()
        self.estimate_var = tk.StringVar()
        self.stats_var = tk.StringVar(value="Waiting for the camera")
        self.storage_var = tk.StringVar(value="Saving continuously — no raw video or frame folders")
        self.history_stats_var = tk.StringVar(value="")

        self.history = self._load_history()

        self._configure_style()
        self._build_ui()
        self._set_window_icon()
        self._build_tray()
        self.bind_all("<MouseWheel>", self._on_sidebar_wheel)
        if not IS_WINDOWS:
            # Classic X11 wheel; some WMs/compositors still deliver only these.
            self.bind_all("<Button-4>", self._on_sidebar_wheel)
            self.bind_all("<Button-5>", self._on_sidebar_wheel)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-r>", lambda _event: self.start_recording())
        self.bind("<Escape>", lambda _event: self.stop_recording())
        self.bind("<F11>", lambda _event: self._toggle_fullscreen_preview())
        self._apply_speed(silent=True)
        self._apply_fps(silent=True)
        self._update_estimate()
        self._refresh_history_display()
        self._start_camera()
        self.after(80, self._ui_tick)

    # ---------- Interface ----------

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        # Pick a UI font family that actually exists here (Segoe UI is
        # Windows-only; Arch typically ships DejaVu / Noto / Liberation).
        families = set(tkfont.families(self))
        ui_font = next(
            (name for name in (
                "Noto Sans", "DejaVu Sans", "Liberation Sans",
                "Cantarell", "Ubuntu", "Segoe UI",
            ) if name in families),
            tkfont.nametofont("TkDefaultFont").actual("family"),
        )
        colours = theme()
        style.configure(".", background=colours["root"], foreground=colours["body"], font=(ui_font, 10))
        style.configure("Top.TFrame", background=colours["root"])
        style.configure("Panel.TFrame", background=colours["panel"])
        style.configure("Preview.TFrame", background=colours["preview"])
        style.configure("Title.TLabel", background=colours["root"], foreground=colours["title"], font=(ui_font, 21, "bold"))
        style.configure("Subtitle.TLabel", background=colours["root"], foreground=colours["subtitle"], font=(ui_font, 10))
        style.configure("Section.TLabel", background=colours["panel"], foreground=colours["section"], font=(ui_font, 11, "bold"))
        style.configure("Small.TLabel", background=colours["panel"], foreground=colours["subtitle"], font=(ui_font, 9))
        style.configure("Body.TLabel", background=colours["panel"], foreground=colours["body"], font=(ui_font, 10))
        style.configure("Preview.TLabel", background=colours["preview"], foreground=colours["preview_text"], font=(ui_font, 11))
        style.configure("Status.TLabel", background=colours["root"], foreground=colours["status"], font=(ui_font, 10))
        style.configure("Accent.TButton", background=colours["accent"], foreground=colours["accent_fg"], borderwidth=0, font=(ui_font, 11, "bold"), padding=(14, 10))
        style.map("Accent.TButton", background=[("active", colours["accent_active"]), ("disabled", colours["accent_disabled"])], foreground=[("disabled", colours["accent_fg_disabled"])])
        style.configure("Danger.TButton", background=colours["danger"], foreground=colours["danger_fg"], borderwidth=0, font=(ui_font, 10, "bold"), padding=(12, 8))
        style.map("Danger.TButton", background=[("active", colours["danger_active"]), ("disabled", colours["danger_disabled"])])
        style.configure("Quiet.TButton", background=colours["button"], foreground=colours["button_fg"], borderwidth=0, padding=(9, 6))
        style.map("Quiet.TButton", background=[("active", colours["button_active"]), ("disabled", colours["button_disabled"])])
        style.configure("TCombobox", fieldbackground=colours["entry_bg"], background=colours["button"], foreground=colours["field_fg"], arrowcolor=colours["arrow"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", colours["entry_bg"])], foreground=[("readonly", colours["field_fg"])])
        style.configure("TEntry", fieldbackground=colours["entry_bg"], foreground=colours["field_fg"], insertcolor=colours["insert"], padding=6)
        # Scrollbar styling goes at the END: it needs the Style object to exist,
        # and must come after theme_use("clam") since switching themes resets
        # the style database.
        style.configure("Sidebar.Vertical.TScrollbar", background=colours["button"], troughcolor=colours["panel"], arrowcolor=colours["arrow"])
        style.map("Sidebar.Vertical.TScrollbar", background=[("active", colours["button_active"])])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="Top.TFrame", padding=(26, 18, 26, 18))
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=3, minsize=560)
        root.columnconfigure(1, weight=2, minsize=340)
        root.rowconfigure(1, weight=1)

        heading = ttk.Frame(root, style="Top.TFrame")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="LapseCam", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text="WEBCAM TIMELAPSE RECORDER", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(1, 0))
        self.recording_badge = ttk.Label(heading, text="● READY", style="Subtitle.TLabel")
        self.recording_badge.grid(row=0, column=1, rowspan=2, sticky="e")

        preview_panel = ttk.Frame(root, style="Preview.TFrame", padding=12)
        preview_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 16))
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            preview_panel, text="Looking for a webcam…",
            style="Preview.TLabel", anchor="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_label.bind("<Double-Button-1>", lambda _event: self._toggle_fullscreen_preview())
        preview_bottom = ttk.Frame(preview_panel, style="Preview.TFrame")
        preview_bottom.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        preview_bottom.columnconfigure(0, weight=1)
        self.preview_hint = ttk.Label(
            preview_bottom, text="No audio • Selected frames are compressed straight into one MP4",
            style="Preview.TLabel", anchor="w",
        )
        self.preview_hint.grid(row=0, column=0, sticky="w")
        self.fullscreen_button = ttk.Button(
            preview_bottom, text="Fullscreen  (F11)", style="Quiet.TButton",
            command=self._toggle_fullscreen_preview,
        )
        self.fullscreen_button.grid(row=0, column=1, sticky="e")

        # Sidebar = scrollable settings canvas + pinned action bar, so
        # Start/Pause/Stop can never be clipped off short screens.
        control_shell = ttk.Frame(root, style="Panel.TFrame")
        control_shell.grid(row=1, column=1, sticky="nsew")
        control_shell.columnconfigure(0, weight=1)
        control_shell.rowconfigure(0, weight=1)

        self.control_canvas = tk.Canvas(
            control_shell, background=theme()["panel"], highlightthickness=0, bd=0, yscrollincrement=20
        )
        self.control_scrollbar = ttk.Scrollbar(
            control_shell, orient="vertical", command=self._clamped_yview,
            style="Sidebar.Vertical.TScrollbar",
        )
        self.control_canvas.configure(yscrollcommand=self.control_scrollbar.set)
        self.control_canvas.grid(row=0, column=0, sticky="nsew")
        self.control_scrollbar.grid(row=0, column=1, sticky="ns")
        # Wheel events landing directly on the canvas are handled here and
        # "break"ed, so Tk's built-in canvas wheel binding doesn't ALSO
        # scroll (that would double the speed over bare canvas areas).
        self.control_canvas.bind("<MouseWheel>", self._on_canvas_wheel)
        if not IS_WINDOWS:
            self.control_canvas.bind("<Button-4>", self._on_canvas_wheel)
            self.control_canvas.bind("<Button-5>", self._on_canvas_wheel)

        control_panel = ttk.Frame(self.control_canvas, style="Panel.TFrame", padding=(18, 18, 14, 8))
        self._control_window = self.control_canvas.create_window((0, 0), window=control_panel, anchor="nw")
        control_panel.bind("<Configure>", self._on_sidebar_content_resized)
        self.control_canvas.bind("<Configure>", self._on_sidebar_viewport_resized)
        control_panel.columnconfigure(0, weight=1)

        # ---- Settings (scrollable) ----
        ttk.Label(control_panel, text="SET UP A TIMELAPSE", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(control_panel, text="Camera", style="Small.TLabel").grid(row=1, column=0, sticky="w", pady=(16, 3))
        camera_line = ttk.Frame(control_panel, style="Panel.TFrame")
        camera_line.grid(row=2, column=0, sticky="ew")
        camera_line.columnconfigure(0, weight=1)
        self.camera_combo = ttk.Combobox(camera_line, textvariable=self.camera_var, values=[f"Camera {i}" for i in range(6)], state="readonly")
        self.camera_combo.grid(row=0, column=0, sticky="ew")
        self.camera_combo.bind("<<ComboboxSelected>>", lambda _event: self._change_camera())
        self.refresh_button = ttk.Button(camera_line, text="Refresh", style="Quiet.TButton", command=self._refresh_cameras)
        self.refresh_button.grid(row=0, column=1, padx=(8, 0))

        # Exposure lock: dim-room auto-exposure is what holds webcams at
        # ~10 fps; locking it shortens each frame time and restores ~30.
        # These live in camera_line's OWN grid (internal rows 1-2, below
        # the combo), so the outer settings grid never needs renumbering.
        self.exposure_status_var = tk.StringVar(value="")
        self.exposure_lock_button = ttk.Button(
            camera_line, text="Lock exposure", style="Quiet.TButton",
            command=self._toggle_exposure_lock,
        )
        self.exposure_lock_button.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.exposure_status = ttk.Label(
            camera_line, textvariable=self.exposure_status_var,
            style="Small.TLabel", anchor="e",
        )
        self.exposure_status.grid(row=1, column=1, sticky="e", pady=(8, 0))
        self.exposure_row = ttk.Frame(camera_line, style="Panel.TFrame")
        self.exposure_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.exposure_row.columnconfigure(1, weight=1)
        self.exposure_scale_var = tk.StringVar(value=str(EXPOSURE_DEFAULT))
        ttk.Label(
            self.exposure_row,
            text="Exposure" + ("" if IS_WINDOWS else " (ms)"),
            style="Small.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.exposure_scale = ttk.Scale(
            self.exposure_row, from_=EXPOSURE_MIN, to_=EXPOSURE_MAX,
            value=EXPOSURE_DEFAULT,
            command=lambda value: self._on_exposure_slider(float(value)),
        )
        self.exposure_scale.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.exposure_scale_value = ttk.Label(
            self.exposure_row, textvariable=self.exposure_scale_var,
            style="Small.TLabel", width=4, anchor="e",
        )
        self.exposure_scale_value.grid(row=0, column=2)
        # Slider row stays hidden until the lock is switched on.
        self.exposure_row.grid_remove()

        ttk.Label(control_panel, text="Time compression", style="Small.TLabel").grid(row=3, column=0, sticky="w", pady=(16, 3))
        speed_line = ttk.Frame(control_panel, style="Panel.TFrame")
        speed_line.grid(row=4, column=0, sticky="ew")
        speed_line.columnconfigure(0, weight=1)
        self.speed_combo = ttk.Combobox(speed_line, textvariable=self.speed_var, values=["2", "5", "10", "30", "60", "100"], width=9)
        self.speed_combo.grid(row=0, column=0, sticky="w")
        self.speed_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_speed())
        self.speed_combo.bind("<Return>", lambda _event: self._apply_speed())
        self.speed_combo.bind("<FocusOut>", lambda _event: self._apply_speed())
        ttk.Label(speed_line, text="×  (type any value from 2 to 10,000)", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))

        # Output frame rate (row 5 was free in the original layout).
        fps_line = ttk.Frame(control_panel, style="Panel.TFrame")
        fps_line.grid(row=5, column=0, sticky="ew", pady=(8, 3))
        ttk.Label(fps_line, text="Output frame rate", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.fps_combo = ttk.Combobox(
            fps_line, textvariable=self.fps_var,
            values=[str(f) for f in OUTPUT_FPS_CHOICES],
            width=5, state="readonly",
        )
        self.fps_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.fps_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_fps())
        ttk.Label(fps_line, text="fps  •  higher = smoother motion, larger files", style="Small.TLabel").grid(row=0, column=2, sticky="w", padx=(7, 0))

        ttk.Label(control_panel, textvariable=self.capture_info_var, style="Small.TLabel", wraplength=300).grid(row=6, column=0, sticky="w", pady=(7, 0))

        ttk.Separator(control_panel).grid(row=7, column=0, sticky="ew", pady=14)
        output_line = ttk.Frame(control_panel, style="Panel.TFrame")
        output_line.grid(row=8, column=0, sticky="ew")
        output_line.columnconfigure(0, weight=1)
        output_line.columnconfigure(1, weight=1)
        ttk.Label(output_line, text="Output size", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(output_line, text="Quality", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.resolution_combo = ttk.Combobox(output_line, textvariable=self.resolution_var, values=["1280×720", "1920×1080"], state="readonly")
        self.resolution_combo.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.resolution_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_estimate())
        self.quality_combo = ttk.Combobox(output_line, textvariable=self.quality_var, values=list(QUALITY), state="readonly")
        self.quality_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(3, 0))
        self.quality_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_estimate())
        # Keep the wheel from silently changing readonly combos while scrolling.
        for combo in (self.camera_combo, self.resolution_combo, self.quality_combo, self.fps_combo):
            combo.bind("<MouseWheel>", lambda _event: "break")
            if not IS_WINDOWS:
                combo.bind("<Button-4>", lambda _event: "break")
                combo.bind("<Button-5>", lambda _event: "break")
        ttk.Label(control_panel, textvariable=self.estimate_var, style="Small.TLabel", wraplength=300).grid(row=9, column=0, sticky="w", pady=(8, 0))

        ttk.Label(control_panel, text="Save to", style="Small.TLabel").grid(row=10, column=0, sticky="w", pady=(14, 3))
        folder_line = ttk.Frame(control_panel, style="Panel.TFrame")
        folder_line.grid(row=11, column=0, sticky="ew")
        folder_line.columnconfigure(0, weight=1)
        self.folder_entry = ttk.Entry(folder_line, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(folder_line, text="Browse", style="Quiet.TButton", command=self._pick_folder)
        self.browse_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(control_panel, text="File label", style="Small.TLabel").grid(row=12, column=0, sticky="w", pady=(10, 3))
        self.name_entry = ttk.Entry(control_panel, textvariable=self.name_var)
        self.name_entry.grid(row=13, column=0, sticky="ew")

        ttk.Label(control_panel, text="Session length", style="Small.TLabel").grid(row=14, column=0, sticky="w", pady=(14, 3))
        session_line = ttk.Frame(control_panel, style="Panel.TFrame")
        session_line.grid(row=15, column=0, sticky="ew")
        self.session_combo = ttk.Combobox(
            session_line, textvariable=self.session_var,
            values=["Until I stop", "15 min", "25 min", "50 min", "90 min", "2 h", "3 h"],
            state="readonly",
        )
        self.session_combo.grid(row=0, column=0, sticky="ew")
        ttk.Label(session_line, text="auto-stops and saves when time is up", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))

        # ---- History / streak ----
        ttk.Separator(control_panel).grid(row=16, column=0, sticky="ew", pady=14)
        ttk.Label(control_panel, text="YOUR HISTORY", style="Section.TLabel").grid(row=17, column=0, sticky="w")
        ttk.Label(control_panel, textvariable=self.history_stats_var, style="Small.TLabel", wraplength=300).grid(row=18, column=0, sticky="w", pady=(7, 0))
        recap_line = ttk.Frame(control_panel, style="Panel.TFrame")
        recap_line.grid(row=19, column=0, sticky="ew", pady=(10, 0))
        recap_line.columnconfigure(0, weight=1)
        self.recap_button = ttk.Button(recap_line, text="Make today's recap", style="Quiet.TButton", command=self._build_recap)
        self.recap_button.grid(row=0, column=0, sticky="ew")
        ttk.Label(recap_line, text="stitches today's sessions into one video", style="Small.TLabel").grid(row=0, column=1, sticky="w", padx=(7, 0))
        self.history_list_frame = ttk.Frame(control_panel, style="Panel.TFrame")
        self.history_list_frame.grid(row=20, column=0, sticky="ew", pady=(10, 0))
        self.history_list_frame.columnconfigure(0, weight=1)

        # ---- Appearance ----
        ttk.Separator(control_panel).grid(row=21, column=0, sticky="ew", pady=14)
        ttk.Label(control_panel, text="APPEARANCE", style="Section.TLabel").grid(row=22, column=0, sticky="w")
        theme_line = ttk.Frame(control_panel, style="Panel.TFrame")
        theme_line.grid(row=23, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(theme_line, text="Colour scheme", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.theme_combo = ttk.Combobox(
            theme_line, textvariable=self.theme_var,
            values=list(THEMES), state="readonly", width=16,
        )
        self.theme_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _event: self._apply_theme())
        # Own wheel-break bindings: this combobox is created further down
        # the sidebar than the shared combo loop above, so it can't be in it.
        self.theme_combo.bind("<MouseWheel>", lambda _event: "break")
        if not IS_WINDOWS:
            self.theme_combo.bind("<Button-4>", lambda _event: "break")
            self.theme_combo.bind("<Button-5>", lambda _event: "break")
        ttk.Label(theme_line, text="applies live • saved for next launch", style="Small.TLabel").grid(row=0, column=2, sticky="w", padx=(7, 0))

        # ---- Action bar: pinned below the scroll area, never clipped ----
        action_bar = ttk.Frame(control_shell, style="Panel.TFrame", padding=(18, 6, 18, 18))
        action_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.start_button = ttk.Button(action_bar, text="Start timelapse", style="Accent.TButton", command=self.start_recording)
        self.start_button.grid(row=0, column=0, sticky="ew")
        action_line = ttk.Frame(action_bar, style="Panel.TFrame")
        action_line.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        action_line.columnconfigure(0, weight=1)
        action_line.columnconfigure(1, weight=1)
        self.pause_button = ttk.Button(action_line, text="Pause capture", style="Quiet.TButton", command=self.toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(action_line, text="Stop & save", style="Danger.TButton", command=self.stop_recording, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # ---- Status panel ----
        status_panel = ttk.Frame(root, style="Panel.TFrame", padding=(16, 12))
        status_panel.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        status_panel.columnconfigure(0, weight=1)
        status_panel.columnconfigure(1, weight=0)
        ttk.Label(status_panel, textvariable=self.status_var, style="Body.TLabel").grid(row=0, column=0, sticky="w")
        self.open_button = ttk.Button(status_panel, text="Open video", style="Quiet.TButton", command=self._open_video, state="disabled")
        self.open_button.grid(row=0, column=1, sticky="e")
        self.show_button = ttk.Button(status_panel, text="Show folder", style="Quiet.TButton", command=self._show_folder, state="disabled")
        self.show_button.grid(row=0, column=2, sticky="e", padx=(7, 0))
        ttk.Label(status_panel, textvariable=self.stats_var, style="Small.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Label(status_panel, textvariable=self.storage_var, style="Small.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(3, 0))

    # ---------- Settings and actions ----------

    def _parse_speed(self) -> float | None:
        raw = self.speed_var.get().strip().lower().replace("×", "").replace("x", "")
        try:
            speed = float(raw)
        except ValueError:
            self.status_var.set("Enter a time compression such as 2, 10, or 100.")
            return None
        if not MIN_SPEED <= speed <= MAX_SPEED:
            self.status_var.set("Time compression must be between 2× and 10,000×.")
            return None
        return speed

    def _parse_fps(self) -> int:
        """The combobox is readonly, so this is mostly a safety net."""
        try:
            fps = int(float(self.fps_var.get().strip()))
        except ValueError:
            fps = OUTPUT_FPS
        if fps not in OUTPUT_FPS_CHOICES:
            self.status_var.set(f"Frame rate must be one of {', '.join(str(f) for f in OUTPUT_FPS_CHOICES)}.")
            return OUTPUT_FPS
        return fps

    def _apply_speed(self, silent: bool = False) -> None:
        speed = self._parse_speed()
        if speed is None:
            return
        self.speed_var.set(f"{speed:g}")
        with self._state_lock:
            changed = speed != self._speed
            self._speed = speed
            if self.recording:
                # Make a live speed change take effect on the next selected frame.
                self.next_capture_at = time.monotonic()
        self._refresh_capture_info()
        self._update_estimate()
        if changed and self.recording and not silent:
            self.status_var.set(f"Speed changed to {format_speed(speed)}. Future frames use the new rate.")

    def _apply_fps(self, silent: bool = False) -> None:
        """Change the output frame rate. Locked during a recording because
        the running encoder was opened with a fixed input framerate."""
        if self.recording or self.finalizing:
            self.fps_var.set(str(self._recording_fps))
            self.status_var.set("Frame rate is fixed while recording — it applies to the next timelapse.")
            return
        fps = self._parse_fps()
        self.fps_var.set(str(fps))
        self._refresh_capture_info()
        self._update_estimate()
        if not silent:
            self.status_var.set(f"Output frame rate set to {fps} fps.")

    def _refresh_capture_info(self) -> None:
        speed = self._parse_speed()
        if speed is None:
            return
        fps = self._parse_fps()
        interval = capture_interval_seconds(speed, fps)
        self.capture_info_var.set(
            f"{format_speed(speed)} at {fps} fps — captures 1 frame every {interval:.2f} sec; "
            f"1 real minute becomes {60 / speed:.1f} sec of video."
        )

    def _output_size(self) -> tuple[int, int]:
        value = self.resolution_var.get().replace("×", "x")
        width, height = value.split("x", 1)
        return int(width), int(height)

    def _session_minutes(self) -> float | None:
        """Session length in minutes, or None for "Until I stop"."""
        raw = self.session_var.get().strip().lower()
        if raw in ("until i stop", ""):
            return None
        match = re.match(r"([\d.]+)\s*(h|hour|hr|m|min)?", raw)
        if match is None:
            return None
        value = float(match.group(1))
        return value * 60.0 if (match.group(2) or "min").startswith("h") else value

    def _update_estimate(self) -> None:
        speed = self._parse_speed()
        if speed is None:
            return
        fps = self._parse_fps()
        width, height = self._output_size()
        preset = QUALITY[self.quality_var.get()]
        pixels_relative_to_720p = (width * height) / (1280 * 720)
        # Bitrate at a fixed CRF grows roughly with frames per video second,
        # so scale the 30 fps calibration by fps/30. Video seconds per real
        # hour = 3600/speed regardless of fps (compression is `speed` alone).
        estimated_mbps = preset["estimate_mbps"] * pixels_relative_to_720p * (fps / 30.0)
        per_hour_mb = estimated_mbps * 450 / speed
        self.estimate_var.set(
            f"Estimated ~{per_hour_mb:.0f} MB per real hour at {format_speed(speed)}, {fps} fps "
            f"(H.264 playback)."
        )

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)
            self._update_estimate()

    def _refresh_cameras(self) -> None:
        if self.recording or self.finalizing:
            return
        self.status_var.set("Refreshing the webcam…")
        self._change_camera()

    def _change_camera(self) -> None:
        if self.recording or self.finalizing:
            return
        self._stop_camera()
        self._start_camera()

    def start_recording(self) -> None:
        if self.recording or self.finalizing:
            return
        speed = self._parse_speed()
        if speed is None:
            return
        fps = self._parse_fps()
        with self._frame_lock:
            camera_ready = self._latest_frame is not None
        if not camera_ready:
            messagebox.showwarning(APP_NAME, "A webcam frame is not available yet. Check the camera, then try again.")
            return
        try:
            folder = Path(self.folder_var.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"That save folder cannot be used.\n\n{exc}")
            return
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            free = MIN_FREE_BYTES + 1
        if free < MIN_FREE_BYTES:
            messagebox.showwarning(APP_NAME, "Less than 1 GB is free in that folder. Choose a drive with more space.")
            return
        label = re.sub(r"[<>:\\/*?\"|]+", "-", self.name_var.get()).strip(" .-") or "Timelapse"
        stamped_name = f"{label}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
        destination = unique_destination(folder, stamped_name)
        width, height = self._output_size()
        preset = QUALITY[self.quality_var.get()]
        try:
            writer = FfmpegWriter(destination, width, height, preset["crf"], preset["jpeg"], fps)
        except Exception as exc:  # User needs a readable message, not a traceback.
            messagebox.showerror(APP_NAME, f"Could not prepare the video encoder.\n\n{exc}")
            return
        with self._state_lock:
            self.writer = writer
            self.recording = True
            self.paused = False
            self.started_at = time.monotonic()
            self.next_capture_at = self.started_at
            self.frame_count = 0
            self.dropped_ticks = 0
            self._speed = speed
            self._recording_fps = fps
            self._auto_stopping = False
            minutes = self._session_minutes()
            self._session_ends_at = (time.monotonic() + minutes * 60.0) if minutes is not None else None
            self._session_auto_stopping = False
            self._session_completed = False
            self._recording_started_wall = datetime.now()
            self._recording_label = label
            self._pending_history_entry = None
        self._prevent_sleep(True)
        self._last_output = None
        self.open_button.configure(state="disabled")
        self.show_button.configure(state="disabled")
        self._set_recording_controls(True)
        self._tray_update_state(True)
        self._refresh_history_display()
        self.recording_badge.configure(text="● RECORDING", foreground=theme()["recording"])
        self.status_var.set(f"Recording {format_speed(speed)} timelapse at {fps} fps to {destination.name}")
        self.storage_var.set("Saving continuously as compressed H.264 — no raw footage is retained.")

    def toggle_pause(self) -> None:
        if not self.recording or self.finalizing:
            return
        with self._state_lock:
            self.paused = not self.paused
            if not self.paused:
                self.next_capture_at = time.monotonic()
            paused = self.paused
        if paused:
            self.pause_button.configure(text="Resume capture")
            self.recording_badge.configure(text="● PAUSED", foreground=theme()["paused"])
            self.status_var.set("Capture paused. The current video is kept safe on disk.")
        else:
            self.pause_button.configure(text="Pause capture")
            self.recording_badge.configure(text="● RECORDING", foreground=theme()["recording"])
            self.status_var.set("Capture resumed.")

    def stop_recording(self) -> None:
        with self._state_lock:
            if not self.recording or self.finalizing:
                return
            elapsed_minutes = max(0.0, (time.monotonic() - self.started_at) / 60.0)
            # Snapshot what the history entry needs now; the path is filled in
            # once the file is finalized successfully.
            self._pending_history_entry = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "started": (self._recording_started_wall or datetime.now()).strftime("%Y-%m-%d %H:%M"),
                "minutes": round(elapsed_minutes, 1),
                "label": self._recording_label,
                "speed": self._speed,
                "fps": self._recording_fps,
                "frames": self.frame_count,
                "path": "",
            }
            self.recording = False
            self.paused = False
            self.finalizing = True
            self._session_ends_at = None
            writer = self.writer
            self.writer = None
        self._prevent_sleep(False)
        self._tray_update_state(False)
        self.status_var.set("Finalizing the compressed MP4…")
        self.recording_badge.configure(text="● SAVING", foreground=theme()["paused"])
        self._set_recording_controls(False, finalizing=True)
        if writer is None:
            self._finish_finalization(None, "No video encoder was available.")
            return
        threading.Thread(target=self._finalize_writer, args=(writer,), daemon=True, name="finish-video").start()

    def _finalize_writer(self, writer: FfmpegWriter) -> None:
        try:
            destination = writer.close()
            self._ui_events.put(("finalized", (destination, None)))
        except Exception as exc:
            self._ui_events.put(("finalized", (None, str(exc))))

    def _finish_finalization(self, destination: Path | None, error: str | None) -> None:
        self.finalizing = False
        self._set_recording_controls(False)
        self.pause_button.configure(text="Pause capture")
        self.recording_badge.configure(text="● READY", foreground=theme()["ready"])
        if error:
            self.status_var.set("The timelapse could not be finalized.")
            self.storage_var.set(error)
            messagebox.showerror(APP_NAME, error)
        elif destination is not None:
            self._last_output = destination
            if self._session_completed:
                self.status_var.set(f"Session complete — saved {destination.name}")
            else:
                self.status_var.set(f"Saved {destination.name}")
            try:
                size = pretty_bytes(destination.stat().st_size)
            except OSError:
                size = "an unknown size"
            self.storage_var.set(f"{size} • H.264 MP4 • saved without a normal-speed source file")
            self.open_button.configure(state="normal")
            self.show_button.configure(state="normal")
            entry = self._pending_history_entry
            if entry is not None:
                entry["path"] = str(destination)
                self.history.append(entry)
                self._pending_history_entry = None
                self._save_history()
                self._refresh_history_display()
        if self._closing_after_save:
            self._shutdown()

    def _set_recording_controls(self, active: bool, finalizing: bool = False) -> None:
        self.exposure_lock_button.configure(state="disabled" if active or finalizing else "normal")
        self.exposure_scale.configure(state="disabled" if active or finalizing else "normal")
        locked = "disabled" if active or finalizing else "readonly"
        self.camera_combo.configure(state=locked)
        self.refresh_button.configure(state="disabled" if active or finalizing else "normal")
        self.resolution_combo.configure(state=locked)
        self.quality_combo.configure(state=locked)
        self.fps_combo.configure(state=locked)  # fixed per recording (encoder input rate)
        self.session_combo.configure(state=locked)
        self.folder_entry.configure(state="disabled" if active or finalizing else "normal")
        self.browse_button.configure(state="disabled" if active or finalizing else "normal")
        self.name_entry.configure(state="disabled" if active or finalizing else "normal")
        self.start_button.configure(state="disabled" if active or finalizing else "normal")
        self.pause_button.configure(state="normal" if active else "disabled")
        self.stop_button.configure(state="normal" if active else "disabled")
        # Speed intentionally stays editable during a recording.
        self.speed_combo.configure(state="normal")

    # ---------- Colour schemes ----------

    def _load_config(self) -> dict[str, Any]:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = CONFIG_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps({"theme": self._active_theme}, indent=1), encoding="utf-8")
            os.replace(temporary, CONFIG_PATH)
        except Exception:
            pass

    def _apply_theme(self, name: str | None = None) -> None:
        """Switch the colour scheme live and remember it for next launch."""
        if name is None:
            name = self.theme_var.get()
        if name not in THEMES:
            name = "Midnight"
        set_active_theme(name)
        self._active_theme = name
        self.theme_var.set(name)
        self.configure(background=theme()["root"])
        self._configure_style()
        self.control_canvas.configure(background=theme()["panel"])
        self._set_window_icon()
        # Re-assert the status badge colour for the current state.
        if self.finalizing:
            badge_colour = theme()["paused"]
        elif self.recording and self.paused:
            badge_colour = theme()["paused"]
        elif self.recording:
            badge_colour = theme()["recording"]
        else:
            badge_colour = theme()["ready"]
        self.recording_badge.configure(foreground=badge_colour)
        self._tray_update_state(self.recording)
        self._save_config()
        self.status_var.set(f"Colour scheme: {name}.")

    # ---------- History, streak and recap ----------

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            with open(HISTORY_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [entry for entry in data if isinstance(entry, dict)]
        except Exception:
            pass
        return []

    def _save_history(self) -> None:
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = HISTORY_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.history, indent=1), encoding="utf-8")
            os.replace(temporary, HISTORY_PATH)
        except Exception:
            self.status_var.set("Note: the session history could not be saved to disk.")

    def _history_summary(self) -> str:
        per_day: dict[str, float] = {}
        counted_days: set[str] = set()
        for entry in self.history:
            day = str(entry.get("date", ""))
            minutes = float(entry.get("minutes", 0) or 0)
            if not day:
                continue
            per_day[day] = per_day.get(day, 0.0) + minutes
            if minutes >= MIN_STREAK_MINUTES:
                counted_days.add(day)
        today = date.today()
        today_key = today.strftime("%Y-%m-%d")
        today_minutes = per_day.get(today_key, 0.0)
        week_minutes = 0.0
        for offset in range(7):
            key = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            week_minutes += per_day.get(key, 0.0)
        streak = 0
        day = today
        if today_key not in counted_days:
            # The streak is still alive if yesterday counted — today keeps it going.
            day = today - timedelta(days=1)
        while day.strftime("%Y-%m-%d") in counted_days:
            streak += 1
            day -= timedelta(days=1)
        summary = (
            f"Today {pretty_hours(today_minutes)}  •  {pretty_hours(week_minutes)} this week  •  "
            f"{streak}-day streak"
        )
        if streak > 0 and today_key not in counted_days:
            summary += " — record today to keep it!"
        return summary

    def _today_video_paths(self) -> list[Path]:
        today_key = date.today().strftime("%Y-%m-%d")
        paths: list[Path] = []
        for entry in self.history:
            if entry.get("date") == today_key and entry.get("path"):
                path = Path(str(entry["path"]))
                if path.exists():
                    paths.append(path)
        return paths

    def _minutes_on(self, day_key: str) -> float:
        return sum(float(e.get("minutes", 0) or 0) for e in self.history if e.get("date") == day_key)

    def _refresh_history_display(self) -> None:
        self.history_stats_var.set(self._history_summary())
        for child in self.history_list_frame.winfo_children():
            child.destroy()
        entries = list(reversed(self.history[-6:]))
        if not entries:
            ttk.Label(
                self.history_list_frame,
                text="No sessions yet — your first timelapse will appear here.",
                style="Small.TLabel", wraplength=300,
            ).grid(row=0, column=0, sticky="w")
        for row, entry in enumerate(entries):
            line = ttk.Frame(self.history_list_frame, style="Panel.TFrame")
            line.grid(row=row, column=0, sticky="ew", pady=(0, 4))
            line.columnconfigure(0, weight=1)
            started = str(entry.get("started", ""))[:16]
            minutes = pretty_hours(float(entry.get("minutes", 0) or 0))
            speed = format_speed(float(entry.get("speed", 0) or 0))
            fps_value = entry.get("fps")  # absent in pre-fps history entries
            rate = f"{speed} @ {fps_value} fps" if fps_value else speed
            ttk.Label(
                line, text=f"{started}  •  {minutes}  •  {rate}",
                style="Small.TLabel", anchor="w",
            ).grid(row=0, column=0, sticky="w")
            path = Path(str(entry.get("path", "")))
            exists = path.exists()
            button = ttk.Button(
                line, text="Open" if exists else "—", style="Quiet.TButton",
                command=lambda p=path: self._open_history_video(p),
                state="normal" if exists else "disabled",
            )
            button.grid(row=0, column=1, padx=(7, 0))
        today_count = len(self._today_video_paths())
        self.recap_button.configure(
            state="normal"
            if today_count and not self.recording and not self.finalizing and not self._building_recap
            else "disabled"
        )

    def _open_history_video(self, path: Path) -> None:
        if path and path.exists():
            open_with_default_app(path)
        else:
            self.status_var.set("That video has been moved or deleted.")

    def _build_recap(self) -> None:
        if self.recording or self.finalizing or self._building_recap:
            return
        videos = self._today_video_paths()
        if not videos:
            self.status_var.set("No saved sessions from today to recap yet.")
            return
        try:
            folder = Path(self.folder_var.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"That save folder cannot be used.\n\n{exc}")
            return
        today_key = datetime.now().strftime("%Y-%m-%d")
        destination = unique_destination(folder, f"Recap_{today_key}")
        day_number = len({str(e.get("date")) for e in self.history if e.get("date")})
        total_text = pretty_hours(self._minutes_on(today_key))
        self._building_recap = True
        self.recap_button.configure(state="disabled")
        self.status_var.set(f"Building today's recap from {len(videos)} video(s)…")
        threading.Thread(
            target=self._recap_worker, args=(videos, day_number, total_text, destination),
            daemon=True, name="recap",
        ).start()

    def _recap_worker(
        self, videos: list[Path], day_number: int, total_text: str, destination: Path
    ) -> None:
        title_png: Path | None = None
        try:
            title_png = self._recap_title_png(day_number, total_text)
            ffmpeg = find_ffmpeg()
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1", "-t", "3", "-i", str(title_png),
            ]
            for video in videos:
                command += ["-i", str(video)]
            count = len(videos) + 1
            # Sessions may have been recorded at 24/30/60 fps; resampling
            # every input to 30 fps preserves each clip's DURATION exactly
            # (frames are dropped/duplicated, timing is not stretched).
            chains = [f"[{i}:v]scale=1280:720,setsar=1,fps=30[v{i}]" for i in range(count)]
            concat = "".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[out]"
            command += [
                "-filter_complex", ";".join(chains) + ";" + concat,
                "-map", "[out]",
                "-c:v", "libx264", "-preset", "faster", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(destination),
            ]
            startupinfo, creationflags = _subprocess_hide_window()
            result = subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                startupinfo=startupinfo, creationflags=creationflags,
            )
            if result.returncode != 0:
                lines = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
                self._ui_events.put(("recap_done", (None, lines[-1] if lines else "ffmpeg failed")))
            else:
                self._ui_events.put(("recap_done", (destination, None)))
        except Exception as exc:
            self._ui_events.put(("recap_done", (None, str(exc))))
        finally:
            if title_png is not None:
                try:
                    title_png.unlink()
                except OSError:
                    pass

    @staticmethod
    def _recap_title_png(day_number: int, total_text: str) -> Path:
        colours = theme()
        image = Image.new("RGB", (1280, 720), _hex_to_rgb(colours["root"]))
        draw = ImageDraw.Draw(image)

        title_font = load_font(110, bold=True)
        sub_font = load_font(44)
        date_font = load_font(30)

        draw.text((640, 280), f"DAY {day_number}", font=title_font, fill=_hex_to_rgb(colours["title"]), anchor="mm")
        draw.text((640, 420), f"{total_text} recorded today", font=sub_font, fill=_hex_to_rgb(colours["subtitle"]), anchor="mm")
        draw.text((640, 490), datetime.now().strftime("%A, %d %B %Y"), font=date_font, fill=_hex_to_rgb(colours["subtitle"]), anchor="mm")
        handle = tempfile.NamedTemporaryFile(prefix="lapsecam_title_", suffix=".png", delete=False)
        image.save(handle, format="PNG")
        handle.close()
        return Path(handle.name)

    # ---------- Camera and encoder work ----------

    def _selected_camera_index(self) -> int:
        found = re.search(r"(\d+)", self.camera_var.get())
        return int(found.group(1)) if found else 0

    def _start_camera(self) -> None:
        self._camera_stop.clear()
        self._camera_thread_started_at = time.monotonic()
        self._shown_no_frame_hint = False
        self._shown_opening_note = False
        self._camera_open_confirmed = False
        self._camera_opened_at = 0.0
        index = self._selected_camera_index()
        self._camera_thread = threading.Thread(target=self._camera_loop, args=(index,), daemon=True, name="webcam")
        self._camera_thread.start()

    def _stop_camera(self) -> None:
        self._camera_stop.set()
        camera = self._camera
        if camera is not None:
            try:
                camera.release()
            except Exception:
                pass
        if self._camera_thread is not None and self._camera_thread.is_alive():
            self._camera_thread.join(timeout=1.5)
        self._camera_thread = None
        self._camera = None
        with self._frame_lock:
            self._latest_frame = None
            self._preview_frame = None
        self._shown_frame_id = -1

    def _toggle_exposure_lock(self) -> None:
        if self._camera is None:
            self.status_var.set("No camera is open yet — lock will apply when one is.")
            return
        self._exposure_locked = not self._exposure_locked
        if self._exposure_locked:
            self.exposure_row.grid()
            self._apply_exposure_lock()
            self.exposure_lock_button.configure(text="Unlock exposure")
        else:
            self.exposure_row.grid_remove()
            self._release_exposure_lock()
            self.exposure_lock_button.configure(text="Lock exposure")
            self.status_var.set("Exposure back to automatic — point a lamp at yourself for best fps.")

    def _apply_exposure_lock(self) -> None:
        """Push manual-exposure mode + the slider value onto the live camera."""
        camera = self._camera
        if camera is None or not self._exposure_locked:
            return
        try:
            if IS_WINDOWS:
                # DirectShow convention: 1 = manual, 0/4 = automatic (varies
                # by driver, so try the common values in order).
                for aec_value in (1, 0):
                    camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, aec_value)
                    if camera.set(cv2.CAP_PROP_EXPOSURE, self._exposure_value):
                        break
                applied = camera.get(cv2.CAP_PROP_EXPOSURE)
                label = f"locked at {applied:g}"
            else:
                # V4L2: 1 = manual mode, 3 = automatic; CAP_PROP_EXPOSURE is
                # in 100 µs units, so the millisecond slider is ×10.
                camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
                camera.set(cv2.CAP_PROP_EXPOSURE, self._exposure_value * 10)
                applied = camera.get(cv2.CAP_PROP_EXPOSURE)
                label = f"locked at {applied / 10:.0f} ms"
            self.exposure_status_var.set(label)
            self.status_var.set("Exposure locked — check the fps meter; slide if too dark or blown out.")
        except Exception:
            self.exposure_status_var.set("not supported")
            self.status_var.set("This camera doesn't support manual exposure.")

    def _release_exposure_lock(self) -> None:
        camera = self._camera
        if camera is None:
            return
        try:
            if IS_WINDOWS:
                for aec_value in (0, 4, 3):
                    camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, aec_value)
                    if camera.get(cv2.CAP_PROP_AUTO_EXPOSURE) == aec_value:
                        break
            else:
                camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)  # V4L2 auto
        except Exception:
            pass
        self.exposure_status_var.set("")

    def _on_exposure_slider(self, value: float) -> None:
        self._exposure_value = int(round(value))
        self.exposure_scale_var.set(f"{self._exposure_value}")
        if self._exposure_locked:
            # Debounce: the slider fires continuously while dragging, but the
            # camera property write is comparatively slow. Coalesce writes.
            if not self._exposure_apply_pending:
                self._exposure_apply_pending = True
                self.after(150, self._apply_exposure_from_slider)

    def _apply_exposure_from_slider(self) -> None:
        self._exposure_apply_pending = False
        self._apply_exposure_lock()

    @staticmethod
    def _open_capture(index: int) -> cv2.VideoCapture | None:
        if IS_WINDOWS:
            # DirectShow is usually the least laggy backend on Windows.
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        else:
            # Linux: V4L2.
            backends = [cv2.CAP_V4L2, cv2.CAP_ANY]
        for backend in backends:
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                # MJPG keeps 30 fps at 720p on most UVC webcams (raw YUY2
                # caps near 10-15 fps at that size).
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                capture.set(cv2.CAP_PROP_FPS, 30)
                return capture
            capture.release()
        return None

    @staticmethod
    def _shrink_for_preview(frame: np.ndarray) -> np.ndarray:
        """Small BGR frame for display only (at most 1280x720)."""
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        height, width = frame.shape[:2]
        max_width, max_height = PREVIEW_SOURCE_MAX
        if width <= max_width and height <= max_height:
            return frame  # already small enough; zero extra cost
        scale = min(max_width / width, max_height / height)
        return cv2.resize(
            frame,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _camera_loop(self, index: int) -> None:
        capture = self._open_capture(index)
        if capture is None:
            self._ui_events.put((
                "camera_error",
                f"Camera {index} could not be opened. Close other apps using it "
                "(browser, OBS, Cheese), and check 'ls /dev/video*' and "
                "'v4l2-ctl --list-devices' (v4l-utils); your user may need to be "
                "in the 'video' group (then re-login).",
            ))
            return
        if self._exposure_locked:
            # The lock is a property of the camera device, so a reopen
            # (camera switch, Refresh) needs it re-applied.
            self._apply_exposure_lock()
        self._camera = capture
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        reported_fps = capture.get(cv2.CAP_PROP_FPS)
        mode = (
            f"{width}×{height} @ {reported_fps:.0f} fps (driver-reported)"
            if reported_fps
            else f"{width}×{height} (fps not reported)"
        )
        self._ui_events.put(("camera_ready", (index, mode)))
        failures = 0
        try:
            while not self._camera_stop.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= 30:
                        self._ui_events.put(("camera_error", "The webcam stopped providing frames. Check its connection and try again."))
                        break
                    time.sleep(0.03)
                    continue
                failures = 0
                # Shrink for display here, in the camera thread, so the UI
                # thread never touches a full-resolution frame. Recording is
                # unaffected: the writer still gets the original `frame`.
                preview = self._shrink_for_preview(frame)
                with self._frame_lock:
                    # cv2's read() returns a freshly allocated array every
                    # call and _shrink_for_preview builds a new one too, so
                    # storing references is safe — no 6 MB copy per frame.
                    self._latest_frame = frame
                    self._latest_frame_id += 1
                    self._preview_frame = preview
                self._capture_if_due(frame)
        finally:
            try:
                capture.release()
            except Exception:
                pass
            if self._camera is capture:
                self._camera = None

    def _capture_if_due(self, frame: np.ndarray) -> None:
        writer_to_finalize: FfmpegWriter | None = None
        encoder_error: str | None = None
        with self._state_lock:
            if not self.recording or self.paused or self.writer is None:
                return
            now = time.monotonic()
            if now < self.next_capture_at:
                return
            interval = capture_interval_seconds(self._speed, self._recording_fps)
            if interval > 0 and now > self.next_capture_at + interval:
                self.dropped_ticks += int((now - self.next_capture_at) // interval)
            # Skip missed moments rather than queueing old frames. RAM stays flat.
            self.next_capture_at = now + interval
            writer = self.writer
            try:
                writer.write(frame)
                self.frame_count += 1
            except Exception as exc:
                self.recording = False
                self.paused = False
                self.finalizing = True
                self.writer = None
                writer_to_finalize = writer
                encoder_error = str(exc)
        if writer_to_finalize is not None:
            self._ui_events.put(("encoder_failed", (writer_to_finalize, encoder_error)))

    # ---------- Sidebar scrolling ----------

    def _on_sidebar_wheel(self, event: tk.Event) -> None:
        """Wheel-scroll the settings sidebar while the pointer is over it."""
        if event.num in (4, 5):
            # Classic X11 wheel (Button-4 up / Button-5 down). Modern Tk on
            # X11 usually delivers the same physical scroll as <MouseWheel>
            # already; skip near-duplicates so we never scroll at 2× speed.
            if time.monotonic() - self._last_mousewheel < 0.05:
                return
            delta = 120 if event.num == 4 else -120
        else:
            delta = event.delta
            if not delta:
                return
            if not IS_WINDOWS:
                self._last_mousewheel = time.monotonic()
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self.control_canvas:
            widget = widget.master
        if widget is None:
            return
        # One notch (delta ±120) ≈ 60 px. The accumulator preserves
        # fractional motion from precision touchpads between events.
        self._wheel_accumulator -= delta / 2.0
        pixels = int(self._wheel_accumulator)
        self._wheel_accumulator -= pixels
        if pixels:
            self._pending_scroll_pixels += pixels
            self._schedule_scroll_apply()

    def _on_canvas_wheel(self, event: tk.Event) -> str:
        # Widget-level bindings run BEFORE the widget's class bindings, so
        # handling the wheel here and returning "break" stops Tk's own
        # canvas wheel-scroll from firing as well.
        self._on_sidebar_wheel(event)
        return "break"

    def _schedule_scroll_apply(self) -> None:
        """Coalesce bursts of wheel events into one scroll per ~16 ms.

        Touchpads emit a stream of tiny deltas; scrolling on each raw event
        forces a repaint per event, which is what made the sidebar tear.
        """
        if self._scroll_apply_scheduled:
            return
        self._scroll_apply_scheduled = True
        self.after(16, self._apply_pending_scroll)

    def _apply_pending_scroll(self) -> None:
        self._scroll_apply_scheduled = False
        pixels, self._pending_scroll_pixels = self._pending_scroll_pixels, 0
        if pixels:
            self._scroll_sidebar_by(pixels)

    def _scroll_sidebar_by(self, delta_pixels: int) -> None:
        """Scroll the sidebar by exact pixels, clamped to the content.

        Every scroll path funnels through here. Two Tk facts drive this:
        (1) canvas.yview_scroll() accepts only "units" or "pages" — an
        earlier "pixels" argument raised a TclError on every wheel event,
        which is why the wheel looked dead outside the scrollbar;
        (2) yview_moveto() takes a fraction of the scrollregion, which
        with the pixel math below gives exact positioning. Clamping keeps
        the view inside the content, so blank space is impossible.
        """
        canvas = self.control_canvas
        bbox = canvas.bbox("all")
        viewport_height = canvas.winfo_height()
        if not bbox or viewport_height <= 1:
            return
        _x1, y1, _x2, y2 = bbox
        content_height = y2 - y1
        if content_height <= viewport_height:
            # Everything fits: the only legal view is the top.
            if canvas.canvasy(0) != y1:
                canvas.yview_moveto(0.0)
            return
        current = canvas.canvasy(0)               # true view top, in pixels
        max_top = y2 - viewport_height            # no blank space below content
        desired = min(max(current + delta_pixels, y1), max_top)
        if abs(desired - current) > 0.5:
            # Mark "recently scrolled" so preview repaints pause briefly
            # instead of interleaving with this redraw (tearing mitigation).
            self._scrolling_until = time.monotonic() + 0.25
            canvas.yview_moveto((desired - y1) / content_height)

    def _clamped_yview(self, *args: Any) -> None:
        """Scrollbar -> canvas bridge; always re-clamp afterwards."""
        self.control_canvas.yview(*args)
        self._scroll_sidebar_by(0)

    def _on_sidebar_content_resized(self, _event: tk.Event) -> None:
        self.control_canvas.configure(scrollregion=self.control_canvas.bbox("all"))
        # Content height changed; the current view may now be out of range.
        self._scroll_sidebar_by(0)

    def _on_sidebar_viewport_resized(self, event: tk.Event) -> None:
        # Keep the settings panel exactly as wide as the canvas viewport.
        self.control_canvas.itemconfigure(self._control_window, width=event.width)
        # A taller/shorter viewport changes the legal scroll range.
        self._scroll_sidebar_by(0)

    # ---------- Rendering, disk guard and events ----------

    def _ui_tick(self) -> None:
        self._tick_count += 1
        try:
            self._drain_events()
            self._update_preview()
            self._update_preview_fps()
            self._fs_cursor_update()
            # Stats don't change fast enough to justify 30 Hz; ~6 updates/sec
            # keeps the numbers feeling live while most of each tick goes
            # to the preview.
            if self._tick_count % 5 == 0:
                self._update_recording_stats()
        except Exception:
            # Errors inside after() callbacks used to vanish into Tk's
            # default handler (stderr only) — invisible without a console.
            self._report_tick_error()
        finally:
            if self.winfo_exists():
                self.after(33, self._ui_tick)  # ~30 fps UI refresh

    def _report_tick_error(self) -> None:
        detail = traceback.format_exc()
        if detail == self._last_tick_error:      # nag once per unique error
            return
        self._last_tick_error = detail
        traceback.print_exc()
        last_line = detail.strip().splitlines()[-1]
        try:
            self.preview_label.configure(image="", text=f"UI error: {last_line}")
        except Exception:
            pass

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload = self._ui_events.get_nowait()
            except queue.Empty:
                return
            if kind == "camera_ready":
                index, mode = payload
                self._camera_open_confirmed = True
                self._camera_opened_at = time.monotonic()
                self.status_var.set(f"Camera {index} opened — {mode} — waiting for its first frame…")
            elif kind == "camera_error":
                with self._frame_lock:
                    has_frame = self._latest_frame is not None
                if not has_frame:
                    self.preview_label.configure(image="", text="Webcam unavailable")
                self.status_var.set(str(payload))
            elif kind == "finalized":
                destination, error = payload
                self._finish_finalization(destination, error)
            elif kind == "encoder_failed":
                writer, error = payload
                self._prevent_sleep(False)
                self._tray_update_state(False)
                self.status_var.set("The encoder stopped. Saving what was captured…")
                self._set_recording_controls(False, finalizing=True)
                threading.Thread(target=self._finalize_after_error, args=(writer, error), daemon=True).start()
            elif kind == "recap_done":
                destination, error = payload
                self._building_recap = False
                self._refresh_history_display()
                if error is not None:
                    self.status_var.set("The recap could not be built.")
                    messagebox.showerror(APP_NAME, f"The recap could not be built.\n\n{error}")
                elif destination is not None:
                    self._last_output = destination
                    try:
                        size = pretty_bytes(destination.stat().st_size)
                    except OSError:
                        size = "an unknown size"
                    self.status_var.set(f"Today's recap is ready — {destination.name} ({size})")
                    self.open_button.configure(state="normal")
                    self.show_button.configure(state="normal")
            elif kind == "tray_show":
                self.deiconify()
                self.lift()
            elif kind == "tray_start":
                self.deiconify()
                self.start_recording()
            elif kind == "tray_stop":
                self.stop_recording()
            elif kind == "tray_exit":
                self.deiconify()
                self._on_close(force=True)

    def _finalize_after_error(self, writer: FfmpegWriter, original_error: str) -> None:
        try:
            writer.close()
        except Exception:
            pass
        self._ui_events.put(("finalized", (None, original_error)))

    def _preview_target_size(self) -> tuple[int, int]:
        """Letterbox the preview into whatever space the panel currently has."""
        width = self.preview_label.winfo_width()
        height = self.preview_label.winfo_height()
        if width < 40 or height < 40:          # not laid out yet
            return PREVIEW_SIZE
        return width, height

    # ---------- Fullscreen preview ----------

    def _toggle_fullscreen_preview(self) -> None:
        if self._fs_window is not None and self._fs_window.winfo_exists():
            self._close_fullscreen_preview()
        else:
            self._open_fullscreen_preview()

    def _open_fullscreen_preview(self) -> None:
        window = tk.Toplevel(self)
        self._fs_window = window
        window.title("LapseCam — Fullscreen preview")
        window.configure(background="#000000")
        try:
            window.attributes("-fullscreen", True)
        except tk.TclError:
            try:
                window.attributes("-zoomed", True)  # X11 maximize fallback
            except tk.TclError:
                pass
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        self._fs_label = ttk.Label(
            window, text="Waiting for the camera…",
            style="Preview.TLabel", anchor="center",
        )
        self._fs_label.grid(row=0, column=0, sticky="nsew")
        window.bind("<Escape>", lambda _event: self._close_fullscreen_preview())
        window.bind("<F11>", lambda _event: self._toggle_fullscreen_preview())
        window.bind("<Double-Button-1>", lambda _event: self._toggle_fullscreen_preview())
        window.bind("<Control-r>", lambda _event: self.start_recording())
        window.bind("<Motion>", self._on_fs_motion)
        window.protocol("WM_DELETE_WINDOW", self._close_fullscreen_preview)
        window.focus_force()
        window.lift()
        self._fs_last_motion = time.monotonic()
        self._fs_cursor_hidden = False

    def _close_fullscreen_preview(self) -> None:
        window = self._fs_window
        self._fs_window = None
        self._fs_label = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _on_fs_motion(self, _event: tk.Event) -> None:
        self._fs_last_motion = time.monotonic()
        if self._fs_cursor_hidden:
            self._fs_cursor_hidden = False
            window = self._fs_window
            if window is not None and window.winfo_exists():
                try:
                    window.configure(cursor="")
                except tk.TclError:
                    pass

    def _fs_cursor_update(self) -> None:
        """Hide the mouse pointer in fullscreen after ~3 s of stillness."""
        window = self._fs_window
        if window is None or not window.winfo_exists():
            return
        if not self._fs_cursor_hidden and time.monotonic() - self._fs_last_motion > 3.0:
            self._fs_cursor_hidden = True
            try:
                window.configure(cursor="none")
            except tk.TclError:
                pass

    def _fs_target_size(self) -> tuple[int, int]:
        label = self._fs_label
        if label is None:
            return PREVIEW_SIZE
        width = label.winfo_width()
        height = label.winfo_height()
        if width < 40 or height < 40:
            return PREVIEW_SIZE
        return width, height

    # ---------- Preview rendering ----------

    def _preview_overlay_text(self) -> str | None:
        """The focus clock burned into the bottom of the preview while recording."""
        with self._state_lock:
            recording = self.recording
            paused = self.paused
            started_at = self.started_at
            frames = self.frame_count
            speed = self._speed
            fps = self._recording_fps
            session_ends_at = self._session_ends_at
        if not (recording or paused) or self.finalizing:
            return None
        elapsed = time.monotonic() - started_at
        video_seconds = frames / fps
        text = f"{'PAUSED — ' if paused else ''}{pretty_duration(elapsed)} real → {pretty_duration(video_seconds)} video"
        if session_ends_at is not None:
            text += f"  •  {pretty_duration(session_ends_at - time.monotonic())} left"
        text += f"  •  {format_speed(speed)} @ {fps} fps"
        return text

    def _update_preview(self) -> None:
        if time.monotonic() < self._scrolling_until:
            # A sidebar scroll is in flight; the 30 Hz preview repaint would
            # interleave with the sidebar redraw and tear. Pause frames for a
            # moment — invisible in practice (< 0.25 s).
            return
        with self._frame_lock:
            has_frame = self._latest_frame is not None
            preview = self._preview_frame
            frame_id = self._latest_frame_id
        if not has_frame:
            if not self._camera_open_confirmed:
                # Driver still negotiating — some webcams legitimately take
                # several seconds here. Don't imply anything is wrong yet.
                waited = time.monotonic() - self._camera_thread_started_at
                if waited > 3 and not self._shown_opening_note:
                    self._shown_opening_note = True
                    self.preview_label.configure(
                        text=(f"Opening {self.camera_var.get()}… some webcams take "
                              "a few seconds to deliver their first frame.")
                    )
                return
            opened_for = time.monotonic() - self._camera_opened_at
            if opened_for > 10 and not self._shown_no_frame_hint:
                # Opened fine but no frames for 10 s — NOW the privacy /
                # exclusive-use explanation is the likely one.
                self._shown_no_frame_hint = True
                self.preview_label.configure(
                    text=(
                        "The camera opened but hasn't produced a frame for 10 seconds. "
                        "Make sure no other app (browser, OBS, Cheese) has the camera open, "
                        "and check that your user can read /dev/video* "
                        "(ls -l /dev/video*; the 'video' group may be needed)."
                    )
                )
            return
        if preview is None or frame_id == self._shown_frame_id:
            return
        first_of_session = self._shown_frame_id == -1
        overlay = self._preview_overlay_text()
        try:
            photo = self._to_photoimage(preview, self._preview_target_size(), overlay)
        except Exception as exc:  # deliberately broad: any conversion failure
            # should surface in the label, not silently freeze the preview.
            self._preview_failures += 1
            if self._preview_failures in (1, 10, 60, 300):
                traceback.print_exc(file=sys.stderr)
                self.preview_label.configure(text=f"Preview unavailable ({exc.__class__.__name__}): {exc}")
            return
        self._preview_failures = 0
        self.preview_label.configure(image=photo, text="")
        self.preview_label.image = photo  # Keep Tk's image object alive.
        self._shown_frame_id = frame_id
        self._preview_fps_frames += 1
        fs_window = self._fs_window
        if fs_window is not None and fs_window.winfo_exists() and self._fs_label is not None:
            try:
                fs_photo = self._to_photoimage(
                    preview, self._fs_target_size(), overlay, corner_hint=True,
                )
                self._fs_label.configure(image=fs_photo, text="")
                self._fs_label.image = fs_photo
            except Exception:
                pass  # the main preview already surfaces conversion errors
        if first_of_session:
            self.status_var.set(f"{self.camera_var.get()} is live. Choose a speed and start your timelapse.")

    def _update_preview_fps(self) -> None:
        """Measure the real preview rate once per second and show it."""
        now = time.monotonic()
        elapsed = now - self._preview_fps_window
        if elapsed < 1.0:
            return
        self._preview_fps = round(self._preview_fps_frames / elapsed)
        self._preview_fps_frames = 0
        self._preview_fps_window = now
        if self._preview_fps > 0:
            self.preview_hint.configure(
                text=f"No audio • frames compressed straight into one MP4 • preview {self._preview_fps} fps"
            )

    @staticmethod
    def _overlay_font(size: int):
        return load_font(size)

    @staticmethod
    def _draw_overlay(image: Image.Image, text: str | None, corner_hint: bool = False) -> Image.Image:
        """Focus-clock bar bottom-left; optional small exit hint bottom-right."""
        base = image.convert("RGBA")
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if text:
            font = LapseCam._overlay_font(max(16, base.height // 14))
            try:
                text_width = draw.textlength(text, font=font)
            except Exception:
                text_width = font.size * len(text) * 0.6
            text_height = font.size + 8
            box = (12, base.height - text_height - 22, 16 + int(text_width) + 20, base.height - 12)
            colours = theme()
            box_fill = _hex_to_rgb(colours["panel"]) + (185,)
            box_outline = _hex_to_rgb(colours["accent"]) + (150,)
            text_fill = _hex_to_rgb(colours["body"]) + (255,)
            try:
                draw.rounded_rectangle(box, radius=8, fill=box_fill, outline=box_outline)
            except Exception:
                draw.rectangle(box, fill=box_fill)
            draw.text((26, (box[1] + box[3]) // 2), text, font=font, fill=text_fill, anchor="lm")
        if corner_hint:
            hint_font = LapseCam._overlay_font(max(12, base.height // 30))
            try:
                draw.text((base.width - 16, base.height - 14), "F11 / Esc — exit",
                          font=hint_font, fill=_hex_to_rgb(theme()["subtitle"]) + (210,), anchor="rb")
            except Exception:
                pass
        return Image.alpha_composite(base, layer).convert("RGB")

    @staticmethod
    def _to_photoimage(
        frame: np.ndarray,
        target_size: tuple[int, int],
        overlay: str | None = None,
        corner_hint: bool = False,
    ) -> ImageTk.PhotoImage:
        target_width, target_height = target_size
        if frame.ndim == 2:  # grayscale source
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:  # BGRA source
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        height, width = frame.shape[:2]
        scale = min(target_width / width, target_height / height)
        scaled_width = max(1, int(width * scale))
        scaled_height = max(1, int(height * scale))
        resized = cv2.resize(frame, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        top = (target_height - scaled_height) // 2
        left = (target_width - scaled_width) // 2
        # Pillow's Image.fromarray expects RGB order (the opposite of
        # cv2.imencode, which expects/assumes BGR and swaps internally).
        canvas[top : top + scaled_height, left : left + scaled_width] = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        # Building the preview through Pillow instead of Tk's own
        # PhotoImage(data=..., format=...) string parser sidesteps the
        # underlying problem entirely: Tk's built-in PNG/PPM readers are
        # optional, build-dependent features of whatever Tcl/Tk happens to
        # be bundled with this Python install. ImageTk.PhotoImage instead
        # hands Tk raw pixel data directly through Tk_PhotoPutBlock, which
        # every Tk build supports.
        image = Image.fromarray(canvas, mode="RGB")
        if overlay or corner_hint:
            image = LapseCam._draw_overlay(image, overlay, corner_hint)
        return ImageTk.PhotoImage(image)

    def _update_recording_stats(self) -> None:
        with self._state_lock:
            recording = self.recording
            paused = self.paused
            writer = self.writer
            started_at = self.started_at
            frames = self.frame_count
            dropped = self.dropped_ticks
            speed = self._speed
            fps = self._recording_fps
        if not recording:
            if not self.finalizing and self._last_output is None:
                self.stats_var.set("Ready for a webcam-only timelapse • output frame rate is set in the sidebar")
            return
        elapsed = time.monotonic() - started_at
        video_seconds = frames / fps
        written = writer.output_bytes() if writer is not None else 0
        state = "paused" if paused else "recording"
        self.stats_var.set(
            f"{state.title()} {pretty_duration(elapsed)} real time → {pretty_duration(video_seconds)} video  •  "
            f"{format_speed(speed)} @ {fps} fps  •  {frames:,} frames  •  {pretty_bytes(written)} written"
            + (f"  •  {dropped} late samples skipped" if dropped else "")
        )
        if self._session_ends_at is not None:
            remaining = self._session_ends_at - time.monotonic()
            if remaining <= 0:
                if not self._session_auto_stopping and not self.finalizing:
                    self._session_auto_stopping = True
                    self._session_completed = True
                    self.status_var.set("Session complete — stopping and saving…")
                    self._chime()
                    self.after_idle(self.stop_recording)
                return
            self.stats_var.set(self.stats_var.get() + f"  •  {pretty_duration(remaining)} left in session")
        now = time.monotonic()
        if now - self._last_disk_check < 4:
            return
        self._last_disk_check = now
        try:
            folder = Path(self.folder_var.get()).expanduser()
            free = shutil.disk_usage(folder).free
            self.storage_var.set(f"Saving continuously • {pretty_bytes(free)} free on the selected drive")
            if free < MIN_FREE_BYTES and not self._auto_stopping:
                self._auto_stopping = True
                self.status_var.set("Storage is below 1 GB. Stopping safely to protect the drive…")
                self.after_idle(self.stop_recording)
        except OSError:
            pass

    # ---------- System tray ----------

    def _tray_action(self, kind: str):
        def handler(_icon, _item):
            # pystray callbacks run on its own thread; forward every request
            # to the Tk thread via the event queue.
            self._ui_events.put((kind, None))
        return handler

    def _build_tray(self) -> None:
        if not HAVE_PYSTRAY:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Show LapseCam", self._tray_action("tray_show"), default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start timelapse", self._tray_action("tray_start")),
                pystray.MenuItem("Stop & save", self._tray_action("tray_stop")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._tray_action("tray_exit")),
            )
            self._tray_icon = pystray.Icon(APP_NAME, self._make_tray_image(False), APP_NAME, menu)
            self._tray_icon.run_detached()
        except Exception:
            self._tray_icon = None

    @staticmethod
    def _app_icon_image(size: int, recording: bool = False) -> Image.Image:
        """The LapseCam mark: dark rounded square with a status dot."""
        colours = theme()
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        pad = max(1, size // 16)
        radius = max(2, size // 5)
        panel_fill = _hex_to_rgb(colours["panel"]) + (255,)
        try:
            draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius, fill=panel_fill)
        except Exception:
            draw.rectangle((pad, pad, size - pad, size - pad), fill=panel_fill)
        dot = size // 4
        centre = size // 2
        draw.ellipse(
            (centre - dot, centre - dot, centre + dot, centre + dot),
            fill=_hex_to_rgb(colours["danger"] if recording else colours["accent"]) + (255,),
        )
        return image

    def _make_tray_image(self, recording: bool) -> Image.Image:
        base = self._load_custom_icon(64)
        if base is None:
            return self._app_icon_image(64, recording)
        if not recording:
            return base
        # Recording = red dot badge in the corner of your icon.
        image = base.copy()
        draw = ImageDraw.Draw(image)
        colours = theme()
        draw.ellipse((44, 44, 62, 62), fill=_hex_to_rgb(colours["danger"]) + (255,))
        draw.ellipse((44, 44, 62, 62), outline=_hex_to_rgb(colours["preview"]) + (255,), width=2)
        return image

    def _icon_source_path(self) -> Path | None:
        """Locate myicon.png whether running as a script or a frozen exe."""
        if hasattr(sys, "_MEIPASS"):
            candidate = Path(sys._MEIPASS) / "myicon.png"
        else:
            candidate = Path(__file__).parent / "myicon.png"
        return candidate if candidate.exists() else None

    def _load_custom_icon(self, size: int) -> Image.Image | None:
        """myicon.png as a square RGBA image at `size`, or None if absent."""
        source = self._icon_source_path()
        if source is None:
            return None
        try:
            image = Image.open(source).convert("RGBA")
            # Center-crop to square so non-square art doesn't distort.
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            if image.size != (size, size):
                image = image.resize((size, size), Image.LANCZOS)
            return image
        except Exception:
            return None

    def _set_window_icon(self) -> None:
        """Title-bar and taskbar icon — your myicon.png, code-drawn mark as
        fallback if the file is missing."""
        try:
            custom = self._load_custom_icon(256)
            if custom is not None:
                photo = ImageTk.PhotoImage(custom)
            else:
                photo = ImageTk.PhotoImage(self._app_icon_image(256))
            # True = every future Toplevel (fullscreen preview) inherits it.
            # The reference must be kept or garbage collection kills the image.
            self.iconphoto(True, photo)
            self._window_icon_photo = photo
        except Exception:
            pass

    def _tray_update_state(self, recording: bool) -> None:
        if self._tray_icon is None:
            return
        try:
            self._tray_icon.icon = self._make_tray_image(recording)
        except Exception:
            pass

    def _stop_tray(self) -> None:
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

    # ---------- Desktop conveniences ----------

    def _prevent_sleep(self, enabled: bool) -> None:
        """Keep the machine awake while recording.

        Linux: holds a systemd-logind inhibitor (sleep + idle) for the
        duration of the recording. Windows: SetThreadExecutionState.
        """
        if IS_WINDOWS:
            try:
                continuous = 0x80000000
                system_required = 0x00000001
                display_required = 0x00000002
                flags = continuous | system_required | display_required if enabled else continuous
                ctypes.windll.kernel32.SetThreadExecutionState(flags)
            except Exception:
                pass
            return
        if enabled:
            if self._inhibit_process is None:
                try:
                    self._inhibit_process = subprocess.Popen(
                        ["systemd-inhibit",
                         "--what=sleep:idle",
                         "--who=LapseCam",
                         "--why=Recording a webcam timelapse",
                         "--mode=block",
                         "sleep", "infinity"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        preexec_fn=_set_pdeathsig,
                    )
                except Exception:
                    self._inhibit_process = None
        else:
            process, self._inhibit_process = self._inhibit_process, None
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

    @staticmethod
    def _chime() -> None:
        """Soft system sound when a timed session completes."""
        if IS_WINDOWS:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
            return
        # freedesktop event sound via libcanberra; terminal bell fallback.
        try:
            subprocess.Popen(
                ["canberra-gtk-play", "-i", "complete"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass
        print("\a", end="", flush=True)

    def _open_video(self) -> None:
        if self._last_output and self._last_output.exists():
            open_with_default_app(self._last_output)

    def _show_folder(self) -> None:
        if self._last_output and self._last_output.exists():
            if not self._reveal_in_file_manager(self._last_output):
                open_with_default_app(self._last_output.parent)
        else:
            folder = Path(self.folder_var.get()).expanduser()
            if folder.exists():
                open_with_default_app(folder)

    def _reveal_in_file_manager(self, path: Path) -> bool:
        """Best effort: ask the desktop's file manager (Nautilus, Dolphin,
        Nemo, Thunar…) to open the folder with the file selected."""
        try:
            result = subprocess.run(
                ["dbus-send", "--session",
                 "--dest=org.freedesktop.FileManager1",
                 "--type=method_call",
                 "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems",
                 f"array:string:file://{path.resolve()}",
                 "string:"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _on_close(self, force: bool = False) -> None:
        if self.finalizing:
            self._closing_after_save = True
            self.status_var.set("Finishing the current video before closing…")
            return
        if self.recording:
            if messagebox.askyesno(APP_NAME, "Stop and save the current timelapse before closing?"):
                self._closing_after_save = True
                self.stop_recording()
            return
        if not force and self._tray_icon is not None:
            # The window's X button hides to the tray; the tray menu's Exit
            # (force=True) is the path that actually quits while idle.
            self.withdraw()
            if not self._tray_hidden_hint_shown:
                self._tray_hidden_hint_shown = True
                try:
                    self._tray_icon.notify("LapseCam is still running in the tray.", APP_NAME)
                except Exception:
                    pass
            return
        self._shutdown()

    def _shutdown(self) -> None:
        self._prevent_sleep(False)
        self._close_fullscreen_preview()
        self._stop_camera()
        self._stop_tray()
        self.destroy()


if __name__ == "__main__":
    app = LapseCam()
    app.mainloop()
