#!/usr/bin/env python3
"""
Optical CT Scan UI — integrated with scan_sample.py and compute_dose_profile.py logic.

Phases:
  1. Dark capture           (lamp off, no sample)
  2. Flat capture           (lamp on, no sample)
  3. Pre-irradiation scan   (lamp on, sample rotating → saved to pre/)
  4. Post-irradiation scan  (user irradiates sample, replaces it, clicks to start)
                             (lamp on, sample rotating → saved to post/)
  5. Subtraction            (ΔA = A_post − A_pre, auto-computed, saved to subtracted/)
  6. Reconstruction + dose profiling (inline FBP on ΔA projections)

Absorbance conversion: A = −log(I / I₀) where I₀ is the flat-field image.
ΔA projections are encoded as uint16 PNG (value = ΔA × OD_SCALE).

Image directories under each scan folder:
  pre/         — raw intensity projections before irradiation
  post/        — raw intensity projections after irradiation
  subtracted/  — ΔA = A_post − A_pre projections (uint16, OD_SCALE encoded)

Hardware is driven from QThread workers; the main thread only updates the UI.
Camera preview uses the real Pi camera HTTP endpoint; falls back to simulator.

Requires: PyQt6, numpy, opencv-python, requests, pyserial, blinka/digitalio,
          scikit-image, matplotlib, pandas, openpyxl
"""

import os
import sys
import json
import time
import math
import shutil
import socket
import string
import glob
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, List
from urllib.parse import urlparse as _urlparse

import numpy as np
import cv2
import requests

