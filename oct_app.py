#!/usr/bin/env python3
"""
Single-file PyQt6 Optical-CT style UI skeleton.

Features:
- Start/Stop scan
- Step increment (2–10 deg)
- Live camera preview (simulated) with interactive square ROI overlay (drag + resize)
- Scan progress (0–100%)
- Reconstruction progress (0–100%)
- Dose/depth plot (matplotlib)
- Export to USB drive workflow (select scan + select USB/mount + export)

Notes:
- Camera feed is simulated. Replace CameraSimulator with your real capture (e.g., requests->PNG->np array).
- "Scan" output is simulated as a folder under ./scans/ with metadata.json and preview.png.
- USB detection is best-effort cross-platform. Also provides "Browse..." to pick a destination.

Requires: PyQt6, numpy, matplotlib
"""

import os
import sys
import json
import time
import math
import shutil
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List

import numpy as np

from PyQt6.QtCore import (
    Qt, QTimer, QRectF, QPointF, QSize, pyqtSignal, QObject, QThread
)
from PyQt6.QtGui import (
    QAction, QImage, QPixmap, QPainter, QPen, QColor, QBrush
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QSpinBox, QProgressBar, QGroupBox, QFileDialog,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem,
    QDialog, QListWidget, QListWidgetItem, QComboBox, QMessageBox, QFormLayout,
    QLineEdit
)

# Matplotlib embedding (QtAgg)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


APP_TITLE = "Optical CT Scan UI (PyQt6) - Single File"
SCANS_DIR = Path("./scans").resolve()
SCANS_DIR.mkdir(parents=True, exist_ok=True)


# -------------------------
# Helpers: basic USB/mount discovery
# -------------------------

def list_usb_like_mounts() -> List[Path]:
    """
    Best-effort: list mount points that *look* like external drives.
    Always allow user to browse as fallback.
    """
    mounts: List[Path] = []

    # macOS: /Volumes/<Name>
    vol = Path("/Volumes")
    if vol.exists():
        for p in vol.iterdir():
            if p.is_dir() and p.name not in {"Macintosh HD", "Macintosh HD - Data"}:
                mounts.append(p)

    # Linux common mount points
    for base in (Path("/media"), Path("/run/media")):
        if base.exists():
            for root, dirs, _files in os.walk(base):
                # only add leaf-ish dirs at shallow depth
                rootp = Path(root)
                if rootp.is_dir() and rootp != base:
                    # heuristic: if it has 'lost+found' or is writable
                    try:
                        testfile = rootp / ".write_test_tmp"
                        with open(testfile, "w") as f:
                            f.write("x")
                        testfile.unlink(missing_ok=True)
                        mounts.append(rootp)
                    except Exception:
                        pass
                    # don't traverse too deep
                    if rootp.parts.count(base.name) >= 1 and len(rootp.parts) - len(base.parts) >= 2:
                        dirs[:] = []
                # avoid huge walk; keep it cheap
                if len(mounts) > 30:
                    break

    # Windows: drive letters, filter by existence and writable
    if os.name == "nt":
        for letter in string.ascii_uppercase:
            p = Path(f"{letter}:\\")
            if p.exists():
                # heuristic: skip system drive
                if letter.upper() == "C":
                    continue
                try:
                    testfile = p / ".write_test_tmp"
                    with open(testfile, "w") as f:
                        f.write("x")
                    testfile.unlink(missing_ok=True)
                    mounts.append(p)
                except Exception:
                    pass

    # Deduplicate
    uniq = []
    seen = set()
    for m in mounts:
        try:
            rp = m.resolve()
        except Exception:
            rp = m
        if str(rp) not in seen:
            seen.add(str(rp))
            uniq.append(rp)

    # Sort stable
    return sorted(uniq, key=lambda x: str(x).lower())


def list_scans() -> List[Path]:
    scans = []
    if SCANS_DIR.exists():
        for p in SCANS_DIR.iterdir():
            if p.is_dir():
                scans.append(p)
    scans.sort(key=lambda p: p.name, reverse=True)
    return scans


