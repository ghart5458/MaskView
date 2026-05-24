from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QPointF
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)
import numpy as np


class VolumeViewer(QWidget):
    """Single-panel volume viewer: scrollable slices, click-to-zoom, drag-to-pan."""

    slice_changed  = pyqtSignal(int)
    zoom_changed   = pyqtSignal(float)
    pan_changed    = pyqtSignal(float, float)
    cursor_moved   = pyqtSignal(float, float)   # scene x, y
    cursor_left    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: np.ndarray | None = None
        self._display_lo = 0.0
        self._display_hi = 1.0
        self._current_z = 0
        self._orientation = "XY"
        self._threshold_active = False
        self._threshold_lo     = 0.0
        self._threshold_hi     = 1.0
        self._threshold_rgb    = [0, 200, 160]    # turquoise default
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
        self._view.zoom_changed.connect(self._on_view_zoom)
        self._view.pan_changed.connect(self.pan_changed)
        self._view.wheel_scroll.connect(self._on_wheel_scroll)
        self._view.cursor_moved.connect(self.cursor_moved)
        self._view.cursor_left.connect(self.cursor_left)
        layout.addWidget(self._view, stretch=1)

        bar_widget = QWidget()
        bar_widget.setContentsMargins(0, 0, 0, 0)
        bar_widget.setFixedHeight(28)
        bar = QHBoxLayout(bar_widget)
        bar.setContentsMargins(4, 4, 4, 4)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
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
        bar.addWidget(self._slider, stretch=1)

        self._info = QLabel("—")
        self._info.setFixedWidth(140)
        self._info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._info.setStyleSheet("color: #aaa; font-size: 12px;")
        bar.addWidget(self._info)

        layout.addWidget(bar_widget)

    def load(self, data: np.ndarray, display_lo: float, display_hi: float):
        self._data = data
        self._display_lo = float(display_lo)
        self._display_hi = float(display_hi)
        n = self._n_slices()
        self._slider.setMaximum(n - 1)
        self._current_z = n // 2
        self._slider.blockSignals(True)
        self._slider.setValue(self._current_z)
        self._slider.blockSignals(False)
        self._update_slice()
        QTimer.singleShot(0, self._fit_to_view)

    def set_slice(self, z: int):
        """Jump to slice z without emitting slice_changed (used by sync system)."""
        if self._data is None:
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
        """Set zoom to exact factor without emitting zoom_changed (used by sync)."""
        self._view.set_zoom(factor)
        self._update_info()

    def set_pan(self, x: float, y: float):
        """Center viewport on scene point (x, y) without emitting pan_changed."""
        self._view.set_center(x, y)

    def set_external_cursor(self, x: float, y: float):
        self._view.set_external_cursor(x, y)

    def clear_external_cursor(self):
        self._view.clear_external_cursor()

    def set_display_range(self, lo: float, hi: float):
        self._display_lo = float(lo)
        self._display_hi = float(hi)
        self._update_slice()

    def set_threshold(self, lo: float, hi: float):
        self._threshold_lo     = lo
        self._threshold_hi     = hi
        self._threshold_active = True
        self._update_slice()

    def clear_threshold(self):
        self._threshold_active = False
        self._update_slice()

    def set_threshold_color(self, r: int, g: int, b: int):
        self._threshold_rgb = [r, g, b]
        if self._threshold_active:
            self._update_slice()

    @property
    def data(self) -> "np.ndarray | None":
        return self._data

    @property
    def display_range(self) -> tuple[float, float]:
        return self._display_lo, self._display_hi

    @property
    def current_slice_2d(self) -> "np.ndarray | None":
        return self._get_slice_2d() if self._data is not None else None

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

    def _on_slider_changed(self, value: int):
        self._current_z = value
        self._update_slice()
        self.slice_changed.emit(value)

    def _on_wheel_scroll(self, delta: int):
        if self._data is None:
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

    def _on_view_zoom(self, factor: float):
        self._update_info()
        self.zoom_changed.emit(factor)

    def _fit_to_view(self):
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_info()

    def set_orientation(self, orientation: str):
        """Switch between XY / XZ / YZ planes. Silent — no signals emitted."""
        if orientation == self._orientation:
            return
        self._orientation = orientation
        if self._data is None:
            return
        n = self._n_slices()
        self._current_z = n // 2
        self._slider.setMaximum(n - 1)
        self._slider.blockSignals(True)
        self._slider.setValue(self._current_z)
        self._slider.blockSignals(False)
        self._update_slice()
        QTimer.singleShot(0, self._fit_to_view)

    def _n_slices(self) -> int:
        if self._data is None:
            return 1
        return self._data.shape[{"XY": 0, "XZ": 1, "YZ": 2}[self._orientation]]

    def _get_slice_2d(self) -> "np.ndarray":
        z = self._current_z
        if self._orientation == "XY":
            return self._data[z]
        elif self._orientation == "XZ":
            return self._data[:, z, :]
        else:
            return self._data[:, :, z]

    def _update_slice(self):
        if self._data is None:
            return
        self._pixmap_item.setPixmap(self._to_pixmap(self._get_slice_2d()))
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._update_info()

    def _to_pixmap(self, slice_2d: np.ndarray) -> QPixmap:
        lo, hi = self._display_lo, self._display_hi
        norm = np.clip((slice_2d.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
        gray = (norm * 255).astype(np.uint8)
        h, w = gray.shape

        if not self._threshold_active:
            img = QImage(gray.tobytes(), w, h, w, QImage.Format.Format_Grayscale8)
            return QPixmap.fromImage(img)

        # RGB: grayscale base + highlight color for in-range pixels.
        # np.where is fully vectorized; avoids boolean scatter writes.
        tlo, thi   = self._threshold_lo, self._threshold_hi
        in_range   = (slice_2d >= tlo) & (slice_2d <= thi)
        tr, tg, tb = self._threshold_rgb
        rgb = np.stack([
            np.where(in_range, np.uint8(tr), gray),
            np.where(in_range, np.uint8(tg), gray),
            np.where(in_range, np.uint8(tb), gray),
        ], axis=-1)
        raw = rgb.tobytes()
        img = QImage(raw, w, h, w * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(img)

    def _update_info(self):
        if self._data is None:
            self._info.setText("—")
            return
        zoom = self._view.transform().m11()
        pct = round(zoom * 100, 1)
        pct_str = f"{int(pct)}%" if pct == int(pct) else f"{pct:.1f}%"
        n = self._n_slices()
        self._info.setText(
            f"{self._orientation}  {self._current_z + 1}/{n}   {pct_str}"
        )


class _PanZoomView(QGraphicsView):
    """QGraphicsView with click-to-zoom and drag-to-pan.

    Left click (no drag)  → zoom in
    Right click           → zoom out
    Left click + drag     → pan  (distinguished from click by > 5 px movement)
    Scroll wheel          → slice navigation (forwarded to VolumeViewer)
    """

    zoom_changed  = pyqtSignal(float)
    pan_changed   = pyqtSignal(float, float)
    wheel_scroll  = pyqtSignal(int)
    cursor_moved  = pyqtSignal(float, float)   # scene x, y
    cursor_left   = pyqtSignal()

    # Discrete zoom levels matching FIJI's sequence
    _ZOOM_LEVELS = (
        1/72, 1/48, 1/32, 1/24, 1/16, 1/12,
        1/8,  1/6,  1/4,  1/3,  1/2,  3/4,
        1.0,  1.5,  2.0,  3.0,  4.0,  6.0,
        8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0,
    )
    _PAN_THRESHOLD = 5

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("""
            QScrollBar:horizontal {
                height: 8px; background: #111; border: none; margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #555; border-radius: 0px; min-width: 24px;
            }
            QScrollBar::handle:horizontal:hover { background: #888; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar:vertical {
                width: 8px; background: #111; border: none; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #555; border-radius: 0px; min-height: 24px;
            }
            QScrollBar::handle:vertical:hover { background: #888; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        # Nearest-neighbor: preserve sharp voxel edges when zoomed in
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        self._drag_start: QPoint | None = None
        self._last_pos: QPoint | None = None
        self._is_panning = False
        self._ext_cursor: QPointF | None = None

        # Required so mouseMoveEvent fires during hover (not just button-press)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self._last_pos = event.pos()
            self._is_panning = False
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._apply_zoom(-1, event.pos())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start is not None:
            delta = event.pos() - self._drag_start
            if not self._is_panning and (abs(delta.x()) > self._PAN_THRESHOLD
                                          or abs(delta.y()) > self._PAN_THRESHOLD):
                self._is_panning = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.cursor_left.emit()  # clear marker in other panels while panning
            if self._is_panning and self._last_pos is not None:
                move = event.pos() - self._last_pos
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - move.x())
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - move.y())
                c = self.mapToScene(self.viewport().rect().center())
                self.pan_changed.emit(c.x(), c.y())
            self._last_pos = event.pos()
            event.accept()
        else:
            sp = self.mapToScene(event.pos())
            self.cursor_moved.emit(sp.x(), sp.y())
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_panning and self._drag_start is not None:
                self._apply_zoom(+1, self._drag_start)
            self._is_panning = False
            self._drag_start = None
            self._last_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        self.wheel_scroll.emit(event.angleDelta().y())

    def set_zoom(self, factor: float):
        """Set zoom to exact factor, keeping the current viewport center fixed."""
        current = self.transform().m11()
        if abs(current - factor) < 1e-6:
            return
        center = self.mapToScene(self.viewport().rect().center())
        self.scale(factor / current, factor / current)
        self.centerOn(center)

    def set_center(self, x: float, y: float):
        """Pan so that scene point (x, y) is at the viewport center."""
        self.centerOn(x, y)

    def leaveEvent(self, event):
        self.cursor_left.emit()
        super().leaveEvent(event)

    def set_external_cursor(self, x: float, y: float):
        self._ext_cursor = QPointF(x, y)
        self.viewport().update()

    def clear_external_cursor(self):
        if self._ext_cursor is not None:
            self._ext_cursor = None
            self.viewport().update()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self._ext_cursor is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y = self._ext_cursor.x(), self._ext_cursor.y()
        arm = 12 / max(self.transform().m11(), 0.001)
        pen = QPen(QColor(0, 210, 255, 255))
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.drawLine(QPointF(x - arm, y), QPointF(x + arm, y))
        painter.drawLine(QPointF(x, y - arm), QPointF(x, y + arm))

    def _next_zoom(self, direction: int) -> float:
        """Return the next discrete zoom level up (+1) or down (-1) from current."""
        current = self.transform().m11()
        if direction > 0:
            for level in self._ZOOM_LEVELS:
                if level > current * 1.001:
                    return level
            return self._ZOOM_LEVELS[-1]
        else:
            for level in reversed(self._ZOOM_LEVELS):
                if level < current * 0.999:
                    return level
            return self._ZOOM_LEVELS[0]

    def _apply_zoom(self, direction: int, view_pos: QPoint):
        """Zoom in (direction=+1) or out (direction=-1), snapping to discrete levels."""
        target = self._next_zoom(direction)
        current = self.transform().m11()
        factor = target / current
        if abs(factor - 1.0) < 1e-4:
            return

        # Keep the scene point under view_pos stationary during zoom.
        scene_pos = self.mapToScene(view_pos)
        self.scale(factor, factor)
        new_view_pos = self.mapFromScene(scene_pos)
        shift = new_view_pos - view_pos
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + shift.x())
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + shift.y())

        self.zoom_changed.emit(self.transform().m11())
        c = self.mapToScene(self.viewport().rect().center())
        self.pan_changed.emit(c.x(), c.y())