from PyQt6.QtCore import (
    Qt, QTimer, QRectF, QPointF, QSize, pyqtSignal, QObject, QThread
)
from PyQt6.QtGui import (
    QAction, QImage, QPixmap, QPainter, QPen, QColor, QBrush, QFont, QPalette
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QSpinBox, QProgressBar, QGroupBox, QFileDialog,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem, QGraphicsLineItem,
    QDialog, QListWidget, QListWidgetItem, QComboBox, QMessageBox, QFormLayout,
    QLineEdit, QCheckBox, QTabWidget, QDoubleSpinBox, QSplitter, QFrame,
    QScrollArea, QTextEdit
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# ──────────────────────────────────────────────────────────────────────────────
# Paths / constants
# ──────────────────────────────────────────────────────────────────────────────

APP_TITLE        = "Optical CT Scanner"
_VERSION_MAJOR   = 1
_VERSION_MINOR   = 0

def _compute_version() -> str:
    try:
        import subprocess
        patch = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        if patch.isdigit():
            return f"{_VERSION_MAJOR}.{_VERSION_MINOR}.{patch}"
    except Exception:
        pass
    return f"{_VERSION_MAJOR}.{_VERSION_MINOR}.0"

APP_VERSION = _compute_version()

HOME = Path.home()
BASE_DIR    = HOME / "OCT"
SCANS_DIR   = BASE_DIR / "scans"
CONFIG_DIR  = BASE_DIR / "config"
for d in (SCANS_DIR, CONFIG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Write a light-coloured down-arrow SVG for the QComboBox indicator so it is
# visible on the dark background regardless of the system palette.
_ARROW_SVG_PATH = CONFIG_DIR / "arrow_down.svg"
_ARROW_SVG_PATH.write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8" viewBox="0 0 8 8">'
    '<polygon points="0,1 8,1 4,7" fill="#e2e6ed"/>'
    '</svg>'
)

CAMERA_URL      = "http://192.168.7.2:8000"
_cam            = _urlparse(CAMERA_URL)
CAMERA_HOST     = _cam.hostname
CAMERA_PORT     = _cam.port or 80
PING_INTERVAL_MS = 20_000   # keep USB-ethernet alive; check camera reachability
SERIAL_PORT     = "/dev/tty.usbserial-A9TKD8CR"     # stepper motor
BAUDRATE        = 9600
SERIAL_TIMEOUT  = 1

FULL_STEPS_PER_REV = 200
MICROSTEPS         = 256
STEPS_PER_REV      = FULL_STEPS_PER_REV * MICROSTEPS
INITIAL_VELOCITY   = 50
MAX_VELOCITY       = 768000
ACCELERATION       = 10000
DECELERATION       = 10000
RUN_CURRENT        = 100
DEVICE_NAME        = 'A'
MOTION_STATUS_VAR  = "MV"

CALIBRATION_JSON   = Path(__file__).resolve().parent / "camera_calibration_charuco.json"

MM_PER_PIXEL_XZ  = 43 / 454
MM_PER_SLICE_Y   = 0.1


# ──────────────────────────────────────────────────────────────────────────────
# Palette – dark industrial
# ──────────────────────────────────────────────────────────────────────────────

DARK_BG     = "#0d0f12"
PANEL_BG    = "#13161b"
BORDER_CLR  = "#252932"
ACCENT      = "#00d4aa"
ACCENT2     = "#0099ff"
TEXT_MAIN   = "#e2e6ed"
TEXT_DIM    = "#5a6070"
BTN_ACTIVE  = "#00d4aa"
BTN_STOP    = "#e05252"
PHASE_IDLE  = "#252932"
PHASE_ACTIVE= "#00d4aa"
PHASE_DONE  = "#1a3d34"

# Absorbance encoding: uint16 PNG value = ΔA * OD_SCALE
# Covers OD range [0, 4] with ~0.00006 OD resolution.
OD_SCALE = 65535.0 / 4.0


STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {DARK_BG};
    color: {TEXT_MAIN};
    font-family: 'Courier New', monospace;
    font-size: 12px;
}}
QGroupBox {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER_CLR};
    border-radius: 4px;
    margin-top: 18px;
    padding: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    color: {ACCENT};
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QPushButton {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER_CLR};
    border-radius: 3px;
    color: {TEXT_MAIN};
    padding: 6px 14px;
    min-height: 28px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: #1e2229;
}}
QPushButton#start_btn {{
    background-color: #0d2e25;
    border: 1px solid {ACCENT};
    color: {ACCENT};
    font-size: 13px;
    font-weight: bold;
    min-height: 36px;
}}
QPushButton#start_btn:hover {{
    background-color: #15443a;
}}
QPushButton#start_btn:disabled {{
    background-color: {PANEL_BG};
    border: 1px solid #1e2229;
    color: {TEXT_DIM};
}}
QPushButton#stop_btn {{
    background-color: #2e1111;
    border: 1px solid {BTN_STOP};
    color: {BTN_STOP};
    font-size: 13px;
    font-weight: bold;
    min-height: 36px;
}}
QPushButton#stop_btn:hover {{
    background-color: #441515;
}}
QPushButton#stop_btn:disabled {{
    background-color: {PANEL_BG};
    border: 1px solid #1e2229;
    color: {TEXT_DIM};
}}
QProgressBar {{
    background-color: #0d0f12;
    border: 1px solid {BORDER_CLR};
    border-radius: 2px;
    text-align: center;
    color: {TEXT_MAIN};
    height: 18px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 2px;
}}
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {{
    background-color: {DARK_BG};
    border: 1px solid {BORDER_CLR};
    border-radius: 2px;
    color: {TEXT_MAIN};
    padding: 3px 6px;
    min-height: 24px;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border-left: 1px solid {BORDER_CLR};
    width: 20px;
}}
QComboBox::down-arrow {{
    image: url({_ARROW_SVG_PATH});
    width: 8px;
    height: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER_CLR};
    color: {TEXT_MAIN};
    selection-background-color: #0d2e25;
    selection-color: {ACCENT};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 4px 8px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: #0d2e25;
    color: {ACCENT};
}}
QLabel {{
    color: {TEXT_MAIN};
}}
QLabel#dim {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QLabel#accent {{
    color: {ACCENT};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER_CLR};
    background-color: {PANEL_BG};
}}
QTabBar::tab {{
    background-color: {DARK_BG};
    border: 1px solid {BORDER_CLR};
    color: {TEXT_DIM};
    padding: 5px 16px;
}}
QTabBar::tab:selected {{
    background-color: {PANEL_BG};
    color: {ACCENT};
    border-bottom-color: {PANEL_BG};
}}
QListWidget {{
    background-color: {DARK_BG};
    border: 1px solid {BORDER_CLR};
    color: {TEXT_MAIN};
}}
QListWidget::item:selected {{
    background-color: #0d2e25;
    color: {ACCENT};
}}
QScrollBar:vertical {{
    background: {DARK_BG};
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_CLR};
    border-radius: 4px;
}}
QTextEdit {{
    background-color: {DARK_BG};
    border: 1px solid {BORDER_CLR};
    color: {TEXT_DIM};
    font-family: 'Courier New', monospace;
    font-size: 11px;
}}
QMenuBar {{
    background-color: {PANEL_BG};
    color: {TEXT_MAIN};
    border-bottom: 1px solid {BORDER_CLR};
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 12px;
}}
QMenuBar::item:selected {{
    background-color: #0d2e25;
    color: {ACCENT};
}}
QMenu {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER_CLR};
    color: {TEXT_MAIN};
}}
QMenu::item {{
    padding: 5px 24px 5px 12px;
}}
QMenu::item:selected {{
    background-color: #0d2e25;
    color: {ACCENT};
}}
"""


# ──────────────────────────────────────────────────────────────────────────────
# Camera: real HTTP + simulator fallback
# ──────────────────────────────────────────────────────────────────────────────

class CameraSimulator:
    def __init__(self, w=2028, h=1520):
        self.w, self.h = w, h
        self.t0 = time.time()

    def get_frame(self, stack=1) -> Optional[np.ndarray]:
        t = time.time() - self.t0
        y = np.linspace(0, 1, self.h, dtype=np.float32)[:, None]
        x = np.linspace(0, 1, self.w, dtype=np.float32)[None, :]
        cx = 0.5 + 0.15 * math.sin(t * 0.7)
        cy = 0.5 + 0.12 * math.cos(t * 0.9)
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        blob = np.exp(-(r ** 2) / (2 * (0.12 ** 2)))
        stripes = 0.5 + 0.5 * np.sin(2 * math.pi * (x * 4.0 + t * 0.25))
        img = np.clip(0.25 * stripes + 0.85 * blob + 0.15 * y, 0, 1)
        img = (img * 255).astype(np.uint8)
        noise = np.random.normal(0, 4, img.shape).astype(np.int16)
        return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def take_photo_http(stack: int = 3) -> Optional[np.ndarray]:
    try:
        resp = requests.get(f"{CAMERA_URL}/capture?stack={stack}", timeout=10)
        resp.raise_for_status()
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None


def probe_camera() -> bool:
    """Return True if the RPi camera server is reachable (TCP connect only)."""
    try:
        with socket.create_connection((CAMERA_HOST, CAMERA_PORT), timeout=2):
            return True
    except OSError:
        return False


class CameraProbeWorker(QObject):
    """Lightweight keep-alive: opens a TCP connection to the RPi, reports reachability."""
    result = pyqtSignal(bool)

    def run(self):
        self.result.emit(probe_camera())


# ──────────────────────────────────────────────────────────────────────────────
# GPIO lamp helpers (with graceful fallback)
# ──────────────────────────────────────────────────────────────────────────────

def _make_lamp_fns():
    try:
        os.environ['BLINKA_FT232H'] = '1'
        import board
        import digitalio

        def _lamp(state: bool):
            p = digitalio.DigitalInOut(board.C0)
            p.direction = digitalio.Direction.OUTPUT
            p.value = state

        return lambda: _lamp(True), lambda: _lamp(False)
    except Exception:
        return lambda: None, lambda: None

lamp_on, lamp_off = _make_lamp_fns()


# ──────────────────────────────────────────────────────────────────────────────
# Serial helpers
# ──────────────────────────────────────────────────────────────────────────────

def serial_send(ser, cmd: str):
    ser.write((cmd + "\r").encode("ascii"))
    ser.flush()

def serial_wait_stopped(ser, dn_char='A', timeout_s=10.0):
    t0 = time.time()
    buf = ""
    while True:
        if time.time() - t0 > timeout_s:
            raise TimeoutError("Timed out waiting for move complete")
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting).decode(errors="ignore")
            if dn_char in buf:
                return


# ──────────────────────────────────────────────────────────────────────────────
# Calibration scan helper
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_calibration_scan(path: str, capture_fn, average_stack=5,
                                    force_new=False, label="calibration"):
    png_path = os.path.splitext(path)[0] + ".png"
    if os.path.exists(path) and not force_new:
        return np.load(path)

    image = capture_fn(average_stack)
    if image is None:
        return None

    np.save(path, image)
    png_img = image.astype(np.float32)
    lo, hi = np.percentile(png_img, (0.5, 99.5))
    if hi > lo:
        png_img = (png_img - lo) / (hi - lo)
    png_img = (np.clip(png_img, 0, 1) * 65535).astype(np.uint16)
    cv2.imwrite(png_path, png_img)
    return image


# ──────────────────────────────────────────────────────────────────────────────
# Reconstruction helpers (from compute_dose_profile.py)
# ──────────────────────────────────────────────────────────────────────────────

def load_png_stack(proj_dir, pattern="*.png"):
    from pathlib import Path as _P
    files = sorted(glob.glob(os.path.join(proj_dir, pattern)))
    if not files:
        raise FileNotFoundError("No PNG projections found")
    imgs = []
    for f in files:
        im = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if im is None:
            raise RuntimeError(f"Failed to read {f}")
        imgs.append(im.astype(np.float32))
    return np.stack(imgs, axis=0)

def line_integrals(imgs, dark, flat, eps=1e-6):
    num = np.clip(imgs - dark, eps, None)
    den = np.clip(flat - dark, eps, None)
    T = np.clip(num / den, 1e-3, 1.0)
    return -np.log(T)

def recon_volume_fbp(P, angles_deg, filter_name="hann", circle=True,
                     output_size=None, progress_cb=None):
    from skimage.transform import iradon
    from concurrent.futures import ThreadPoolExecutor, as_completed
    A, H, W = P.shape
    if output_size is None:
        output_size = W
    vol = np.zeros((H, output_size, output_size), dtype=np.float32)

    def recon_slice(y):
        sino = P[:, y, :].T
        return y, iradon(sino, theta=angles_deg, filter_name=filter_name,
                         circle=circle, output_size=output_size).astype(np.float32)

    n_workers = os.cpu_count() or 1
    completed = 0
    report_every = max(1, H // 50)
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(recon_slice, y): y for y in range(H)}
        for future in as_completed(futures):
            y, result = future.result()
            vol[y] = result
            completed += 1
            if progress_cb and completed % report_every == 0:
                progress_cb(int(100 * completed / H))
    return vol

def crop_window(imgs, dark, flat, cx, top, extent):
    half = extent // 2
    y0, y1 = top, top + extent
    x0, x1 = int(cx - half), int(cx + half)
    H, W = dark.shape
    if y0 < 0 or x0 < 0 or y1 > H or x1 > W:
        raise ValueError(f"Crop exceeds image bounds (H={H}, W={W})")
    return imgs[:, y0:y1, x0:x1], dark[y0:y1, x0:x1], flat[y0:y1, x0:x1]

def dose_profile_from_volume(mu_vol, mm_per_pixel_xz, depth_y=None,
                              roi_radius_px=10, lateral_axis="z"):
    Y, Z, X = mu_vol.shape
    y0 = Y // 2 if depth_y is None else int(depth_y)
    plane = mu_vol[y0]
    zc, xc = Z // 2, X // 2
    r = int(roi_radius_px)
    if lateral_axis.lower() == "z":
        xL, xR = max(0, xc - r), min(X, xc + r + 1)
        od = plane[:, xL:xR].mean(axis=1)
        pos_mm = (np.arange(Z) - zc) * mm_per_pixel_xz
    else:
        zL, zR = max(0, zc - r), min(Z, zc + r + 1)
        od = plane[zL:zR, :].mean(axis=0)
        pos_mm = (np.arange(X) - xc) * mm_per_pixel_xz
    rel = od / (float(np.max(od)) + 1e-12)
    return pos_mm, rel, od

def depth_dose_from_central_axis(mu_vol, mm_per_slice_y, sample_top_px,
                                  sample_height_px, roi_radius_px=10,
                                  edge_baseline_px=50, invert=False):
    Y, Z, X = mu_vol.shape
    zc, xc = Z // 2, X // 2
    r = int(roi_radius_px)
    zL, zR = max(0, zc - r), min(Z, zc + r + 1)
    xL, xR = max(0, xc - r), min(X, xc + r + 1)
    y0 = int(sample_top_px)
    y1 = min(Y, y0 + int(sample_height_px))
    od_depth = mu_vol[y0:y1, zL:zR, xL:xR].mean(axis=(1, 2))
    n = min(edge_baseline_px, len(od_depth) // 4)
    baseline = np.mean(np.concatenate([od_depth[:n], od_depth[-n:]]))
    dose = baseline - od_depth if invert else od_depth - baseline
    dose = np.maximum(dose, 0)
    rel = dose / (np.max(dose) + 1e-12)
    depth_mm = np.arange(len(od_depth)) * mm_per_slice_y
    return depth_mm, rel, dose, od_depth

def find_axis_from_nozzle(img: np.ndarray) -> Optional[float]:
    """
    Estimate the axis-of-rotation X coordinate from a flat-field image.

    The nozzle holder is mounted above the bright backlit background and casts
    a dark vertical band over it.  The axis of rotation is the horizontal
    centre of that band.

    Method: take the top 20 % of the image (where the holder is visible),
    exclude the outermost 15 % of columns on each side (frame borders), then
    find the column with the lowest mean intensity after Gaussian smoothing.

    Returns the centre X in image pixels, or None if the image is empty.
    """
    h, w = img.shape[:2]
    if h == 0 or w == 0:
        return None

    # Strip containing the nozzle holder
    top_h = max(1, int(h * 0.20))
    top = img[:top_h, :].astype(np.float32)

    # Exclude dark frame borders (leftmost / rightmost 15 % of columns)
    border = max(1, int(w * 0.15))
    inner = top[:, border: w - border]   # shape: (top_h, inner_w)
    if inner.shape[1] == 0:
        return None

    # Per-column mean intensity — nozzle holder → darkest vertical band
    col_means = inner.mean(axis=0)

    # Smooth with a narrow Gaussian to suppress noise
    sigma = max(3.0, w * 0.01)
    ks = int(sigma * 6) | 1          # odd kernel size
    half = ks // 2
    xs = np.arange(ks, dtype=np.float32) - half
    kernel = np.exp(-0.5 * (xs / sigma) ** 2)
    kernel /= kernel.sum()
    smoothed = np.convolve(col_means, kernel, mode='same')

    # The darkest column is the nozzle axis (offset back by excluded border)
    darkest_local = int(np.argmin(smoothed))
    return float(darkest_local + border)


def ema(values, alpha=0.1):
    out = []
    for i, v in enumerate(values):
        out.append(v if i == 0 else alpha * v + (1 - alpha) * out[-1])
    return out


# ──────────────────────────────────────────────────────────────────────────────
# QImage helper
# ──────────────────────────────────────────────────────────────────────────────

def np_gray_to_qimage(gray: np.ndarray) -> QImage:
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    h, w = gray.shape
    c = np.ascontiguousarray(gray)
    return QImage(c.data, w, h, w, QImage.Format.Format_Grayscale8).copy()


# ──────────────────────────────────────────────────────────────────────────────
# Resizable square ROI (unchanged from skeleton)
# ──────────────────────────────────────────────────────────────────────────────

class ResizableSquareItem(QGraphicsItem):
    HANDLE_SIZE = 10.0

    def __init__(self, rect: QRectF, scene_rect: QRectF):
        super().__init__()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self._rect       = rect
        self._scene_rect = scene_rect
        self._drag_mode  = None
        self._press_pos  = QPointF()
        self._press_rect = QRectF()
        self._fixed_cx: Optional[float] = None   # when set, X-centre is locked
        self._pen          = QPen(QColor(ACCENT), 2)
        self._handle_brush = QBrush(QColor(ACCENT))
        self._fill_brush   = QBrush(QColor(0, 212, 170, 30))
        self._emit_changed = lambda: None
        self.setAcceptHoverEvents(True)

    def boundingRect(self):
        pad = self.HANDLE_SIZE + 2
        r = QRectF(self._rect)
        r.adjust(-pad, -pad, pad, pad)
        return r

    def rect(self):
        return QRectF(self._rect)

    def set_rect(self, rect: QRectF):
        """Programmatically reposition/resize the crop square (from spinbox edits)."""
        self._rect = self._constrain(rect, min_size=40.0)
        self.prepareGeometryChange()
        self.update()

    def setSceneRectConstraint(self, sr):
        self._scene_rect = QRectF(sr)
        self._rect = self._constrain(self._rect)
        self.prepareGeometryChange()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen)
        painter.setBrush(self._fill_brush)
        painter.drawRect(self._rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._handle_brush)
        for hr in self._handles():
            painter.drawRect(hr)

    def _handles(self):
        hs = self.HANDLE_SIZE
        r = self._rect
        return [
            QRectF(r.left()-hs/2,  r.top()-hs/2,  hs, hs),
            QRectF(r.right()-hs/2, r.top()-hs/2,  hs, hs),
            QRectF(r.left()-hs/2,  r.bottom()-hs/2, hs, hs),
            QRectF(r.right()-hs/2, r.bottom()-hs/2, hs, hs),
        ]

    def _hit_handle(self, pos):
        names = ["resize_tl","resize_tr","resize_bl","resize_br"]
        for h, n in zip(self._handles(), names):
            if h.contains(pos):
                return n
        return None

    def hoverMoveEvent(self, event):
        m = self._hit_handle(event.pos())
        if m in ("resize_tl","resize_br"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif m in ("resize_tr","resize_bl"):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif self._rect.contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self._press_pos  = event.pos()
        self._press_rect = QRectF(self._rect)
        h = self._hit_handle(event.pos())
        self._drag_mode = h if h else ("move" if self._rect.contains(event.pos()) else None)
        event.accept()

    def _apply_fixed_cx(self, r: QRectF) -> QRectF:
        """Re-centre rect at _fixed_cx if the X axis is locked."""
        if self._fixed_cx is not None:
            r = QRectF(r)
            r.moveLeft(self._fixed_cx - r.width() / 2)
        return r

    def mouseMoveEvent(self, event):
        if not self._drag_mode:
            return super().mouseMoveEvent(event)
        delta = event.pos() - self._press_pos
        r0 = self._press_rect
        if self._drag_mode == "move":
            r = QRectF(r0); r.translate(delta)
            self._rect = self._apply_fixed_cx(self._constrain(r))
        else:
            corners = {
                "resize_tl": (QPointF(r0.right(), r0.bottom()), QPointF(r0.left()+delta.x(), r0.top()+delta.y())),
                "resize_tr": (QPointF(r0.left(),  r0.bottom()), QPointF(r0.right()+delta.x(), r0.top()+delta.y())),
                "resize_bl": (QPointF(r0.right(), r0.top()),    QPointF(r0.left()+delta.x(), r0.bottom()+delta.y())),
                "resize_br": (QPointF(r0.left(),  r0.top()),    QPointF(r0.right()+delta.x(), r0.bottom()+delta.y())),
            }
            anchor, corner = corners[self._drag_mode]
            self._rect = self._apply_fixed_cx(self._constrain(self._sq(anchor, corner), min_size=40))
        self.prepareGeometryChange()
        self.update()
        self._emit_changed()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
        event.accept()

    def _sq(self, anchor, corner):
        dx, dy = corner.x()-anchor.x(), corner.y()-anchor.y()
        side = max(abs(dx), abs(dy))
        sx = side if dx >= 0 else -side
        sy = side if dy >= 0 else -side
        x1, y1 = anchor.x(), anchor.y()
        x2, y2 = x1+sx, y1+sy
        return QRectF(QPointF(min(x1,x2),min(y1,y2)), QPointF(max(x1,x2),max(y1,y2)))

    def _constrain(self, rect, min_size=10.0):
        r = QRectF(rect)
        side = max(max(r.width(), r.height()), min_size)
        r.setWidth(side); r.setHeight(side)
        s = self._scene_rect
        if r.left() < s.left():   r.moveLeft(s.left())
        if r.top()  < s.top():    r.moveTop(s.top())
        if r.right() > s.right(): r.moveRight(s.right())
        if r.bottom()> s.bottom():r.moveBottom(s.bottom())
        r.setWidth(min(r.width(), s.width()))
        r.setHeight(min(r.height(), s.height()))
        return r


# ──────────────────────────────────────────────────────────────────────────────
# Inner sample ROI — vertically resizable rectangle, locked to crop square width
# ──────────────────────────────────────────────────────────────────────────────

class SampleROIItem(QGraphicsItem):
    """
    A semi-transparent blue rectangle that sits inside the crop square.
    - Drag the body to move it vertically (X is ignored / locked to parent rect width)
    - Drag the top or bottom edge to resize height
    - Always constrained within the parent crop rect
    _emit_changed() is monkey-patched from the outside (same pattern as ResizableSquareItem)
    """
    HANDLE_H = 8.0

    def __init__(self, rect: QRectF, crop_rect: QRectF):
        super().__init__()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self._crop   = QRectF(crop_rect)
        self._rect   = self._locked_x(rect)
        self._drag   = None       # None / "move" / "top" / "bottom"
        self._press_pos  = QPointF()
        self._press_rect = QRectF()

        self._pen          = QPen(QColor(ACCENT2), 1.5)
        self._fill         = QBrush(QColor(0, 153, 255, 35))
        self._handle_brush = QBrush(QColor(ACCENT2))
        self._emit_changed = lambda: None
        self.setAcceptHoverEvents(True)

    def set_crop_rect(self, crop_rect: QRectF):
        self._crop = QRectF(crop_rect)
        self._rect = self._constrain(self._locked_x(self._rect))
        self.prepareGeometryChange()

    def set_rect_from_params(self, top_rel: int, height: int):
        """Programmatically reposition from sample_top / sample_height spinbox values."""
        new_top = self._crop.top() + top_rel
        new_r   = QRectF(self._crop.left(), new_top, self._crop.width(), height)
        self._rect = self._constrain(self._locked_x(new_r))
        self.prepareGeometryChange()
        self.update()

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def _locked_x(self, r: QRectF) -> QRectF:
        """Force X/width to match crop rect exactly."""
        return QRectF(self._crop.left(), r.top(), self._crop.width(), r.height())

    def _constrain(self, r: QRectF, min_h: float = 10.0) -> QRectF:
        h = max(r.height(), min_h)
        top = max(r.top(), self._crop.top())
        top = min(top, self._crop.bottom() - h)
        return QRectF(self._crop.left(), top, self._crop.width(), h)

    def _top_handle(self) -> QRectF:
        r = self._rect
        return QRectF(r.left(), r.top() - self.HANDLE_H / 2,
                      r.width(), self.HANDLE_H)

    def _bot_handle(self) -> QRectF:
        r = self._rect
        return QRectF(r.left(), r.bottom() - self.HANDLE_H / 2,
                      r.width(), self.HANDLE_H)

    def boundingRect(self) -> QRectF:
        pad = self.HANDLE_H + 2
        r = QRectF(self._rect)
        r.adjust(0, -pad, 0, pad)
        return r

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._pen)
        painter.setBrush(self._fill)
        painter.drawRect(self._rect)

        # top / bottom edge handles as full-width bars
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._handle_brush)
        h = self.HANDLE_H / 2
        r = self._rect
        painter.drawRect(QRectF(r.left(), r.top() - h/2,    r.width(), h))
        painter.drawRect(QRectF(r.left(), r.bottom() - h/2, r.width(), h))

        # label
        painter.setPen(QPen(QColor(ACCENT2)))
        painter.setFont(QFont("Courier New", 8))
        painter.drawText(
            QRectF(r.left() + 4, r.top() + 2, r.width(), 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            "sample ROI"
        )

    def hoverMoveEvent(self, event):
        p = event.pos()
        if self._top_handle().contains(p) or self._bot_handle().contains(p):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._rect.contains(p):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        p = event.pos()
        self._press_pos  = p
        self._press_rect = QRectF(self._rect)
        if self._top_handle().contains(p):
            self._drag = "top"
        elif self._bot_handle().contains(p):
            self._drag = "bottom"
        elif self._rect.contains(p):
            self._drag = "move"
        else:
            self._drag = None
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drag:
            return super().mouseMoveEvent(event)
        dy   = event.pos().y() - self._press_pos.y()
        r0   = self._press_rect

        if self._drag == "move":
            new_r = QRectF(r0.left(), r0.top() + dy, r0.width(), r0.height())
        elif self._drag == "top":
            new_top = r0.top() + dy
            new_h   = r0.height() - dy
            new_r   = QRectF(r0.left(), new_top, r0.width(), max(new_h, 10))
        else:  # bottom
            new_h = max(r0.height() + dy, 10)
            new_r = QRectF(r0.left(), r0.top(), r0.width(), new_h)

        self._rect = self._constrain(self._locked_x(new_r))
        self.prepareGeometryChange()
        self.update()
        self._emit_changed()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None
        event.accept()


# ──────────────────────────────────────────────────────────────────────────────
# Preview widget
# ──────────────────────────────────────────────────────────────────────────────

class PreviewWithROI(QWidget):
    roiChanged    = pyqtSignal(int, int, int)        # crop: cx, top, extent (image px)
    sampleChanged = pyqtSignal(int, int)             # sample: top_px, height_px (within crop)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view  = QGraphicsView(self.scene, self)
        self.view.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.view.setBackgroundBrush(QBrush(QColor(10, 12, 15)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.view)

        self.pix_item:    Optional[QGraphicsPixmapItem] = None
        self.roi_item:    Optional[ResizableSquareItem] = None
        self.sample_item: Optional[SampleROIItem]       = None
        self.axis_line:   Optional[QGraphicsLineItem]   = None
        self._img_w = self._img_h = 0

        # Default ROI seeded before first frame arrives
        self._default_cx:         Optional[int] = None
        self._default_top:        Optional[int] = None
        self._default_extent:     Optional[int] = None
        self._default_sample_top: Optional[int] = None
        self._default_sample_h:   Optional[int] = None

    def set_default_roi(self, cx: int, top: int, extent: int,
                        sample_top: int, sample_h: int):
        """Call before the first frame to place overlays at the desired defaults."""
        self._default_cx         = cx
        self._default_top        = top
        self._default_extent     = extent
        self._default_sample_top = sample_top
        self._default_sample_h   = sample_h

        self._crop_debounce   = QTimer(self); self._crop_debounce.setSingleShot(True)
        self._crop_debounce.timeout.connect(self._emit_roi)
        self._sample_debounce = QTimer(self); self._sample_debounce.setSingleShot(True)
        self._sample_debounce.timeout.connect(self._emit_sample)

    def set_frame(self, gray: np.ndarray):
        qimg = np_gray_to_qimage(gray)
        pix  = QPixmap.fromImage(qimg)
        self._img_h, self._img_w = gray.shape

        if self.pix_item is None:
            self.pix_item = self.scene.addPixmap(pix)
            self.pix_item.setZValue(0)
        else:
            self.pix_item.setPixmap(pix)

        self.scene.setSceneRect(QRectF(0, 0, self._img_w, self._img_h))

        if self.roi_item is None:
            # Crop square — use seeded defaults if available, else 55% centred
            if self._default_cx is not None:
                size = float(self._default_extent)
                x    = self._default_cx - size / 2
                y    = float(self._default_top)
            else:
                size = min(self._img_w, self._img_h) * 0.55
                x    = (self._img_w - size) / 2
                y    = (self._img_h - size) / 2
            self.roi_item = ResizableSquareItem(QRectF(x, y, size, size),
                                                self.scene.sceneRect())
            self.roi_item._fixed_cx = x + size / 2   # lock to axis of rotation
            self.roi_item.setZValue(10)
            self.scene.addItem(self.roi_item)
            self.roi_item._emit_changed = lambda: self._crop_debounce.start(120)

            # Sample ROI — use seeded defaults if available, else middle 60%
            crop_r = self.roi_item.rect()
            if self._default_sample_top is not None:
                s_top = crop_r.top() + self._default_sample_top
                s_h   = float(self._default_sample_h)
            else:
                s_top = crop_r.top() + crop_r.height() * 0.20
                s_h   = crop_r.height() * 0.60
            self.sample_item = SampleROIItem(
                QRectF(crop_r.left(), s_top, crop_r.width(), s_h),
                crop_r)
            self.sample_item.setZValue(20)
            self.scene.addItem(self.sample_item)
            self.sample_item._emit_changed = lambda: self._sample_debounce.start(120)
            # Fire once to sync spinboxes with the initial ROI positions
            self._crop_debounce.start(120)
            self._sample_debounce.start(120)
        else:
            # Only re-constrain when the image dimensions actually change; calling
            # setSceneRectConstraint on every frame would shift the ROI away from
            # the desired default position on smaller images (e.g. the simulator).
            new_sr = self.scene.sceneRect()
            if new_sr != self.roi_item._scene_rect:
                self.roi_item.setSceneRectConstraint(new_sr)
                self._crop_debounce.start(120)
                self._sample_debounce.start(120)
            # Always keep sample ROI locked to current crop rect
            self.sample_item.set_crop_rect(self.roi_item.rect())

        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _update_axis_line(self, cx: float):
        """Draw/move the dotted vertical axis-of-rotation line at x=cx."""
        if self._img_h == 0:
            return
        if self.axis_line is None:
            pen = QPen(QColor("#ffdd00"), 1.5)
            pen.setStyle(Qt.PenStyle.DotLine)
            self.axis_line = self.scene.addLine(cx, 0, cx, self._img_h, pen)
            self.axis_line.setZValue(5)
        else:
            self.axis_line.setLine(cx, 0, cx, self._img_h)

    def set_crop_overlay(self, cx: int, top: int, extent: int):
        """Called from spinbox edits — update the green square without re-emitting."""
        if self.roi_item is None or self._img_w == 0:
            return
        x = cx - extent / 2
        new_r = QRectF(x, top, extent, extent)
        self.roi_item._fixed_cx = float(cx)
        self.roi_item.set_rect(new_r)
        # Read the actual centre after _constrain may have adjusted the rect
        actual_cx = self.roi_item.rect().center().x()
        self.roi_item._fixed_cx = actual_cx
        if self.sample_item:
            self.sample_item.set_crop_rect(self.roi_item.rect())
        self._update_axis_line(actual_cx)
        self.scene.update()

    def set_sample_overlay(self, top_rel: int, height: int):
        """Called from spinbox edits — update the blue rectangle without re-emitting."""
        if self.sample_item is None:
            return
        self.sample_item.set_rect_from_params(top_rel, height)
        self.scene.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.scene.sceneRect().isValid():
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _emit_roi(self):
        if self.roi_item is None or self._img_w == 0:
            return
        # roi_item.rect() is already in scene coords = raw image pixels,
        # because scene rect is set to (0, 0, img_w, img_h).
        # The view scaling is purely cosmetic and doesn't affect scene coords.
        r    = self.roi_item.rect()
        cx   = int(round(r.center().x()))
        top  = int(round(r.top()))
        size = int(round(r.width()))   # square, so width == height == extent
        # Keep sample ROI locked to new crop bounds
        if self.sample_item:
            self.sample_item.set_crop_rect(r)
            self._sample_debounce.start(120)
        self._update_axis_line(cx)
        self.roiChanged.emit(cx, top, size)

    def _emit_sample(self):
        if self.sample_item is None or self.roi_item is None:
            return
        crop_r  = self.roi_item.rect()
        samp_r  = self.sample_item.rect()
        # sample_top is relative to the top of the crop window (in image pixels)
        top_rel = int(round(samp_r.top()  - crop_r.top()))
        height  = int(round(samp_r.height()))
        self.sampleChanged.emit(top_rel, height)


# ──────────────────────────────────────────────────────────────────────────────
# Dose/depth plot widget
# ──────────────────────────────────────────────────────────────────────────────

class DoseDepthPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(5, 3), facecolor="#0d0f12")
        self.ax  = self.fig.add_subplot(111, facecolor="#13161b")
        self.ax.tick_params(colors="#5a6070", labelsize=9)
        for sp in self.ax.spines.values():
            sp.set_color("#252932")
        self.ax.set_xlabel("Depth (mm)", color="#5a6070", fontsize=9)
        self.ax.set_ylabel("Relative Dose", color="#5a6070", fontsize=9)
        self.ax.grid(True, alpha=0.15, color="#252932")
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setStyleSheet("background-color: #0d0f12;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.canvas)
        # seed with flat line
        self.set_data(np.linspace(0, 60, 121), np.zeros(121))

    def set_data(self, depth_mm, rel_dose, title="Depth Dose"):
        self.ax.clear()
        self.ax.set_facecolor("#13161b")
        self.ax.tick_params(colors="#5a6070", labelsize=9)
        for sp in self.ax.spines.values():
            sp.set_color("#252932")
        self.ax.set_xlabel("Depth (mm)", color="#5a6070", fontsize=9)
        self.ax.set_ylabel("Relative Dose", color="#5a6070", fontsize=9)
        self.ax.grid(True, alpha=0.15, color="#252932")
        self.ax.set_title(title, color=ACCENT, fontsize=9, pad=4)
        self.ax.plot(depth_mm, rel_dose, color=ACCENT, linewidth=1.5)
        self.fig.tight_layout(pad=1.2)
        self.canvas.draw_idle()


# ──────────────────────────────────────────────────────────────────────────────
# Phase button bar — user clicks each phase button to proceed
# ──────────────────────────────────────────────────────────────────────────────

class PhaseButtonBar(QWidget):
    """
    Checklist showing all four scan phases, with a single PROCEED button
    for the active phase and an instruction line explaining what to do.

    External interface is unchanged: set_phase_state / ready_phase / reset
    and the phase_requested signal.
    """
    phase_requested = pyqtSignal(int)   # 0=dark, 1=flat, 2=pre, 3=post

    LABELS = [
        "① Capture dark",
        "② Capture flat",
        "③ Pre-irradiation scan",
        "④ Post-irradiation scan",
    ]
    HINTS = [
        "Turn lamp OFF and remove sample",
        "Turn lamp ON with no sample",
        "Place sample, lamp ON — begin pre-irradiation rotation",
        "Place irradiated sample, lamp ON — begin post-irradiation rotation",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        # Read-only checklist rows
        self._step_labels = []
        for label in self.LABELS:
            lbl = QLabel(f"○  {label}")
            lbl.setObjectName("dim")
            self._step_labels.append(lbl)
            layout.addWidget(lbl)

        # Instruction text for the current phase
        self._instruction_lbl = QLabel("")
        self._instruction_lbl.setStyleSheet(f"color:{TEXT_MAIN};")
        self._instruction_lbl.setWordWrap(True)
        layout.addWidget(self._instruction_lbl)

        # Single proceed button shared across all phases
        self._proceed_btn = QPushButton("▶  PROCEED")
        self._proceed_btn.setMinimumHeight(36)
        self._proceed_btn.setEnabled(False)
        self._current_idx = -1
        self._proceed_btn.clicked.connect(
            lambda: self.phase_requested.emit(self._current_idx)
        )
        layout.addWidget(self._proceed_btn)

    def set_phase_state(self, idx: int, state: int):
        """state: 0=locked, 1=ready-to-click, 2=running, 3=done, 4=skipped"""
        if not (0 <= idx < len(self._step_labels)):
            return
        lbl = self._step_labels[idx]
        if state == 3:                          # done
            lbl.setText(f"✓  {self.LABELS[idx]}")
            lbl.setStyleSheet(f"color:{TEXT_DIM};")
        elif state == 4:                        # skipped (post-only mode)
            lbl.setText(f"—  {self.LABELS[idx]}")
            lbl.setStyleSheet(f"color:{TEXT_DIM}; font-style:italic;")
        elif state == 1:                        # ready — waiting for user
            lbl.setText(f"▶  {self.LABELS[idx]}")
            lbl.setStyleSheet(f"color:{ACCENT}; font-weight:bold;")
            self._instruction_lbl.setText(self.HINTS[idx])
            self._proceed_btn.setEnabled(True)
            self._proceed_btn.setText("▶  PROCEED")
            self._current_idx = idx
        elif state == 2:                        # running
            lbl.setText(f"⏳  {self.LABELS[idx]}…")
            lbl.setStyleSheet(f"color:{ACCENT};")
            self._proceed_btn.setEnabled(False)
            self._proceed_btn.setText(f"⏳  {self.LABELS[idx]}…")
        else:                                   # locked / not yet reached
            lbl.setText(f"○  {self.LABELS[idx]}")
            lbl.setStyleSheet(f"color:{TEXT_DIM};")

    def reset(self):
        for i in range(len(self._step_labels)):
            self.set_phase_state(i, 0)
        self._instruction_lbl.setText("")
        self._proceed_btn.setEnabled(False)
        self._proceed_btn.setText("▶  PROCEED")
        self._current_idx = -1

    def ready_phase(self, idx: int):
        """Mark phase idx as ready-to-click; earlier phases done, later locked."""
        for i in range(len(self._step_labels)):
            if i < idx:
                self.set_phase_state(i, 3)
            elif i == idx:
                self.set_phase_state(i, 1)
            else:
                self.set_phase_state(i, 0)


# ──────────────────────────────────────────────────────────────────────────────
# Scan worker — runs in QThread
# ──────────────────────────────────────────────────────────────────────────────

class ScanWorker(QObject):
    """
    Drives the full scan sequence with explicit user-gated phases.

    Flow:
      1. Worker emits phase_ready(0)  → UI enables "Capture dark" button
      2. User clicks it               → UI calls worker.proceed()
      3. Worker captures dark, emits phase_done(0)
      4. Worker emits phase_ready(1)  → UI enables "Capture flat" button
      … and so on.

    Signals:
      phase_ready(idx)      — phase idx is waiting for user click
      phase_running(idx)    — phase idx is now executing
      phase_done(idx)       — phase idx completed successfully
      scan_progress(pct)    — 0..100 during the rotation phase
      image_ready(ndarray)  — latest captured frame
      log(str)
      finished(ok, message)
    """
    phase_ready    = pyqtSignal(int)
    phase_running  = pyqtSignal(int)
    phase_done     = pyqtSignal(int)
    phase_skipped  = pyqtSignal(int)
    scan_progress  = pyqtSignal(int)
    image_ready    = pyqtSignal(object)
    log            = pyqtSignal(str)
    finished       = pyqtSignal(bool, str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg    = cfg
        self._abort = False
        # Threading primitive: worker blocks here until user clicks
        import threading
        self._gate  = threading.Event()

    def proceed(self):
        """Called from the UI thread when user clicks the phase button."""
        self._gate.set()

    def abort(self):
        self._abort = True
        self._gate.set()   # unblock any waiting gate

    def _wait_for_user(self, phase_idx: int) -> bool:
        """Emit phase_ready, then block until proceed() or abort()."""
        self._gate.clear()
        self.phase_ready.emit(phase_idx)
        self._gate.wait()          # blocks worker thread only
        return not self._abort

    def run(self):
        cfg            = self.cfg
        pre_dir        = Path(cfg["pre_dir"])
        post_dir       = Path(cfg["post_dir"])
        subtracted_dir = Path(cfg["subtracted_dir"])
        dark_path      = str(CONFIG_DIR / "dark.npy")
        flat_path      = str(CONFIG_DIR / "flat.npy")

        degree_increment = cfg["degree_increment"]
        oct_stack        = cfg["oct_stack"]
        dark_stack       = cfg["dark_stack"]
        flat_stack       = cfg["flat_stack"]
        force_dark       = cfg["force_dark"]
        force_flat       = cfg["force_flat"]
        scan_mode        = cfg["scan_mode"]   # "pre" or "post"
        use_real_camera  = cfg["use_real_camera"]
        use_real_serial  = cfg["use_real_serial"]

        sim = CameraSimulator()

        def capture(stack):
            if use_real_camera:
                img = take_photo_http(stack)
                if img is None:
                    self.log.emit("✗ Camera unreachable — image capture failed")
                    return None
            else:
                img = sim.get_frame(stack)
            return img

        # Load undistortion maps
        map1 = map2 = None
        if CALIBRATION_JSON.exists():
            try:
                with open(CALIBRATION_JSON) as f:
                    calib = json.load(f)
                K    = np.array(calib["K"], dtype=np.float64)
                dist = np.array(calib["dist"], dtype=np.float64)
                w    = calib["image_size"]["width"]
                h    = calib["image_size"]["height"]
                map1, map2 = cv2.initUndistortRectifyMap(
                    K, dist, None, K, (w, h), cv2.CV_32FC1)
                self.log.emit("✓ Camera calibration loaded")
            except Exception as e:
                self.log.emit(f"⚠ Calibration load failed: {e}")
        else:
            self.log.emit("⚠ No calibration file — images will not be undistorted")

        try:
            # ── Phase 0: Dark ─────────────────────────────────────────────────
            if force_dark or not os.path.exists(dark_path):
                self.log.emit("Waiting: turn lamp OFF and remove sample, then click ① Capture dark")
                if not self._wait_for_user(0):
                    self.finished.emit(False, "Aborted"); return
                self.phase_running.emit(0)
                lamp_off()
                self.log.emit("Capturing dark frame (lamp OFF)…")
            else:
                self.phase_running.emit(0)
                self.log.emit("Loading existing dark frame…")

            dark = get_or_create_calibration_scan(
                dark_path, capture, dark_stack, force_dark, "dark")
            if dark is None:
                self.finished.emit(False, "Dark frame capture failed"); return
            self.phase_done.emit(0)
            if self._abort:
                self.finished.emit(False, "Aborted"); return

            # ── Phase 1: Flat ─────────────────────────────────────────────────
            if force_flat or not os.path.exists(flat_path):
                self.log.emit("Waiting: turn lamp ON with no sample, then click ② Capture flat")
                if not self._wait_for_user(1):
                    self.finished.emit(False, "Aborted"); return
                self.phase_running.emit(1)
                lamp_on()
                self.log.emit("Capturing flat frame (lamp ON, no sample)…")
            else:
                self.phase_running.emit(1)
                self.log.emit("Loading existing flat frame…")

            flat = get_or_create_calibration_scan(
                flat_path, capture, flat_stack, force_flat, "flat")
            if flat is None:
                self.finished.emit(False, "Flat frame capture failed"); return
            self.phase_done.emit(1)
            if self._abort:
                self.finished.emit(False, "Aborted"); return

            num_positions = int(360 / degree_increment)

            # ── Phase 2: Pre-irradiation scan ────────────────────────────────
            if scan_mode == "post":
                # Pre images already exist from an earlier session — skip this phase
                self.phase_skipped.emit(2)
                self.log.emit("Post session — using saved pre-irradiation images")
            else:
                # scan_mode == "pre"
                self.log.emit("Waiting: place sample, lamp ON — click ③ to begin pre-irradiation scan")
                if not self._wait_for_user(2):
                    self.finished.emit(False, "Aborted"); return

                self.phase_running.emit(2)
                self.log.emit("Pre-irradiation scan…")
                lamp_on()
                ok = self._run_rotation(num_positions, degree_increment, oct_stack,
                                        pre_dir, map1, map2, use_real_serial,
                                        progress_offset=0, progress_scale=1.0)
                if not ok:
                    return
                self.phase_done.emit(2)
                lamp_off()
                if self._abort:
                    self.finished.emit(False, "Scan aborted by user"); return
                # Save acquisition parameters so the post-scan can validate them
                meta = {"step_deg": degree_increment, "num_positions": num_positions}
                (pre_dir.parent / "scan_meta.json").write_text(json.dumps(meta, indent=2))
                self.log.emit("✓ Pre-irradiation scan complete — irradiate the dosimeter, "
                              "then return for the post-irradiation session")
                self.finished.emit(True, "Pre-scan complete — ready for irradiation")
                return

            # ── Phase 3: Post-irradiation scan ────────────────────────────────
            self.log.emit("Waiting: place irradiated sample, lamp ON — then click ④")
            if not self._wait_for_user(3):
                self.finished.emit(False, "Aborted"); return

            self.phase_running.emit(3)
            self.log.emit("Post-irradiation scan…")
            lamp_on()
            ok = self._run_rotation(num_positions, degree_increment, oct_stack,
                                    post_dir, map1, map2, use_real_serial,
                                    progress_offset=0, progress_scale=0.8)
            if not ok:
                return
            self.phase_done.emit(3)
            lamp_off()

            if self._abort:
                self.finished.emit(False, "Scan aborted by user"); return

            # ── Subtraction in OD domain: ΔA = A_post − A_pre ───────────────
            self.log.emit("Computing ΔA = A_post − A_pre projections (OD domain)…")
            self.scan_progress.emit(0)
            self._compute_subtracted(pre_dir, post_dir, subtracted_dir, flat,
                                     progress_cb=self.scan_progress.emit)
            self.log.emit(f"✓ ΔA projections saved to {subtracted_dir}")

            self.finished.emit(True, "Post-scan and subtraction complete")

        except Exception as e:
            lamp_off()
            self.finished.emit(False, f"Scan error: {e}\n{traceback.format_exc()}")

    def _save_image(self, img, i, angle, image_dir, map1, map2):
        if map1 is not None:
            img = cv2.remap(img, map1, map2,
                            interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT)
        fname = image_dir / f"img_{i:04d}_{int(angle):03d}_deg.png"
        cv2.imwrite(str(fname), img)

    def _run_rotation(self, num_positions, degree_increment, oct_stack,
                      image_dir, map1, map2, use_real_serial,
                      progress_offset=0.0, progress_scale=1.0):
        """Execute one full rotation scan, saving images to image_dir.
        Returns True on success, False (and emits finished) on error/abort.
        progress_offset and progress_scale map [0,100] → overall scan_progress range.
        """
        sim = CameraSimulator()

        def capture(stack):
            if self.cfg["use_real_camera"]:
                img = take_photo_http(stack)
                if img is None:
                    self.log.emit("✗ Camera unreachable — image capture failed")
                    return None
            else:
                img = sim.get_frame(stack)
            return img

        if use_real_serial:
            import serial
            try:
                with serial.Serial(SERIAL_PORT, BAUDRATE, timeout=SERIAL_TIMEOUT) as ser:
                    time.sleep(0.5)
                    for cmd in [f"EE=0", f"DN={DEVICE_NAME}", f"MS={MICROSTEPS}",
                                f"VI={INITIAL_VELOCITY}", f"VM={MAX_VELOCITY}",
                                f"A={ACCELERATION}", f"D={DECELERATION}",
                                f"RC={RUN_CURRENT}", "P=0"]:
                        serial_send(ser, cmd)

                    for i in range(num_positions):
                        if self._abort:
                            serial_send(ser, "SL 0")
                            break
                        angle        = i * degree_increment
                        target_steps = round((angle / 360.0) * STEPS_PER_REV)
                        self.log.emit(f"  Step {i+1}/{num_positions} — {angle}°")
                        serial_send(ser, f"MA {target_steps},1")
                        serial_wait_stopped(ser, dn_char=DEVICE_NAME)
                        time.sleep(self.cfg["settle_ms"] / 1000.0)
                        img = capture(oct_stack)
                        if img is None:
                            self.finished.emit(False, f"Camera failed at step {i}")
                            return False
                        self._save_image(img, i, angle, image_dir, map1, map2)
                        self.image_ready.emit(img.copy())
                        pct = int((progress_offset + progress_scale * (i+1) / num_positions) * 100)
                        self.scan_progress.emit(pct)

                    serial_send(ser, "MA 0")
                    serial_wait_stopped(ser, dn_char=DEVICE_NAME)
            except Exception as e:
                self.finished.emit(False, f"Serial error: {e}\n{traceback.format_exc()}")
                return False
        else:
            for i in range(num_positions):
                if self._abort:
                    break
                angle = i * degree_increment
                time.sleep(0.05)
                img = capture(oct_stack)
                if img is None:
                    self.finished.emit(False, f"Camera failed at step {i+1}/{num_positions}")
                    return False
                self._save_image(img, i, angle, image_dir, map1, map2)
                self.image_ready.emit(img.copy())
                pct = int((progress_offset + progress_scale * (i+1) / num_positions) * 100)
                self.scan_progress.emit(pct)

        return True

    def _compute_subtracted(self, pre_dir: Path, post_dir: Path, subtracted_dir: Path,
                             flat: np.ndarray, progress_cb=None):
        """
        Compute ΔA = A_post − A_pre in the OD domain and save as uint16 PNG.

        A = −log(I / I₀) where I₀ is the flat-field image.
        ΔA is clipped to [0, ∞) and encoded with OD_SCALE.

        Note: the flat field cancels in the subtraction (ΔA = log(I_pre/I_post)),
        so the result is independent of flat-field drift between sessions.
        """
        flat_f    = np.clip(flat.astype(np.float32), 1e-6, None)
        pre_files = sorted(pre_dir.glob("*.png"))
        post_files = sorted(post_dir.glob("*.png"))
        if len(pre_files) != len(post_files):
            self.log.emit(f"⚠ pre/post image count mismatch "
                          f"({len(pre_files)} vs {len(post_files)}) — "
                          f"using first {min(len(pre_files), len(post_files))} pairs")
        n = min(len(pre_files), len(post_files))
        for i, (pf, qf) in enumerate(zip(pre_files[:n], post_files[:n])):
            pre_img  = cv2.imread(str(pf),  cv2.IMREAD_GRAYSCALE).astype(np.float32)
            post_img = cv2.imread(str(qf), cv2.IMREAD_GRAYSCALE).astype(np.float32)
            A_pre  = -np.log(np.clip(pre_img,  1e-6, None) / flat_f)
            A_post = -np.log(np.clip(post_img, 1e-6, None) / flat_f)
            delta_A = np.clip(A_post - A_pre, 0.0, None)
            out = np.clip(delta_A * OD_SCALE, 0, 65535).astype(np.uint16)
            cv2.imwrite(str(subtracted_dir / pf.name), out)
            if progress_cb and n:
                progress_cb(int((i + 1) / n * 100))


# ──────────────────────────────────────────────────────────────────────────────
# Reconstruction worker
# ──────────────────────────────────────────────────────────────────────────────

class ReconWorker(QObject):
    progress    = pyqtSignal(int)
    log         = pyqtSignal(str)
    dose_ready  = pyqtSignal(object, object)   # depth_mm, rel_dose arrays
    finished    = pyqtSignal(bool, str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        cfg = self.cfg
        try:
            image_dir      = cfg["image_dir"]
            reconstruct_dir = cfg["reconstruct_dir"]
            dose_dir       = cfg["dose_dir"]
            depth_dose_dir = cfg["depth_dose_dir"]
            degree_increment = cfg["degree_increment"]
            crop_cx        = cfg["crop_cx"]
            crop_top       = cfg["crop_top"]
            crop_extent    = cfg["crop_extent"]
            sample_top     = cfg["sample_top"]
            sample_height  = cfg["sample_height"]
            force_new_vol  = cfg["force_new_vol"]

            for d in (reconstruct_dir, dose_dir, depth_dose_dir):
                Path(d).mkdir(parents=True, exist_ok=True)

            dark_path = str(CONFIG_DIR / "dark.npy")
            flat_path = str(CONFIG_DIR / "flat.npy")

            self.log.emit("Loading ΔA projections...")
            self.progress.emit(5)
            imgs = load_png_stack(image_dir)
            dark = np.load(dark_path).astype(np.float32)
            flat = np.load(flat_path).astype(np.float32)

            self.log.emit(f"Cropping: cx={crop_cx}, top={crop_top}, extent={crop_extent}")
            imgs, dark, flat = crop_window(imgs, dark, flat, crop_cx, crop_top, crop_extent)
            self.progress.emit(10)

            # Save crop preview
            ex = imgs[0].copy()
            ex = cv2.normalize(ex, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            cv2.imwrite(str(Path(reconstruct_dir) / "example_cropped.png"), ex)

            # Images are pre-computed ΔA projections encoded as uint16 — decode directly.
            # (line_integrals is not applied; the subtraction pipeline already produced
            #  ΔA = A_post − A_pre = −log(I_post/I₀) − (−log(I_pre/I₀)))
            P = imgs / OD_SCALE
            angles_deg = np.arange(P.shape[0], dtype=np.float32) * float(degree_increment)
            self.progress.emit(20)

            vol_path = str(Path(reconstruct_dir) / "attenuation_volume.npy")
            if os.path.exists(vol_path) and not force_new_vol:
                self.log.emit("Loading cached attenuation volume...")
                mu_vol = np.load(vol_path)
                self.progress.emit(70)
            else:
                self.log.emit("Reconstructing volume (FBP)...")

                def recon_progress(pct):
                    self.progress.emit(20 + int(pct * 0.5))
                    self.log.emit(f"  FBP: {pct}%")

                mu_vol = recon_volume_fbp(P, angles_deg, progress_cb=recon_progress)
                np.save(vol_path, mu_vol)
                self.log.emit("Attenuation volume saved.")
                self.progress.emit(70)

            if self._abort:
                self.finished.emit(False, "Aborted"); return

            # Depth dose
            self.log.emit("Computing depth dose...")
            depth_mm, rel_dose, dose_signal, od_depth = depth_dose_from_central_axis(
                mu_vol,
                mm_per_slice_y=MM_PER_SLICE_Y,
                sample_top_px=sample_top,
                sample_height_px=sample_height,
                roi_radius_px=10,
                invert=False,
            )
            smoothed = np.array(ema(rel_dose, alpha=0.1))
            self.dose_ready.emit(depth_mm, smoothed)
            self.progress.emit(85)

            # Save depth dose
            try:
                import pandas as pd
                df = pd.DataFrame({
                    "depth_mm": depth_mm,
                    "rel_dose": rel_dose,
                    "dose_signal": dose_signal,
                    "optical_density_depth": od_depth,
                })
                df.to_excel(str(Path(depth_dose_dir) / "depth_dose.xlsx"), index=False)
                self.log.emit("Depth dose saved to Excel.")
            except Exception as e:
                self.log.emit(f"⚠ Could not save Excel: {e}")

            # Dose profiles per slice
            self.log.emit("Computing per-slice dose profiles...")
            Y = mu_vol.shape[0]
            for y in range(0, Y, max(1, Y // 20)):
                if self._abort:
                    break
                pos_mm, rel, _ = dose_profile_from_volume(
                    mu_vol, MM_PER_PIXEL_XZ, depth_y=y, roi_radius_px=10)
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                fig, ax = plt.subplots(figsize=(6, 3))
                ax.plot(pos_mm, rel)
                ax.set_xlabel("Position (mm)"); ax.set_ylabel("Relative Dose")
                ax.set_title(f"Dose profile depth {y}"); ax.grid(True)
                fig.tight_layout()
                fig.savefig(str(Path(dose_dir) / f"profile_{y:04d}.png"), dpi=150)
                plt.close(fig)
                self.progress.emit(85 + int(15 * y / Y))

            self.progress.emit(100)
            self.finished.emit(True, "Reconstruction complete")

        except Exception as e:
            self.finished.emit(False, f"Reconstruction error: {e}\n{traceback.format_exc()}")


# ──────────────────────────────────────────────────────────────────────────────
# Export worker (unchanged logic from skeleton)
# ──────────────────────────────────────────────────────────────────────────────

class ExportWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, scan_dir: Path, dest_root: Path):
        super().__init__()
        self.scan_dir  = scan_dir
        self.dest_root = dest_root

    def run(self):
        try:
            if not self.scan_dir.exists():
                self.finished.emit(False, "Scan directory does not exist.")
                return
            dest = self.dest_root / self.scan_dir.name
            if dest.exists():
                dest = self.dest_root / f"{self.scan_dir.name}_{int(time.time())}"
            files = [p for p in self.scan_dir.rglob("*") if p.is_file()]
            total = max(1, len(files))
            dest.mkdir(parents=True, exist_ok=True)
            for i, src in enumerate(files):
                rel = src.relative_to(self.scan_dir)
                dst = dest / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                self.progress.emit(int(100 * (i+1) / total))
            self.finished.emit(True, f"Exported to: {dest}")
        except Exception as e:
            self.finished.emit(False, f"Export failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# USB helpers
# ──────────────────────────────────────────────────────────────────────────────

def list_usb_mounts() -> List[Path]:
    mounts = []
    for base in (Path("/media"), Path("/run/media")):
        if base.exists():
            for root, dirs, _ in os.walk(base):
                rp = Path(root)
                if rp != base and rp.is_dir():
                    try:
                        tf = rp / ".oct_write_test"
                        tf.write_text("x"); tf.unlink()
                        mounts.append(rp)
                    except Exception:
                        pass
    for p in (Path("/Volumes"),):
        if p.exists():
            for m in p.iterdir():
                if m.is_dir() and "Macintosh" not in m.name:
                    mounts.append(m)
    return sorted(set(mounts), key=str)

def list_scans() -> List[Path]:
    if not SCANS_DIR.exists():
        return []
    return sorted([p for p in SCANS_DIR.iterdir() if p.is_dir()],
                  key=lambda p: p.name, reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# Export dialog
# ──────────────────────────────────────────────────────────────────────────────

class ExportDialog(QDialog):
    def __init__(self, parent=None, current_scan: Optional[Path] = None):
        super().__init__(parent)
        self.setWindowTitle("Export scan")
        self.setMinimumSize(600, 380)

        self.scan_list    = QListWidget()
        self.refresh_btn  = QPushButton("Refresh")
        self.mount_combo  = QComboBox()
        self.browse_btn   = QPushButton("Browse…")
        self.dest_line    = QLineEdit(); self.dest_line.setReadOnly(True)
        self.export_btn   = QPushButton("Export")
        self.cancel_btn   = QPushButton("Close")
        self.progress     = QProgressBar()
        self.progress.setRange(0, 100)

        root = QVBoxLayout(self)
        sb   = QGroupBox("Select scan")
        sl   = QVBoxLayout(sb)
        sl.addWidget(self.scan_list)
        sl.addWidget(self.refresh_btn)

        db   = QGroupBox("Destination")
        dl   = QFormLayout(db)
        row  = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0,0,0,0)
        rl.addWidget(self.mount_combo, 1); rl.addWidget(self.browse_btn)
        dl.addRow("Drive:", row)
        dl.addRow("Path:",  self.dest_line)

        root.addWidget(sb, 3)
        root.addWidget(db, 1)
        root.addWidget(QLabel("Progress:")); root.addWidget(self.progress)
        btns = QHBoxLayout(); btns.addStretch()
        btns.addWidget(self.export_btn); btns.addWidget(self.cancel_btn)
        root.addLayout(btns)

        self._selected_scan: Optional[Path] = None
        self._selected_dest: Optional[Path] = None
        self._thread = self._worker = None
        self._current_scan = current_scan

        self.refresh_btn.clicked.connect(self._refresh_scans)
        self.browse_btn.clicked.connect(self._browse)
        self.export_btn.clicked.connect(self._start_export)
        self.cancel_btn.clicked.connect(self.close)
        self.scan_list.currentItemChanged.connect(self._scan_sel)
        self.mount_combo.currentIndexChanged.connect(self._mount_sel)

        self._refresh_scans()
        self._refresh_mounts()

    def _refresh_scans(self):
        self.scan_list.clear()
        for d in list_scans():
            item = QListWidgetItem(d.name)
            item.setData(Qt.ItemDataRole.UserRole, str(d))
            self.scan_list.addItem(item)
        if self.scan_list.count():
            # Pre-select the current scan if provided, otherwise default to row 0
            selected_row = 0
            if self._current_scan is not None:
                target = str(self._current_scan)
                for i in range(self.scan_list.count()):
                    if self.scan_list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                        selected_row = i
                        break
            self.scan_list.setCurrentRow(selected_row)

    def _refresh_mounts(self):
        self.mount_combo.blockSignals(True)
        self.mount_combo.clear()
        mounts = list_usb_mounts()
        if not mounts:
            self.mount_combo.addItem("(none detected — use Browse)", "")
        else:
            for p in mounts:
                self.mount_combo.addItem(str(p), str(p))
        self.mount_combo.blockSignals(False)
        self._mount_sel(0)

    def _browse(self):
        p = QFileDialog.getExistingDirectory(
            self, "Select destination", "",
            QFileDialog.Option.DontUseNativeDialog
        )
        if p:
            self._selected_dest = Path(p)
            self.dest_line.setText(str(p))

    def _scan_sel(self, cur, _):
        if cur:
            self._selected_scan = Path(cur.data(Qt.ItemDataRole.UserRole))

    def _mount_sel(self, idx):
        data = self.mount_combo.itemData(idx)
        if data:
            self._selected_dest = Path(data)
            self.dest_line.setText(data)

    def _start_export(self):
        if not self._selected_scan or not self._selected_dest:
            QMessageBox.warning(self, "Export", "Select both a scan and a destination.")
            return
        self.export_btn.setEnabled(False)
        self.progress.setValue(0)
        self._thread = QThread()
        self._worker = ExportWorker(self._selected_scan, self._selected_dest)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _done(self, ok, msg):
        self.export_btn.setEnabled(True)
        (QMessageBox.information if ok else QMessageBox.critical)(self, "Export", msg)


# ──────────────────────────────────────────────────────────────────────────────
# Startup workflow chooser
# ──────────────────────────────────────────────────────────────────────────────

class StartupDialog(QDialog):
    """
    Shown once at launch so the user picks the session workflow.
    chosen_mode is set to one of: "pre", "post", "reconstruct", "view_dose", "export".
    """

    _MODES = [
        ("pre",         "Pre-irradiation scan",
         "Capture dark, flat, and pre-\nirradiation sample images"),
        ("post",        "Post-irradiation scan",
         "Capture dark, flat, and post-\nirradiation images · compute ΔA"),
        ("reconstruct", "Reconstruct",
         "Run FBP reconstruction on a\ncompleted pre+post scan"),
        ("view_dose",   "View depth dose",
         "Load and display the depth dose\nfrom an existing reconstruction"),
        ("export",      "Export scan",
         "Copy a scan to a USB drive\nor local folder"),
    ]

    def __init__(self):
        super().__init__()
        self.chosen_mode: Optional[str] = None
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setSpacing(18)
        root.setContentsMargins(32, 28, 32, 28)

        hdr = QLabel(f"{APP_TITLE}")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setStyleSheet(
            f"font-size:16px; font-weight:bold; color:{ACCENT}; letter-spacing:2px;"
            f" border:none; background:transparent;"
        )
        root.addWidget(hdr)

        prompt = QLabel("What would you like to do?")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:12px; border:none; background:transparent;"
        )
        root.addWidget(prompt)

        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (mode, title, desc) in enumerate(self._MODES):
            grid.addWidget(self._make_card(mode, title, desc), i // 3, i % 3)
        root.addLayout(grid)

        ver = QLabel(f"v{APP_VERSION}")
        ver.setAlignment(Qt.AlignmentFlag.AlignRight)
        ver.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:10px; border:none; background:transparent;"
        )
        root.addWidget(ver)

    def _make_card(self, mode: str, title: str, desc: str) -> QFrame:
        card = QFrame()
        card.setFixedSize(210, 100)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {PANEL_BG};
                border: 1px solid {BORDER_CLR};
                border-radius: 6px;
            }}
            QFrame:hover {{
                border-color: {ACCENT};
                background-color: #0d2e25;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(5)

        t = QLabel(title)
        t.setStyleSheet(
            f"color:{ACCENT}; font-size:12px; font-weight:bold;"
            f" background:transparent; border:none;"
        )
        lay.addWidget(t)

        d = QLabel(desc)
        d.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:10px; background:transparent; border:none;"
        )
        d.setWordWrap(True)
        lay.addWidget(d, 1)

        card.mousePressEvent = lambda _e, m=mode: self._choose(m)
        return card

    def _choose(self, mode: str):
        self.chosen_mode = mode
        self.accept()

    def closeEvent(self, e):
        self.reject()
        e.accept()


# ──────────────────────────────────────────────────────────────────────────────
# Main window
# ──────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, mode: str = "pre"):
        super().__init__()
        self._startup_mode = mode
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        # Start maximised so the layout fills whatever screen is available
        self.showMaximized()

        # State
        self._scan_name: Optional[str]  = None
        self._scan_dir:  Optional[Path] = None
        self._roi = (995, 270, 700)
        self._thread: Optional[QThread]  = None
        self._worker = None
        self._mode   = "idle"
        self._updating_overlays = False   # guard against spinbox↔overlay feedback loops
        self._last_preview_frame: Optional[np.ndarray] = None
        self._freeze_preview = False      # keep last scan frame after scan completes

        # Camera
        self._sim = CameraSimulator()
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._update_preview)
        self._frame_timer.start(100)

        self._build_ui()
        self._connect_signals()
        self.setStyleSheet(STYLESHEET)
        self._auto_detect_axis()   # populate crop centre X from flat field if available

        # Keep USB-ethernet alive and track camera reachability
        self._probe_thread: Optional[QThread] = None
        self._probe_worker = None
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._probe_camera)
        self._ping_timer.start(PING_INTERVAL_MS)
        self._probe_camera()   # immediate first check

        # Apply startup mode after event loop starts so dialogs open on top
        QTimer.singleShot(0, lambda: self._apply_startup_mode(self._startup_mode))

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Menu bar ──────────────────────────────────────────────────────────
        wf_menu = self.menuBar().addMenu("Workflow")
        for mode, label, _ in StartupDialog._MODES:
            action = QAction(label, self)
            action.triggered.connect(lambda checked, m=mode: self._apply_startup_mode(m))
            wf_menu.addAction(action)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Left column: preview + scan controls ──────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        # Camera preview
        preview_box = QGroupBox("Camera preview")
        pl = QVBoxLayout(preview_box)
        self.preview = PreviewWithROI()
        self.preview.set_default_roi(cx=995, top=270, extent=700,
                                     sample_top=0, sample_h=450)
        self.preview.setMinimumHeight(200)
        pl.addWidget(self.preview, 1)
        self.roi_label = QLabel("ROI: cx=995  top=270  extent=700")
        self.roi_label.setObjectName("dim")
        pl.addWidget(self.roi_label)
        self._cam_status_lbl = QLabel("● Camera: checking…")
        self._cam_status_lbl.setObjectName("dim")
        pl.addWidget(self._cam_status_lbl)
        left.addWidget(preview_box, 3)

        # Scan controls
        ctrl_box = QGroupBox("Scan controls")
        ctrl_grid = QGridLayout(ctrl_box)
        ctrl_grid.setColumnStretch(1, 1)
        ctrl_grid.setSpacing(6)

        # Session type (row 0)
        ctrl_grid.addWidget(QLabel("Session:"), 0, 0)
        self.scan_mode_combo = QComboBox()
        self.scan_mode_combo.addItem("Pre-irradiation scan", "pre")
        self.scan_mode_combo.addItem("Post-irradiation scan", "post")
        ctrl_grid.addWidget(self.scan_mode_combo, 0, 1, 1, 2)

        # Scan name — shown in pre mode (row 1)
        self.scan_name_lbl = QLabel("Scan name:")
        ctrl_grid.addWidget(self.scan_name_lbl, 1, 0)
        self.scan_name_edit = QLineEdit(time.strftime("scan_%Y%m%d_%H%M%S"))
        ctrl_grid.addWidget(self.scan_name_edit, 1, 1, 1, 2)

        # Pre scan selector — shown in post mode (row 1, same slot, toggled)
        self.pre_scan_lbl = QLabel("Pre scan:")
        ctrl_grid.addWidget(self.pre_scan_lbl, 2, 0)
        pre_scan_row = QWidget()
        pre_scan_rl = QHBoxLayout(pre_scan_row)
        pre_scan_rl.setContentsMargins(0, 0, 0, 0)
        self.pre_scan_combo = QComboBox()
        self.pre_scan_refresh_btn = QPushButton("⟳")
        self.pre_scan_refresh_btn.setFixedWidth(32)
        self.pre_scan_refresh_btn.setToolTip("Refresh pre-scan list")
        pre_scan_rl.addWidget(self.pre_scan_combo, 1)
        pre_scan_rl.addWidget(self.pre_scan_refresh_btn)
        ctrl_grid.addWidget(pre_scan_row, 2, 1, 1, 2)
        # Row 2 hidden until post mode is selected
        self.pre_scan_lbl.setVisible(False)
        pre_scan_row.setVisible(False)
        self._pre_scan_row_widget = pre_scan_row

        # Step increment (row 3)
        ctrl_grid.addWidget(QLabel("Step (deg):"), 3, 0)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 30); self.step_spin.setValue(2)
        ctrl_grid.addWidget(self.step_spin, 3, 1)

        # Stacks (rows 4-5)
        ctrl_grid.addWidget(QLabel("Oct stack:"), 4, 0)
        self.oct_stack_spin = QSpinBox()
        self.oct_stack_spin.setRange(1, 20); self.oct_stack_spin.setValue(3)
        ctrl_grid.addWidget(self.oct_stack_spin, 4, 1)

        ctrl_grid.addWidget(QLabel("Dark/flat stack:"), 5, 0)
        self.calib_stack_spin = QSpinBox()
        self.calib_stack_spin.setRange(1, 20); self.calib_stack_spin.setValue(3)
        ctrl_grid.addWidget(self.calib_stack_spin, 5, 1)

        ctrl_grid.addWidget(QLabel("Settle (ms):"), 6, 0)
        self.settle_spin = QSpinBox()
        self.settle_spin.setRange(100, 2000); self.settle_spin.setValue(300)
        self.settle_spin.setSingleStep(100)
        self.settle_spin.setToolTip("Wait after motor stops before capturing (reduce to speed up scan)")
        ctrl_grid.addWidget(self.settle_spin, 6, 1)

        # Force new calibration (row 7)
        self.force_dark_cb = QCheckBox("Force new dark")
        self.force_flat_cb = QCheckBox("Force new flat")
        ctrl_grid.addWidget(self.force_dark_cb, 7, 0)
        ctrl_grid.addWidget(self.force_flat_cb, 7, 1)

        # Hardware toggles (row 8)
        self.real_camera_cb = QCheckBox("Real camera")
        self.real_serial_cb = QCheckBox("Real stepper")
        self.real_camera_cb.setChecked(True)
        self.real_serial_cb.setChecked(True)
        ctrl_grid.addWidget(self.real_camera_cb, 8, 0)
        ctrl_grid.addWidget(self.real_serial_cb, 8, 1)

        # Scan progress (row 9)
        ctrl_grid.addWidget(QLabel("Scan progress:"), 9, 0)
        self.scan_progress = QProgressBar()
        ctrl_grid.addWidget(self.scan_progress, 9, 1, 1, 2)

        # Start / Cancel row (row 10)
        btn_row = QHBoxLayout()
        self.start_stop_btn = QPushButton("▶  CAPTURE PRE SCAN")
        self.start_stop_btn.setObjectName("start_btn")
        self.start_stop_btn.setMinimumHeight(42)
        self.cancel_scan_btn = QPushButton("✕  CANCEL")
        self.cancel_scan_btn.setObjectName("stop_btn")
        self.cancel_scan_btn.setMinimumHeight(42)
        self.cancel_scan_btn.setEnabled(False)
        btn_row.addWidget(self.start_stop_btn, 3)
        btn_row.addWidget(self.cancel_scan_btn, 1)
        ctrl_grid.addLayout(btn_row, 10, 0, 1, 3)

        # Phase buttons — sub-steps that unlock once scan is started (rows 11-13)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        ctrl_grid.addWidget(sep, 11, 0, 1, 3)

        steps_lbl = QLabel("Scan steps:")
        steps_lbl.setObjectName("dim")
        ctrl_grid.addWidget(steps_lbl, 12, 0, 1, 3)

        self.phase_bar = PhaseButtonBar()
        phase_container = QWidget()
        phase_container.setContentsMargins(16, 0, 0, 0)
        pc_layout = QVBoxLayout(phase_container)
        pc_layout.setContentsMargins(16, 0, 0, 0)
        pc_layout.setSpacing(0)
        pc_layout.addWidget(self.phase_bar)
        ctrl_grid.addWidget(phase_container, 13, 0, 1, 3)

        left.addWidget(ctrl_box, 2)
        root.addLayout(left, 5)

        # ── Right column: reconstruct controls + plot + log ───────────────
        right = QVBoxLayout()
        right.setSpacing(6)

        # Reconstruction controls
        recon_box = QGroupBox("Reconstruction")
        rg = QGridLayout(recon_box)
        rg.setColumnStretch(1, 1)
        rg.setSpacing(6)

        # Scan selector
        rg.addWidget(QLabel("Scan:"), 0, 0)
        self.scan_selector = QComboBox()
        rg.addWidget(self.scan_selector, 0, 1, 1, 2)

        rg.addWidget(QLabel("Crop centre X:"), 1, 0)
        self.crop_cx_spin = QSpinBox()
        self.crop_cx_spin.setRange(1, 9999); self.crop_cx_spin.setValue(995)
        rg.addWidget(self.crop_cx_spin, 1, 1)
        self.auto_axis_btn = QPushButton("Auto-detect")
        self.auto_axis_btn.setToolTip("Detect axis of rotation from nozzle edges in current frame")
        rg.addWidget(self.auto_axis_btn, 1, 2)

        rg.addWidget(QLabel("Crop top Y:"), 2, 0)
        self.crop_top_spin = QSpinBox()
        self.crop_top_spin.setRange(0, 9999); self.crop_top_spin.setValue(270)
        rg.addWidget(self.crop_top_spin, 2, 1)

        rg.addWidget(QLabel("Crop extent (px):"), 3, 0)
        self.crop_extent_spin = QSpinBox()
        self.crop_extent_spin.setRange(64, 4096); self.crop_extent_spin.setValue(700)
        rg.addWidget(self.crop_extent_spin, 3, 1)

        rg.addWidget(QLabel("Sample top (px):"), 4, 0)
        self.sample_top_spin = QSpinBox()
        self.sample_top_spin.setRange(0, 9999); self.sample_top_spin.setValue(0)
        rg.addWidget(self.sample_top_spin, 4, 1)

        rg.addWidget(QLabel("Sample height (px):"), 5, 0)
        self.sample_h_spin = QSpinBox()
        self.sample_h_spin.setRange(1, 9999); self.sample_h_spin.setValue(450)
        rg.addWidget(self.sample_h_spin, 5, 1)

        self.force_vol_cb = QCheckBox("Force new volume")
        rg.addWidget(self.force_vol_cb, 6, 0, 1, 2)

        rg.addWidget(QLabel("Recon progress:"), 7, 0)
        self.recon_progress = QProgressBar()
        rg.addWidget(self.recon_progress, 7, 1)

        recon_btn_row = QHBoxLayout()
        self.recon_btn = QPushButton("▶  RUN RECONSTRUCTION")
        self.recon_btn.setObjectName("start_btn")
        self.recon_btn.setMinimumHeight(36)
        self.recon_btn.setEnabled(False)
        self.cancel_recon_btn = QPushButton("✕  CANCEL")
        self.cancel_recon_btn.setObjectName("stop_btn")
        self.cancel_recon_btn.setMinimumHeight(36)
        self.cancel_recon_btn.setEnabled(False)
        recon_btn_row.addWidget(self.recon_btn, 3)
        recon_btn_row.addWidget(self.cancel_recon_btn, 1)
        rg.addLayout(recon_btn_row, 8, 0, 1, 2)

        right.addWidget(recon_box)

        # Export button
        self.export_btn = QPushButton("Export scan to USB drive…")
        right.addWidget(self.export_btn)

        # Dose/depth plot
        plot_box = QGroupBox("Depth dose plot")
        pl2 = QVBoxLayout(plot_box)
        self.plot = DoseDepthPlot()
        pl2.addWidget(self.plot, 1)
        self.load_dose_btn = QPushButton("Load depth dose…")
        pl2.addWidget(self.load_dose_btn)
        right.addWidget(plot_box, 2)

        # Status log
        log_box = QGroupBox("Log")
        ll = QVBoxLayout(log_box)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(120)
        ll.addWidget(self.log_view)
        right.addWidget(log_box)

        ver_lbl = QLabel(f"v{APP_VERSION}")
        ver_lbl.setObjectName("dim")
        ver_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(ver_lbl)

        root.addLayout(right, 4)

    # ── Signal connections ────────────────────────────────────────────────────

    def _connect_signals(self):
        self.preview.roiChanged.connect(self._on_roi_changed)
        self.preview.sampleChanged.connect(self._on_sample_changed)
        self.start_stop_btn.clicked.connect(self._toggle_scan)
        self.cancel_scan_btn.clicked.connect(self._cancel_scan)
        self.recon_btn.clicked.connect(self._start_reconstruction)
        self.cancel_recon_btn.clicked.connect(self._cancel_recon)
        self.export_btn.clicked.connect(
            lambda: ExportDialog(self, current_scan=self.scan_selector.currentData()).exec()
        )
        self.load_dose_btn.clicked.connect(self._load_depth_dose_file)
        self.phase_bar.phase_requested.connect(self._on_phase_requested)
        self.auto_axis_btn.clicked.connect(self._auto_detect_axis)
        self.scan_selector.currentIndexChanged.connect(self._on_scan_selected)
        self.scan_mode_combo.currentIndexChanged.connect(self._on_scan_mode_changed)
        self.pre_scan_combo.currentIndexChanged.connect(self._on_pre_scan_selected)
        self.pre_scan_refresh_btn.clicked.connect(self._populate_pre_scan_combo)
        self.real_camera_cb.stateChanged.connect(lambda _: self._probe_camera())
        self._populate_scan_selector()
        self._populate_pre_scan_combo()

        # Spinbox → overlay (crop)
        for spin in (self.crop_cx_spin, self.crop_top_spin, self.crop_extent_spin):
            spin.valueChanged.connect(self._on_crop_spinbox_changed)

        # Spinbox → overlay (sample)
        for spin in (self.sample_top_spin, self.sample_h_spin):
            spin.valueChanged.connect(self._on_sample_spinbox_changed)

    def _on_scan_mode_changed(self):
        mode = self.scan_mode_combo.currentData()
        is_pre = (mode == "pre")
        self.scan_name_lbl.setVisible(is_pre)
        self.scan_name_edit.setVisible(is_pre)
        self.pre_scan_lbl.setVisible(not is_pre)
        self._pre_scan_row_widget.setVisible(not is_pre)
        if is_pre:
            self.start_stop_btn.setText("▶  CAPTURE PRE SCAN")
        else:
            self.start_stop_btn.setText("▶  CAPTURE POST SCAN")

    def _populate_pre_scan_combo(self):
        """List scan folders that have at least one PNG in their pre/ subdirectory."""
        self.pre_scan_combo.blockSignals(True)
        self.pre_scan_combo.clear()
        if SCANS_DIR.exists():
            scans = sorted(
                [p for p in SCANS_DIR.iterdir()
                 if p.is_dir() and (p / "pre").is_dir()
                 and list((p / "pre").glob("*.png"))],
                reverse=True
            )
            for p in scans:
                self.pre_scan_combo.addItem(p.name, userData=p)
        self.pre_scan_combo.blockSignals(False)
        self._on_pre_scan_selected()   # sync spinbox with current selection

    @staticmethod
    def _read_pre_scan_meta(scan_dir: Path) -> dict:
        meta_path = scan_dir / "scan_meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text())
            except Exception:
                pass
        return {}

    def _on_pre_scan_selected(self):
        """When the user picks a pre-scan, auto-populate step degrees from its metadata."""
        path = self.pre_scan_combo.currentData()
        if path is None:
            return
        meta = self._read_pre_scan_meta(path)
        step = meta.get("step_deg")
        if step is not None:
            self.step_spin.setValue(step)

    def _on_crop_spinbox_changed(self):
        if self._updating_overlays:
            return
        self.preview.set_crop_overlay(
            self.crop_cx_spin.value(),
            self.crop_top_spin.value(),
            self.crop_extent_spin.value(),
        )

    def _on_sample_spinbox_changed(self):
        if self._updating_overlays:
            return
        self.preview.set_sample_overlay(
            self.sample_top_spin.value(),
            self.sample_h_spin.value(),
        )

    def _on_phase_requested(self, idx: int):
        """User clicked a phase button — tell the worker to proceed."""
        if self._worker and hasattr(self._worker, 'proceed'):
            self._worker.proceed()

    # ── Camera keep-alive / status ────────────────────────────────────────────

    def _probe_camera(self):
        """Fire a non-blocking TCP probe; reuses the thread slot so at most one runs."""
        if self._probe_thread and self._probe_thread.isRunning():
            return
        self._probe_thread = QThread()
        self._probe_worker = CameraProbeWorker()   # kept alive as instance var
        self._probe_worker.moveToThread(self._probe_thread)
        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_worker.result.connect(self._on_camera_probe)
        self._probe_worker.result.connect(self._probe_thread.quit)
        self._probe_thread.start()

    def _on_camera_probe(self, reachable: bool):
        if reachable:
            self._cam_status_lbl.setText(f"● Camera: online  ({CAMERA_URL})")
            self._cam_status_lbl.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        else:
            self._cam_status_lbl.setText(f"● Camera: offline  ({CAMERA_URL})")
            self._cam_status_lbl.setStyleSheet(f"color:{BTN_STOP}; font-size:11px;")

    # ── Preview update ────────────────────────────────────────────────────────

    def _auto_detect_axis(self):
        flat_path = CONFIG_DIR / "flat.npy"
        if not flat_path.exists():
            self._log("⚠ No flat field found — capture a flat before detecting the axis.")
            return
        img = np.load(flat_path)
        cx = find_axis_from_nozzle(img)
        if cx is None:
            self._log("⚠ Axis detection failed — could not locate nozzle in top of image.")
            return
        cx_int = int(round(cx))
        self._log(f"✓ Axis detected at x={cx_int} px")
        # Update the seeded default so the first frame places the ROI correctly
        # (roi_item may not exist yet if called on startup before any frame arrives)
        self.preview._default_cx = cx_int
        self.crop_cx_spin.setValue(cx_int)

    def _update_preview(self):
        if self._freeze_preview:
            return
        if self._mode != "scanning":
            img = self._sim.get_frame()
            self._last_preview_frame = img
            self.preview.set_frame(img)

    # ── ROI ──────────────────────────────────────────────────────────────────

    def _on_roi_changed(self, cx: int, top: int, extent: int):
        self._roi = (cx, top, extent)
        self.roi_label.setText(
            f"Green: crop  cx={cx}  top={top}  extent={extent} px  |  "
            f"Blue: sample top/height (within crop)")
        self._updating_overlays = True
        self.crop_cx_spin.setValue(cx)
        self.crop_top_spin.setValue(top)
        self.crop_extent_spin.setValue(extent)
        self._updating_overlays = False

    def _on_sample_changed(self, top_rel: int, height: int):
        self._updating_overlays = True
        self.sample_top_spin.setValue(top_rel)
        self.sample_h_spin.setValue(height)
        self._updating_overlays = False

    def _toggle_scan(self):
        if self._mode != "idle":
            return  # shouldn't happen; button is disabled while scanning

        if self.real_camera_cb.isChecked() and not probe_camera():
            QMessageBox.warning(
                self, "Camera unreachable",
                f"Cannot connect to camera at {CAMERA_URL}.\n\n"
                "Check the USB cable and the camera server on the RPi Zero.\n"
                "Uncheck 'Real camera' to run in simulator mode."
            )
            return

        scan_mode = self.scan_mode_combo.currentData()  # "pre" or "post"

        if scan_mode == "pre":
            name = self.scan_name_edit.text().strip() or time.strftime("scan_%Y%m%d_%H%M%S")
            self._scan_dir = SCANS_DIR / name
            pre_dir        = self._scan_dir / "pre"
            post_dir       = self._scan_dir / "post"
            subtracted_dir = self._scan_dir / "subtracted"
            # Clear only pre/ — post/ and subtracted/ are untouched (may not exist yet)
            if pre_dir.exists():
                shutil.rmtree(pre_dir)
            pre_dir.mkdir(parents=True)
        else:  # "post"
            pre_scan_dir = self.pre_scan_combo.currentData()
            if pre_scan_dir is None:
                QMessageBox.warning(self, "Post scan", "No pre-irradiation scan available. Run a pre scan first.")
                return
            self._scan_dir = pre_scan_dir
            name           = pre_scan_dir.name
            pre_dir        = self._scan_dir / "pre"
            post_dir       = self._scan_dir / "post"
            subtracted_dir = self._scan_dir / "subtracted"
            if not pre_dir.is_dir() or not list(pre_dir.glob("*.png")):
                QMessageBox.warning(self, "Post scan", f"No pre-scan images found in:\n{pre_dir}")
                return
            # Validate step degrees against the pre-scan metadata
            meta = self._read_pre_scan_meta(pre_scan_dir)
            pre_step = meta.get("step_deg")
            if pre_step is not None and pre_step != self.step_spin.value():
                QMessageBox.critical(
                    self, "Step angle mismatch",
                    f"The pre-scan used {pre_step}° steps "
                    f"({meta.get('num_positions')} positions).\n"
                    f"The post-scan is set to {self.step_spin.value()}°.\n\n"
                    "The step angle must match exactly for correct subtraction.\n"
                    "Correct the Step (deg) spinbox and try again."
                )
                return
            if pre_step is None:
                self._log("⚠ No scan_meta.json found — cannot verify step angle matches pre-scan")
            # Clear post/ and subtracted/ — pre/ is kept
            for d in (post_dir, subtracted_dir):
                if d.exists():
                    shutil.rmtree(d)
                d.mkdir(parents=True)

        self._scan_name = name

        cfg = dict(
            scan_mode      = scan_mode,
            scan_name      = name,
            pre_dir        = str(pre_dir),
            post_dir       = str(post_dir),
            subtracted_dir = str(subtracted_dir),
            degree_increment = self.step_spin.value(),
            oct_stack      = self.oct_stack_spin.value(),
            dark_stack     = self.calib_stack_spin.value(),
            flat_stack     = self.calib_stack_spin.value(),
            force_dark     = self.force_dark_cb.isChecked(),
            force_flat     = self.force_flat_cb.isChecked(),
            use_real_camera = self.real_camera_cb.isChecked(),
            use_real_serial = self.real_serial_cb.isChecked(),
            settle_ms       = self.settle_spin.value(),
        )

        self._thread = QThread()
        self._worker = ScanWorker(cfg)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.phase_ready.connect(self._on_phase_ready)
        self._worker.phase_running.connect(self._on_phase_running)
        self._worker.phase_done.connect(self._on_phase_done)
        self._worker.phase_skipped.connect(self._on_phase_skipped)
        self._worker.scan_progress.connect(self.scan_progress.setValue)
        self._worker.image_ready.connect(self.preview.set_frame)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._scan_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

        self._mode = "scanning"
        self._freeze_preview = False
        self.scan_progress.setValue(0)
        self.phase_bar.reset()
        self.start_stop_btn.setText("⏳  SCAN IN PROGRESS")
        self.start_stop_btn.setEnabled(False)
        self.scan_mode_combo.setEnabled(False)
        self.start_stop_btn.setStyleSheet(
            f"background:{PANEL_BG}; border:1px solid {BORDER_CLR};"
            f"color:{TEXT_DIM}; font-size:13px; font-weight:bold; min-height:36px;"
        )
        self.cancel_scan_btn.setEnabled(True)
        self.recon_btn.setEnabled(False)
        self._log(f"Starting scan: {name}")

    def _cancel_scan(self):
        self._log("Cancelling scan…")
        if self._worker and hasattr(self._worker, 'abort'):
            self._worker.abort()
        lamp_off()
        self.cancel_scan_btn.setEnabled(False)

    def _on_phase_ready(self, idx: int):
        """Worker is waiting — enable that phase's button."""
        self.phase_bar.ready_phase(idx)

    def _on_phase_running(self, idx: int):
        """Worker is executing the phase — show spinner state."""
        self.phase_bar.set_phase_state(idx, 2)

    def _on_phase_done(self, idx: int):
        """Phase finished successfully."""
        self.phase_bar.set_phase_state(idx, 3)

    def _on_phase_skipped(self, idx: int):
        """Phase was skipped (post-only mode)."""
        self.phase_bar.set_phase_state(idx, 4)

    def _scan_finished(self, ok: bool, msg: str):
        self._mode = "idle"
        self._freeze_preview = ok   # keep last scan image; clear on failure/cancel
        mode = self.scan_mode_combo.currentData()
        label = "▶  CAPTURE PRE SCAN" if mode == "pre" else "▶  CAPTURE POST SCAN"
        self.start_stop_btn.setText(label)
        self.start_stop_btn.setStyleSheet("")   # restore stylesheet objectName style
        self.start_stop_btn.setEnabled(True)
        self.scan_mode_combo.setEnabled(True)
        self.cancel_scan_btn.setEnabled(False)
        # Refresh relevant selectors
        self._populate_pre_scan_combo()
        if ok and self._scan_dir is not None and mode == "post":
            self._populate_scan_selector(select_path=self._scan_dir)
        else:
            self._on_scan_selected()   # re-evaluate enable state
        if not ok:
            self.phase_bar.reset()
        self._log(f"{'✓' if ok else '✗'} {msg}")
        if not ok and "abort" not in msg.lower() and "cancel" not in msg.lower():
            QMessageBox.critical(self, "Scan error", msg)

    # ── Scan selector ─────────────────────────────────────────────────────────

    def _populate_scan_selector(self, select_path: Optional[Path] = None):
        """Refresh the scan combobox from SCANS_DIR; optionally pre-select a path."""
        self.scan_selector.blockSignals(True)
        self.scan_selector.clear()
        scans = [p for p in sorted(SCANS_DIR.iterdir(), reverse=True)
                 if p.is_dir() and (p / "subtracted").is_dir()]  \
                 if SCANS_DIR.exists() else []
        for p in scans:
            self.scan_selector.addItem(p.name, userData=p)
        if select_path is not None:
            for i in range(self.scan_selector.count()):
                if self.scan_selector.itemData(i) == select_path:
                    self.scan_selector.setCurrentIndex(i)
                    break
        self.scan_selector.blockSignals(False)
        self._on_scan_selected()

    def _on_scan_selected(self):
        path = self.scan_selector.currentData()
        self.recon_btn.setEnabled(
            path is not None and self._mode == "idle")

    # ── Reconstruction ────────────────────────────────────────────────────────

    def _start_reconstruction(self):
        if self._mode != "idle":
            self._log("Cannot reconstruct while scan is running.")
            return

        scan_dir = self.scan_selector.currentData()
        if scan_dir is None:
            QMessageBox.warning(self, "Reconstruction", "No scan selected.")
            return

        # Validate projection count — need at least 2 views for FBP
        subtracted_dir = scan_dir / "subtracted"
        n_proj = len(list(subtracted_dir.glob("*.png"))) if subtracted_dir.is_dir() else 0
        min_proj = max(2, int(360 / max(self.step_spin.value(), 1)))
        if n_proj < min_proj:
            msg = (f"Only {n_proj} projection(s) found in '{subtracted_dir.name}' "
                   f"(need at least {min_proj} for a {self.step_spin.value()}° step scan).")
            self._log(f"✗ {msg}")
            self.recon_progress.setValue(0)
            QMessageBox.warning(self, "Insufficient projections", msg)
            return

        recon_dir      = str(scan_dir / "reconstruct")
        dose_dir       = str(scan_dir / "dose-profiles")
        depth_dose_dir = str(scan_dir / "depth-dose")

        cfg = dict(
            image_dir      = str(scan_dir / "subtracted"),
            reconstruct_dir = recon_dir,
            dose_dir       = dose_dir,
            depth_dose_dir = depth_dose_dir,
            degree_increment = self.step_spin.value(),
            crop_cx        = self.crop_cx_spin.value(),
            crop_top       = self.crop_top_spin.value(),
            crop_extent    = self.crop_extent_spin.value(),
            sample_top     = self.sample_top_spin.value(),
            sample_height  = self.sample_h_spin.value(),
            force_new_vol  = self.force_vol_cb.isChecked(),
        )

        self._thread = QThread()
        self._worker = ReconWorker(cfg)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.recon_progress.setValue)
        self._worker.log.connect(self._log)
        self._worker.dose_ready.connect(
            lambda d, r: self.plot.set_data(d, r, title=f"Depth Dose — {scan_dir.name}"))
        self._worker.finished.connect(self._recon_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

        self._mode = "reconstructing"
        self.recon_progress.setValue(0)
        self.recon_btn.setEnabled(False)
        self.cancel_recon_btn.setEnabled(True)
        self.start_stop_btn.setEnabled(False)
        self._log(f"Reconstructing: {scan_dir.name}")

    def _cancel_recon(self):
        self._log("Cancelling reconstruction…")
        if self._worker and hasattr(self._worker, 'abort'):
            self._worker.abort()
        self.cancel_recon_btn.setEnabled(False)

    def _recon_finished(self, ok: bool, msg: str):
        self._mode = "idle"
        self.cancel_recon_btn.setEnabled(False)
        self.start_stop_btn.setEnabled(True)
        if not ok:
            self.recon_progress.setValue(0)
        self._on_scan_selected()   # re-enable recon btn if a valid scan is still selected
        self._log(f"{'✓' if ok else '✗'} {msg}")
        if not ok and "abort" not in msg.lower() and "cancel" not in msg.lower():
            QMessageBox.critical(self, "Reconstruction error", msg)

    # ── Startup mode ──────────────────────────────────────────────────────────

    def _apply_startup_mode(self, mode: str):
        if mode == "post":
            self.scan_mode_combo.setCurrentIndex(1)
        elif mode == "reconstruct":
            self.recon_btn.setFocus()
        elif mode == "view_dose":
            self._load_depth_dose_file()
        elif mode == "export":
            ExportDialog(self, current_scan=self.scan_selector.currentData()).exec()
        # "pre" is the default combo state — nothing extra needed

    def _load_depth_dose_file(self):
        """Load a depth_dose.xlsx produced by a prior reconstruction and plot it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open depth dose file", str(SCANS_DIR),
            "Excel files (*.xlsx);;CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            import pandas as pd
            df = (pd.read_excel(path) if path.endswith(".xlsx")
                  else pd.read_csv(path))
            depth_mm = df["depth_mm"].to_numpy()
            rel_dose  = df["rel_dose"].to_numpy()
            title = Path(path).parts[-3] if len(Path(path).parts) >= 3 else Path(path).stem
            self.plot.set_data(depth_mm, rel_dose, title=f"Depth Dose — {title}")
            self._log(f"✓ Loaded depth dose: {path}")
        except Exception as e:
            self._log(f"✗ Failed to load depth dose: {e}")
            QMessageBox.critical(self, "Load error", str(e))

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_view.append(f"[{ts}] {msg}")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, e):
        if self._worker and hasattr(self._worker, 'abort'):
            self._worker.abort()
        lamp_off()
        super().closeEvent(e)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)   # applied globally so StartupDialog inherits it

    dlg = StartupDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        sys.exit(0)

    w = MainWindow(mode=dlg.chosen_mode)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