# -------------------------
# Camera simulator
# -------------------------

class CameraSimulator(QObject):
    """
    Simulates a grayscale camera feed as a moving pattern.
    Replace `get_frame()` with your real capture.
    """
    def __init__(self, w: int = 1280, h: int = 720):
        super().__init__()
        self.w = w
        self.h = h
        self.t0 = time.time()

    def get_frame(self) -> np.ndarray:
        t = time.time() - self.t0
        y = np.linspace(0, 1, self.h, dtype=np.float32)[:, None]
        x = np.linspace(0, 1, self.w, dtype=np.float32)[None, :]
        # pseudo object + illumination gradients
        cx = 0.5 + 0.15 * math.sin(t * 0.7)
        cy = 0.5 + 0.12 * math.cos(t * 0.9)
        r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
        blob = np.exp(-(r ** 2) / (2 * (0.12 ** 2)))
        stripes = 0.5 + 0.5 * np.sin(2 * math.pi * (x * 4.0 + t * 0.25))
        img = (0.25 * stripes + 0.85 * blob + 0.15 * y)
        img = np.clip(img, 0, 1)
        img = (img * 255.0).astype(np.uint8)
        # add mild noise
        noise = np.random.normal(0, 5, size=img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return img


def np_gray_to_qimage(gray: np.ndarray) -> QImage:
    """
    Convert HxW uint8 array to QImage (Format_Grayscale8).
    """
    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)
    h, w = gray.shape
    # QImage uses bytes; must ensure contiguous
    gray_c = np.ascontiguousarray(gray)
    qimg = QImage(gray_c.data, w, h, w, QImage.Format.Format_Grayscale8)
    # copy to own buffer to be safe
    return qimg.copy()


# -------------------------
# Resizable square ROI item
# -------------------------

