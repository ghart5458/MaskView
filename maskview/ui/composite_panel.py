from dataclasses import dataclass, field

import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsScene, QHBoxLayout,
    QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from .viewer import _PanZoomView

COMPOSITE_TYPE = "__composite__"


@dataclass
class OverlaySpec:
    file_types: list[str]
    colors:     list[tuple[int, int, int]]
    opacities:  list[float]
    replaces:   str | None
    blend_mode:     str  = "screen"
    data:           dict = field(default_factory=dict)   # ft -> np.ndarray
    display_ranges: dict = field(default_factory=dict)   # ft -> (lo, hi)


class CompositeViewer(QWidget):
    """Additive RGB composite viewer — per-slice blending, same sync API as VolumeViewer."""

    slice_changed = pyqtSignal(int)
    zoom_changed  = pyqtSignal(float)
    pan_changed   = pyqtSignal(float, float)
    cursor_moved  = pyqtSignal(float, float)
    cursor_left   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channels: list[tuple] = []  # (data, lo, hi, (r,g,b), opacity)
        self._current_z  = 0
        self._orientation = "XY"
        self._blend_mode  = "screen"
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._scene = QGraphicsScene(self)
        self._scene.setBackgroundBrush(Qt.GlobalColor.black)
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)

        self._view = _PanZoomView(self._scene, self)
        self._view.zoom_changed.connect(self.zoom_changed)
        self._view.pan_changed.connect(self.pan_changed)
        self._view.wheel_scroll.connect(self._on_wheel_scroll)
        self._view.cursor_moved.connect(self.cursor_moved)
        self._view.cursor_left.connect(self.cursor_left)
        layout.addWidget(self._view, stretch=1)

        bar = QWidget()
        bar.setFixedHeight(28)
        brow = QHBoxLayout(bar)
        brow.setContentsMargins(4, 4, 4, 4)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(0)
        self._slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px; background: #2a2a2a; border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #8a8a8a; border-radius: 2px;
            }
            QSlider::handle:horizontal:hover { background: #bbb; }
            QSlider::sub-page:horizontal { background: #3a3a3a; border-radius: 2px; }
        """)
        self._slider.valueChanged.connect(self._on_slider_changed)
        brow.addWidget(self._slider, stretch=1)
        self._info = QLabel("—")
        self._info.setFixedWidth(140)
        self._info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._info.setStyleSheet("color: #aaa; font-size: 12px;")
        brow.addWidget(self._info)
        layout.addWidget(bar)

    # ── Public API (mirrors VolumeViewer for sync compatibility) ──────────────

    def set_channels(self, channels: list[tuple]):
        """channels: list of (data, lo, hi, (r,g,b), opacity)"""
        self._channels = channels
        if not channels:
            return
        n = self._n_slices()
        self._current_z = n // 2
        self._slider.setMaximum(n - 1)
        self._slider.blockSignals(True)
        self._slider.setValue(self._current_z)
        self._slider.blockSignals(False)
        self._update_slice()
        QTimer.singleShot(0, self._fit_to_view)

    def update_channels(self, channels: list[tuple]):
        """Replace channel data and re-render the current slice (no position reset)."""
        self._channels = channels
        if channels:
            self._update_slice()

    def set_blend_mode(self, mode: str):
        if mode == self._blend_mode:
            return
        self._blend_mode = mode
        if self._channels:
            self._update_slice()

    def set_slice(self, z: int):
        if not self._channels:
            return
        z = max(0, min(z, self._n_slices() - 1))
        if z == self._current_z:
            return
        self._current_z = z
        self._slider.blockSignals(True)
        self._slider.setValue(z)
        self._slider.blockSignals(False)
        self._update_slice()

    def set_zoom(self, factor: float):
        self._view.set_zoom(factor)
        self._update_info()

    def set_pan(self, x: float, y: float):
        self._view.set_center(x, y)

    def set_external_cursor(self, x: float, y: float):
        self._view.set_external_cursor(x, y)

    def clear_external_cursor(self):
        self._view.clear_external_cursor()

    def set_orientation(self, orientation: str):
        if orientation == self._orientation:
            return
        self._orientation = orientation
        if not self._channels:
            return
        n = self._n_slices()
        self._current_z = n // 2
        self._slider.setMaximum(n - 1)
        self._slider.blockSignals(True)
        self._slider.setValue(self._current_z)
        self._slider.blockSignals(False)
        self._update_slice()
        QTimer.singleShot(0, self._fit_to_view)

    @property
    def data(self):
        return None  # composite has no single source array

    @property
    def display_range(self) -> tuple[float, float]:
        return 0.0, 1.0

    @property
    def current_slice(self) -> int:
        return self._current_z

    @property
    def current_zoom(self) -> float:
        return self._view.transform().m11()

    @property
    def current_pan(self) -> tuple[float, float]:
        c = self._view.mapToScene(self._view.viewport().rect().center())
        return c.x(), c.y()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _n_slices(self) -> int:
        if not self._channels:
            return 1
        return self._channels[0][0].shape[
            {"XY": 0, "XZ": 1, "YZ": 2}[self._orientation]
        ]

    def _get_slice_2d(self, data: np.ndarray) -> np.ndarray:
        z = self._current_z
        if self._orientation == "XY":
            return data[z]
        elif self._orientation == "XZ":
            return data[:, z, :]
        else:
            return data[:, :, z]

    def _on_slider_changed(self, value: int):
        self._current_z = value
        self._update_slice()
        self.slice_changed.emit(value)

    def _on_wheel_scroll(self, delta: int):
        if not self._channels:
            return
        step = max(1, abs(delta) // 120)
        new_z = self._current_z + (-step if delta > 0 else step)
        new_z = max(0, min(new_z, self._n_slices() - 1))
        if new_z != self._current_z:
            self._current_z = new_z
            self._slider.blockSignals(True)
            self._slider.setValue(new_z)
            self._slider.blockSignals(False)
            self._update_slice()
            self.slice_changed.emit(new_z)

    def _fit_to_view(self):
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_info()

    def _update_slice(self):
        if not self._channels:
            return
        self._pixmap_item.setPixmap(self._composite_pixmap())
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._update_info()

    def _composite_pixmap(self) -> QPixmap:
        slc0 = self._get_slice_2d(self._channels[0][0])
        h, w = slc0.shape

        if self._blend_mode == "alpha":
            # Porter-Duff "over": each channel is composited on top of the previous result.
            # Channels with higher intensity and opacity occlude what's beneath them.
            rgb = np.zeros((h, w, 3), dtype=np.float32)
            for data, lo, hi, color, opacity in self._channels:
                slc = self._get_slice_2d(data)
                drange = max(float(hi) - float(lo), 1.0)
                norm = np.clip((slc.astype(np.float32) - lo) / drange, 0.0, 1.0)
                r, g, b = color
                alpha = (norm * opacity)[:, :, None]
                src = np.stack(
                    [norm * (r / 255.0), norm * (g / 255.0), norm * (b / 255.0)], axis=2
                )
                rgb = src * alpha + rgb * (1.0 - alpha)
            out = (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
        else:
            # Screen blending: result = 1 - ∏(1 - channel_i).
            # Accumulate the product of (1 - contribution) terms, then invert.
            acc = np.ones((h, w, 3), dtype=np.float32)
            for data, lo, hi, color, opacity in self._channels:
                slc = self._get_slice_2d(data)
                drange = max(float(hi) - float(lo), 1.0)
                norm = np.clip((slc.astype(np.float32) - lo) / drange, 0.0, 1.0)
                r, g, b = color
                c = norm * opacity
                acc[:, :, 0] *= 1.0 - c * (r / 255.0)
                acc[:, :, 1] *= 1.0 - c * (g / 255.0)
                acc[:, :, 2] *= 1.0 - c * (b / 255.0)
            out = ((1.0 - acc) * 255).astype(np.uint8)

        img = QImage(out.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def _update_info(self):
        if not self._channels:
            self._info.setText("—")
            return
        zoom = self._view.transform().m11()
        pct = round(zoom * 100, 1)
        pct_str = f"{int(pct)}%" if pct == int(pct) else f"{pct:.1f}%"
        n = self._n_slices()
        self._info.setText(f"{self._orientation}  {self._current_z + 1}/{n}   {pct_str}")


class CompositePanel(QWidget):
    """Title bar + CompositeViewer. Participates in sync exactly like ViewerPanel."""

    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setObjectName("compTitleBar")
        title_bar.setFixedHeight(30)
        title_bar.setStyleSheet("#compTitleBar { background: #2d2d2d; }")
        bar = QHBoxLayout(title_bar)
        bar.setContentsMargins(8, 3, 4, 3)
        lbl = QLabel("Color Composite")
        lbl.setStyleSheet(
            "color: #dddddd; font-weight: bold; font-size: 12px; background: transparent;"
        )
        bar.addWidget(lbl, stretch=1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: none; font-size: 11px;"
            " background: transparent; }"
            "QPushButton:hover { color: white; background: #c0392b; border-radius: 3px; }"
        )
        close_btn.clicked.connect(self.closed)
        bar.addWidget(close_btn)
        layout.addWidget(title_bar)

        self._viewer = CompositeViewer()
        layout.addWidget(self._viewer, stretch=1)

    @property
    def file_type(self) -> str:
        return COMPOSITE_TYPE

    @property
    def viewer(self) -> CompositeViewer:
        return self._viewer

    def start_loading(self): pass
    def stop_loading(self): pass
    def load(self, *_): pass