class ResizableSquareItem(QGraphicsItem):
    """
    Interactive square ROI:
    - Drag inside to move
    - Drag corner handles to resize (maintains square)
    ROI is constrained within scene rect.
    """
    roiChanged = pyqtSignal()

    HANDLE_SIZE = 10.0

    def __init__(self, rect: QRectF, scene_rect: QRectF):
        super().__init__()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self._rect = rect
        self._scene_rect = scene_rect

        self._drag_mode = None  # None / "move" / "resize_tl" / "resize_tr" / "resize_bl" / "resize_br"
        self._press_pos = QPointF()
        self._press_rect = QRectF()

        self._pen = QPen(QColor(0, 255, 0), 2)
        self._handle_brush = QBrush(QColor(0, 255, 0, 180))
        self._fill_brush = QBrush(QColor(0, 255, 0, 40))

        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        # extra space for handles
        pad = self.HANDLE_SIZE + 2
        r = QRectF(self._rect)
        r.adjust(-pad, -pad, pad, pad)
        return r

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def setSceneRectConstraint(self, scene_rect: QRectF):
        self._scene_rect = QRectF(scene_rect)
        self._rect = self._constrain_rect(self._rect)
        self.prepareGeometryChange()

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.setPen(self._pen)
        painter.setBrush(self._fill_brush)
        painter.drawRect(self._rect)

        # handles
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._handle_brush)
        for hrect in self._handle_rects():
            painter.drawRect(hrect)

    def _handle_rects(self) -> List[QRectF]:
        hs = self.HANDLE_SIZE
        r = self._rect
        return [
            QRectF(r.left() - hs / 2,  r.top() - hs / 2,  hs, hs),   # tl
            QRectF(r.right() - hs / 2, r.top() - hs / 2,  hs, hs),   # tr
            QRectF(r.left() - hs / 2,  r.bottom() - hs / 2, hs, hs), # bl
            QRectF(r.right() - hs / 2, r.bottom() - hs / 2, hs, hs), # br
        ]

    def _hit_test_handle(self, pos: QPointF) -> Optional[str]:
        tl, tr, bl, br = self._handle_rects()
        if tl.contains(pos): return "resize_tl"
        if tr.contains(pos): return "resize_tr"
        if bl.contains(pos): return "resize_bl"
        if br.contains(pos): return "resize_br"
        return None

    def hoverMoveEvent(self, event):
        mode = self._hit_test_handle(event.pos())
        if mode is not None:
            if mode in ("resize_tl", "resize_br"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif self._rect.contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        self._press_rect = QRectF(self._rect)

        handle_mode = self._hit_test_handle(event.pos())
        if handle_mode is not None:
            self._drag_mode = handle_mode
        elif self._rect.contains(event.pos()):
            self._drag_mode = "move"
        else:
            self._drag_mode = None
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            super().mouseMoveEvent(event)
            return

        delta = event.pos() - self._press_pos

        if self._drag_mode == "move":
            r = QRectF(self._press_rect)
            r.translate(delta)
            self._rect = self._constrain_rect(r)
            self.prepareGeometryChange()
            self.update()
            self._emit_changed()
            event.accept()
            return

        # resize: maintain square by anchoring opposite corner
        r0 = QRectF(self._press_rect)
        if self._drag_mode == "resize_tl":
            anchor = QPointF(r0.right(), r0.bottom())
            new_corner = QPointF(r0.left() + delta.x(), r0.top() + delta.y())
            self._rect = self._square_from_anchor(anchor, new_corner)
        elif self._drag_mode == "resize_tr":
            anchor = QPointF(r0.left(), r0.bottom())
            new_corner = QPointF(r0.right() + delta.x(), r0.top() + delta.y())
            self._rect = self._square_from_anchor(anchor, new_corner)
        elif self._drag_mode == "resize_bl":
            anchor = QPointF(r0.right(), r0.top())
            new_corner = QPointF(r0.left() + delta.x(), r0.bottom() + delta.y())
            self._rect = self._square_from_anchor(anchor, new_corner)
        elif self._drag_mode == "resize_br":
            anchor = QPointF(r0.left(), r0.top())
            new_corner = QPointF(r0.right() + delta.x(), r0.bottom() + delta.y())
            self._rect = self._square_from_anchor(anchor, new_corner)

        self._rect = self._constrain_rect(self._rect, min_size=40.0)
        self.prepareGeometryChange()
        self.update()
        self._emit_changed()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
        event.accept()

    def _square_from_anchor(self, anchor: QPointF, corner: QPointF) -> QRectF:
        dx = corner.x() - anchor.x()
        dy = corner.y() - anchor.y()
        side = max(abs(dx), abs(dy))
        # preserve direction
        sx = side if dx >= 0 else -side
        sy = side if dy >= 0 else -side
        x1 = anchor.x()
        y1 = anchor.y()
        x2 = anchor.x() + sx
        y2 = anchor.y() + sy
        return QRectF(QPointF(min(x1, x2), min(y1, y2)), QPointF(max(x1, x2), max(y1, y2)))

    def _constrain_rect(self, rect: QRectF, min_size: float = 10.0) -> QRectF:
        r = QRectF(rect)

        # enforce min size square-ish
        side = max(r.width(), r.height())
        side = max(side, min_size)
        r.setWidth(side)
        r.setHeight(side)

        # clamp inside scene
        s = self._scene_rect
        if r.left() < s.left():
            r.moveLeft(s.left())
        if r.top() < s.top():
            r.moveTop(s.top())
        if r.right() > s.right():
            r.moveRight(s.right())
        if r.bottom() > s.bottom():
            r.moveBottom(s.bottom())

        # if scene smaller than roi, clamp
        r.setWidth(min(r.width(), s.width()))
        r.setHeight(min(r.height(), s.height()))

        return r

    def _emit_changed(self):
        # emit via QObject-style signal (QGraphicsItem isn't QObject)
        # We'll call back via a stored callable from view/controller instead.
        pass


# -------------------------
# Preview widget: QGraphicsView with pixmap + ROI square
# -------------------------

class PreviewWithROI(QWidget):
    roiChanged = pyqtSignal(int, int, int)  # x, y, size (pixels in image coords)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.view.setBackgroundBrush(QBrush(QColor(25, 25, 25)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self.pix_item: Optional[QGraphicsPixmapItem] = None
        self.roi_item: Optional[ResizableSquareItem] = None

        self._img_w = 0
        self._img_h = 0

        # timer to debounce ROI emission while dragging
        self._roi_emit_timer = QTimer(self)
        self._roi_emit_timer.setSingleShot(True)
        self._roi_emit_timer.timeout.connect(self._emit_roi)

    def set_frame(self, gray: np.ndarray):
        qimg = np_gray_to_qimage(gray)
        pix = QPixmap.fromImage(qimg)

        self._img_h, self._img_w = gray.shape

        if self.pix_item is None:
            self.pix_item = self.scene.addPixmap(pix)
            self.pix_item.setZValue(0)
        else:
            self.pix_item.setPixmap(pix)

        # Scene rect matches image pixels
        self.scene.setSceneRect(QRectF(0, 0, self._img_w, self._img_h))

        # Init ROI if needed
        if self.roi_item is None:
            size = min(self._img_w, self._img_h) * 0.55
            x = (self._img_w - size) / 2
            y = (self._img_h - size) / 2
            r = QRectF(x, y, size, size)
            self.roi_item = ResizableSquareItem(r, self.scene.sceneRect())
            self.roi_item.setZValue(10)
            self.scene.addItem(self.roi_item)

            # Monkey-patch change emission by hooking viewport mouse events via event filter
            self.roi_item._emit_changed = self._schedule_emit_roi

        else:
            self.roi_item.setSceneRectConstraint(self.scene.sceneRect())

        # Fit view
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        # Emit once after any size changes
        self._schedule_emit_roi()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _schedule_emit_roi(self):
        # debounce
        self._roi_emit_timer.start(50)

    def _emit_roi(self):
        if not self.roi_item or self._img_w <= 0:
            return
        r = self.roi_item.rect()
        x = int(round(r.left()))
        y = int(round(r.top()))
        size = int(round(r.width()))
        x = max(0, min(x, self._img_w - 1))
        y = max(0, min(y, self._img_h - 1))
        size = max(1, min(size, min(self._img_w - x, self._img_h - y)))
        self.roiChanged.emit(x, y, size)

    def current_roi(self) -> Tuple[int, int, int]:
        if not self.roi_item:
            return (0, 0, 0)
        r = self.roi_item.rect()
        return (int(r.left()), int(r.top()), int(r.width()))


# -------------------------
# Plot widget
# -------------------------

class DoseDepthPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(4, 3), dpi=100)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Depth (mm)")
        self.ax.set_ylabel("Relative Dose (a.u.)")
        self.ax.grid(True, alpha=0.3)
        self._line = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

        self.set_data(np.linspace(0, 60, 121), np.exp(-np.linspace(0, 60, 121) / 18.0))

    def set_data(self, depth_mm: np.ndarray, rel_dose: np.ndarray):
        self.ax.clear()
        self.ax.set_xlabel("Depth (mm)")
        self.ax.set_ylabel("Relative Dose (a.u.)")
        self.ax.grid(True, alpha=0.3)
        self.ax.plot(depth_mm, rel_dose)
        self.fig.tight_layout()
        self.canvas.draw_idle()


# -------------------------
# Export worker (copy scan folder)
# -------------------------

class ExportWorker(QObject):
    progress = pyqtSignal(int)     # 0..100
    finished = pyqtSignal(bool, str)

    def __init__(self, scan_dir: Path, dest_root: Path):
        super().__init__()
        self.scan_dir = scan_dir
        self.dest_root = dest_root

    def run(self):
        try:
            if not self.scan_dir.exists() or not self.scan_dir.is_dir():
                self.finished.emit(False, "Scan directory does not exist.")
                return
            if not self.dest_root.exists() or not self.dest_root.is_dir():
                self.finished.emit(False, "Destination root is not a directory.")
                return

            dest_dir = self.dest_root / self.scan_dir.name
            if dest_dir.exists():
                # make unique
                dest_dir = self.dest_root / f"{self.scan_dir.name}_{int(time.time())}"

            # Copy with crude progress: count files first
            files = [p for p in self.scan_dir.rglob("*") if p.is_file()]
            total = max(1, len(files))
            copied = 0

            dest_dir.mkdir(parents=True, exist_ok=True)
            for src in files:
                rel = src.relative_to(self.scan_dir)
                dst = dest_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
                self.progress.emit(int(round(100.0 * copied / total)))

            self.finished.emit(True, f"Exported to: {dest_dir}")
        except Exception as e:
            self.finished.emit(False, f"Export failed: {e}")


# -------------------------
# Export dialog
# -------------------------

class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export scan to USB drive")
        self.setMinimumSize(650, 420)

        self.scan_list = QListWidget()
        self.refresh_scans_btn = QPushButton("Refresh scans")

        self.mount_combo = QComboBox()
        self.refresh_mounts_btn = QPushButton("Refresh drives")
        self.browse_btn = QPushButton("Browse...")

        self.dest_line = QLineEdit()
        self.dest_line.setReadOnly(True)

        self.export_btn = QPushButton("Export")
        self.cancel_btn = QPushButton("Close")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        # Layout
        root = QVBoxLayout(self)

        scans_box = QGroupBox("Select scan")
        scans_l = QVBoxLayout(scans_box)
        scans_l.addWidget(self.scan_list)
        scans_l.addWidget(self.refresh_scans_btn)

        dest_box = QGroupBox("Select USB drive / destination")
        form = QFormLayout(dest_box)
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.addWidget(self.mount_combo, 1)
        row_l.addWidget(self.refresh_mounts_btn)
        row_l.addWidget(self.browse_btn)
        form.addRow("Drive:", row)
        form.addRow("Destination:", self.dest_line)

        root.addWidget(scans_box, 3)
        root.addWidget(dest_box, 1)
        root.addWidget(QLabel("Export progress:"))
        root.addWidget(self.progress)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.export_btn)
        btns.addWidget(self.cancel_btn)
        root.addLayout(btns)

        # State
        self._selected_scan: Optional[Path] = None
        self._selected_dest: Optional[Path] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[ExportWorker] = None

        # Signals
        self.refresh_scans_btn.clicked.connect(self.refresh_scans)
        self.refresh_mounts_btn.clicked.connect(self.refresh_mounts)
        self.browse_btn.clicked.connect(self.browse_destination)
        self.export_btn.clicked.connect(self.start_export)
        self.cancel_btn.clicked.connect(self.close)
        self.scan_list.currentItemChanged.connect(self._scan_selected)
        self.mount_combo.currentIndexChanged.connect(self._mount_selected)

        self.refresh_scans()
        self.refresh_mounts()

    def refresh_scans(self):
        self.scan_list.clear()
        for d in list_scans():
            meta = d / "metadata.json"
            label = d.name
            if meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    label = f"{d.name}  —  step={m.get('step_deg')}°, roi={m.get('roi')}"
                except Exception:
                    pass
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(d))
            self.scan_list.addItem(item)
        if self.scan_list.count() > 0:
            self.scan_list.setCurrentRow(0)

    def refresh_mounts(self):
        self.mount_combo.blockSignals(True)
        self.mount_combo.clear()

        mounts = list_usb_like_mounts()
        if not mounts:
            self.mount_combo.addItem("(No removable drives detected — use Browse...)", "")
        else:
            for p in mounts:
                self.mount_combo.addItem(str(p), str(p))
        self.mount_combo.blockSignals(False)
        self._mount_selected(self.mount_combo.currentIndex())

    def browse_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Select destination folder")
        if path:
            self._selected_dest = Path(path)
            self.dest_line.setText(str(self._selected_dest))

    def _scan_selected(self, current: QListWidgetItem, _previous: QListWidgetItem):
        if current is None:
            self._selected_scan = None
            return
        self._selected_scan = Path(current.data(Qt.ItemDataRole.UserRole))

    def _mount_selected(self, idx: int):
        data = self.mount_combo.itemData(idx)
        if data:
            self._selected_dest = Path(str(data))
            self.dest_line.setText(str(self._selected_dest))
        else:
            # keep current (maybe from browse)
            if self._selected_dest:
                self.dest_line.setText(str(self._selected_dest))
            else:
                self.dest_line.setText("")

    def start_export(self):
        if not self._selected_scan:
            QMessageBox.warning(self, "Export", "Select a scan first.")
            return
        if not self._selected_dest:
            QMessageBox.warning(self, "Export", "Select a destination (drive or folder).")
            return

        self.export_btn.setEnabled(False)
        self.progress.setValue(0)

        self._thread = QThread()
        self._worker = ExportWorker(self._selected_scan, self._selected_dest)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._export_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _export_finished(self, ok: bool, msg: str):
        self.export_btn.setEnabled(True)
        if ok:
            QMessageBox.information(self, "Export", msg)
        else:
            QMessageBox.critical(self, "Export", msg)


# -------------------------
# Main window
# -------------------------

@dataclass
class ScanSettings:
    step_deg: int = 2
    roi: Tuple[int, int, int] = (0, 0, 0)  # x,y,size


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 750)

        self.settings = ScanSettings(step_deg=2, roi=(0, 0, 0))

        # Camera / preview
        self.camera = CameraSimulator(w=1280, h=720)
        self.preview = PreviewWithROI()
        self.preview.roiChanged.connect(self._on_roi_changed)

        # Controls
        self.start_stop_btn = QPushButton("Start scan")
        self.step_spin = QSpinBox()
        self.step_spin.setRange(2, 10)
        self.step_spin.setSingleStep(1)
        self.step_spin.setValue(self.settings.step_deg)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)

        self.recon_progress = QProgressBar()
        self.recon_progress.setRange(0, 100)
        self.recon_progress.setValue(0)

        self.export_btn = QPushButton("Export the scan to a USB drive")

        # Plot
        self.plot = DoseDepthPlot()

        # Status labels
        self.roi_label = QLabel("ROI: (x=?, y=?, size=?)")
        self.step_label = QLabel("Step increment (deg):")
        self.mode_label = QLabel("Idle")

        # Layout
        central = QWidget()
        self.setCentralWidget(central)

        left_box = QGroupBox("Camera preview and ROI")
        left_l = QVBoxLayout(left_box)
        left_l.addWidget(self.preview, 1)
        left_l.addWidget(self.roi_label)

        controls_box = QGroupBox("Scan controls")
        grid = QGridLayout(controls_box)
        grid.addWidget(self.start_stop_btn, 0, 0, 1, 2)
        grid.addWidget(self.step_label, 1, 0)
        grid.addWidget(self.step_spin, 1, 1)
        grid.addWidget(QLabel("Scan progress:"), 2, 0)
        grid.addWidget(self.scan_progress, 2, 1)
        grid.addWidget(QLabel("Reconstruction progress:"), 3, 0)
        grid.addWidget(self.recon_progress, 3, 1)
        grid.addWidget(self.mode_label, 4, 0, 1, 2)
        grid.addWidget(self.export_btn, 5, 0, 1, 2)

        right_box = QGroupBox("Dose / depth plot")
        right_l = QVBoxLayout(right_box)
        right_l.addWidget(self.plot, 1)

        root = QHBoxLayout(central)
        left_col = QVBoxLayout()
        left_col.addWidget(left_box, 3)
        left_col.addWidget(controls_box, 2)

        root.addLayout(left_col, 2)
        root.addWidget(right_box, 2)

        # Timers / state
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._update_frame)
        self._frame_timer.start(100)  # ~10 FPS

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._scan_tick)

        self._recon_timer = QTimer(self)
        self._recon_timer.timeout.connect(self._recon_tick)

        self._is_scanning = False
        self._is_recon = False
        self._scan_step_count = 0
        self._scan_total_steps = 0
        self._current_scan_dir: Optional[Path] = None

        # Signals
        self.start_stop_btn.clicked.connect(self._toggle_scan)
        self.step_spin.valueChanged.connect(self._on_step_changed)
        self.export_btn.clicked.connect(self._open_export_dialog)

        # Menu (optional quick access)
        act_export = QAction("Export...", self)
        act_export.triggered.connect(self._open_export_dialog)
        self.menuBar().addMenu("&File").addAction(act_export)

        # initialize ROI display on first frame update

    def _on_step_changed(self, v: int):
        self.settings.step_deg = int(v)

    def _on_roi_changed(self, x: int, y: int, size: int):
        self.settings.roi = (x, y, size)
        self.roi_label.setText(f"ROI: (x={x}, y={y}, size={size})")

    def _update_frame(self):
        frame = self.camera.get_frame()

        # If ROI exists, you can also show a cropped preview elsewhere; for now only overlay.
        self.preview.set_frame(frame)

    def _toggle_scan(self):
        if self._is_scanning or self._is_recon:
            self._stop_all()
            return

        # Start scanning
        self._is_scanning = True
        self.start_stop_btn.setText("Stop scan")
        self.mode_label.setText("Scanning...")
        self.scan_progress.setValue(0)
        self.recon_progress.setValue(0)

        step = self.settings.step_deg
        self._scan_total_steps = max(1, int(round(360.0 / step)))
        self._scan_step_count = 0

        # Create scan output folder
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._current_scan_dir = SCANS_DIR / f"scan_{ts}"
        self._current_scan_dir.mkdir(parents=True, exist_ok=True)

        # Save metadata
        meta = {
            "created": ts,
            "step_deg": step,
            "roi": {"x": self.settings.roi[0], "y": self.settings.roi[1], "size": self.settings.roi[2]},
            "notes": "Simulated scan output"
        }
        (self._current_scan_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

        self._scan_timer.start(60)  # tick rate: tune as desired

    def _stop_all(self):
        self._scan_timer.stop()
        self._recon_timer.stop()
        self._is_scanning = False
        self._is_recon = False
        self.start_stop_btn.setText("Start scan")
        self.mode_label.setText("Idle")

    def _scan_tick(self):
        if not self._is_scanning:
            return

        self._scan_step_count += 1
        pct = int(round(100.0 * self._scan_step_count / self._scan_total_steps))
        self.scan_progress.setValue(min(100, pct))

        # Simulate saving a few frames during scan
        if self._current_scan_dir and self._scan_step_count in {1, self._scan_total_steps // 2, self._scan_total_steps}:
            frame = self.camera.get_frame()
            # store a preview image for browsing
            qimg = np_gray_to_qimage(frame)
            qimg.save(str(self._current_scan_dir / "preview.png"))

        if self._scan_step_count >= self._scan_total_steps:
            # End scan -> begin reconstruction
            self._scan_timer.stop()
            self._is_scanning = False
            self._begin_reconstruction()

    def _begin_reconstruction(self):
        self._is_recon = True
        self.mode_label.setText("Reconstructing...")
        self.recon_progress.setValue(0)
        self._recon_timer.start(80)

    def _recon_tick(self):
        if not self._is_recon:
            return

        v = self.recon_progress.value()
        v = min(100, v + 2)
        self.recon_progress.setValue(v)

        # Simulate updating depth-dose plot during recon
        # (Replace with your real compute_dose_profile output)
        depth = np.linspace(0, 70, 141)
        # Make it evolve slightly with recon %
        k = 18.0 + 6.0 * (1.0 - v / 100.0)
        dose = np.exp(-depth / k)
        dose = dose / (dose.max() + 1e-9)
        # add small ripple
        dose = np.clip(dose * (1.0 + 0.03 * np.sin(depth * 0.35)), 0, 1)
        self.plot.set_data(depth, dose)

        if v >= 100:
            self._recon_timer.stop()
            self._is_recon = False
            self.mode_label.setText("Done")
            self.start_stop_btn.setText("Start scan")

            # Simulate saving reconstruction outputs
            if self._current_scan_dir:
                out = {
                    "depth_mm": depth.tolist(),
                    "relative_dose": dose.tolist()
                }
                (self._current_scan_dir / "dose_depth.json").write_text(json.dumps(out, indent=2))

    def _open_export_dialog(self):
        dlg = ExportDialog(self)
        dlg.exec()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()