from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QPointF
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QWheelEvent, QMouseEvent
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

    slice_changed       = pyqtSignal(int)
    zoom_changed        = pyqtSignal(float)
    pan_changed         = pyqtSignal(float, float)
    cursor_moved        = pyqtSignal(float, float)   # scene x, y
    cursor_left         = pyqtSignal()
    view_clicked        = pyqtSignal()               # any non-pan left click
    tag_place_requested = pyqtSignal(int, int, int)  # voxel x, y, z
    tag_edit_requested  = pyqtSignal(str)            # tag_id
    anchor_clicked      = pyqtSignal(float, float)   # scene x, y

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
        self._threshold_rgb    = [44, 230, 127]    # theme green (#2ce67f)
        self._tags: list       = []
        self._show_tags        = True
        self._turbo_step       = 1
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
        self._view.view_clicked.connect(self.view_clicked)
        self._view.tag_scene_clicked.connect(self._on_tag_scene_clicked)
        self._view.tag_marker_clicked.connect(self.tag_edit_requested)
        self._view.anchor_scene_clicked.connect(self.anchor_clicked)
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

    def set_tags(self, tags: list, show: bool | None = None):
        self._tags = list(tags)
        if show is not None:
            self._show_tags = show
        self._update_tag_markers()

    def set_tags_visible(self, visible: bool):
        self._show_tags = visible
        self._update_tag_markers()

    def set_tag_mode(self, active: bool):
        self._view.set_tag_mode(active)

    def set_anchor_mode(self, active: bool):
        self._view.set_anchor_mode(active)

    def set_anchor_marker(self, x: float, y: float, confirmed: bool):
        self._view.set_anchor_marker(x, y, confirmed)

    def clear_anchor_marker(self):
        self._view.clear_anchor_marker()

    def highlight_tag(self, tag_id: str):
        self._view.highlight_tag(tag_id)

    def jump_to_slice(self, z: int):
        """Navigate to slice z and emit slice_changed (triggers sync across panels)."""
        if self._data is None:
            return
        z = max(0, min(z, self._n_slices() - 1))
        self._slider.setValue(z)

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

    def _update_tag_markers(self):
        if not self._show_tags or self._data is None:
            self._view.set_tag_markers([])
            return
        s = self._turbo_step
        z = self._current_z
        markers = []
        for tag in self._tags:
            if self._orientation == "XY":
                if tag.z // s == z:
                    markers.append((tag.x // s, tag.y // s, tag.color, tag.id, tag.note))
            elif self._orientation == "XZ":
                if tag.y // s == z:
                    markers.append((tag.x // s, tag.z // s, tag.color, tag.id, tag.note))
            else:  # YZ
                if tag.x // s == z:
                    markers.append((tag.y // s, tag.z // s, tag.color, tag.id, tag.note))
        self._view.set_tag_markers(markers)

    def _on_tag_scene_clicked(self, sx: float, sy: float):
        if self._data is None:
            return
        s = self._turbo_step
        z_dim, y_dim, x_dim = self._data.shape
        z = self._current_z
        if self._orientation == "XY":
            x  = max(0, min(int(sx) * s, x_dim * s - 1))
            y  = max(0, min(int(sy) * s, y_dim * s - 1))
            vz = z * s
        elif self._orientation == "XZ":
            x  = max(0, min(int(sx) * s, x_dim * s - 1))
            y  = z * s
            vz = max(0, min(int(sy) * s, z_dim * s - 1))
        else:  # YZ
            x  = z * s
            y  = max(0, min(int(sx) * s, y_dim * s - 1))
            vz = max(0, min(int(sy) * s, z_dim * s - 1))
        self.tag_place_requested.emit(x, y, vz)

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

    def set_turbo_step(self, step: int):
        self._turbo_step = max(1, step)

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
        self._update_tag_markers()

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

    zoom_changed        = pyqtSignal(float)
    pan_changed         = pyqtSignal(float, float)
    wheel_scroll        = pyqtSignal(int)
    cursor_moved        = pyqtSignal(float, float)   # scene x, y
    cursor_left         = pyqtSignal()
    view_clicked        = pyqtSignal()               # any non-pan left click
    tag_scene_clicked   = pyqtSignal(float, float)   # scene x, y for new tag placement
    tag_marker_clicked  = pyqtSignal(str)            # tag_id for edit
    anchor_scene_clicked = pyqtSignal(float, float)  # scene x, y for anchor placement

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
        self._tag_markers: list = []   # list of (col, row, hex_color, tag_id, note)
        self._tag_mode = False
        self._highlight_tag_id: str | None = None
        self._highlight_frame = 0
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(70)
        self._highlight_timer.timeout.connect(self._highlight_tick)

        self._tooltip_tag_id: str | None = None
        self._tag_tip = QLabel("", self.viewport())
        self._tag_tip.setStyleSheet(
            "QLabel { background: #1e1e1e; color: #ddd; border: 1px solid #444;"
            " border-radius: 3px; padding: 3px 6px; font-size: 12px; }"
        )
        self._tag_tip.setVisible(False)
        self._tag_tip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._anchor_mode = False
        self._anchor_marker: QPointF | None = None
        self._anchor_confirmed = False

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
            self.view_clicked.emit()
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

            tip = ""
            hit_id = None
            for col, row, _color, tag_id, note in self._tag_markers:
                tag_vp = self.mapFromScene(QPointF(col, row))
                dx = event.pos().x() - tag_vp.x()
                dy = event.pos().y() - tag_vp.y()
                if dx * dx + dy * dy < 14 ** 2:
                    tip = note
                    hit_id = tag_id
                    break
            if hit_id != self._tooltip_tag_id:
                self._tooltip_tag_id = hit_id
                if tip:
                    self._tag_tip.setText(tip)
                    self._tag_tip.adjustSize()
                    pos = event.pos() + QPoint(12, 12)
                    vp = self.viewport()
                    if pos.x() + self._tag_tip.width() > vp.width():
                        pos.setX(event.pos().x() - self._tag_tip.width() - 4)
                    if pos.y() + self._tag_tip.height() > vp.height():
                        pos.setY(event.pos().y() - self._tag_tip.height() - 4)
                    self._tag_tip.move(pos)
                    self._tag_tip.show()
                    self._tag_tip.raise_()
                else:
                    self._tag_tip.hide()

            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_panning and self._drag_start is not None:
                self.view_clicked.emit()
                if self._anchor_mode:
                    sp = self.mapToScene(self._drag_start)
                    self.anchor_scene_clicked.emit(sp.x(), sp.y())
                elif self._tag_mode:
                    click_vp = self._drag_start
                    hit_id = None
                    for col, row, _color, tag_id, _note in self._tag_markers:
                        tag_vp = self.mapFromScene(QPointF(col, row))
                        dx = click_vp.x() - tag_vp.x()
                        dy = click_vp.y() - tag_vp.y()
                        if dx * dx + dy * dy < 14 ** 2:
                            hit_id = tag_id
                            break
                    if hit_id is not None:
                        self.tag_marker_clicked.emit(hit_id)
                    else:
                        sp = self.mapToScene(click_vp)
                        self.tag_scene_clicked.emit(sp.x(), sp.y())
                else:
                    self._apply_zoom(+1, self._drag_start)
            self._is_panning = False
            self._drag_start = None
            self._last_pos = None
            self.setCursor(
                Qt.CursorShape.CrossCursor
                if (self._tag_mode or self._anchor_mode)
                else Qt.CursorShape.ArrowCursor
            )
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
        self._tooltip_tag_id = None
        self._tag_tip.hide()
        self.cursor_left.emit()
        super().leaveEvent(event)

    def set_external_cursor(self, x: float, y: float):
        self._ext_cursor = QPointF(x, y)
        self.viewport().update()

    def clear_external_cursor(self):
        if self._ext_cursor is not None:
            self._ext_cursor = None
            self.viewport().update()

    def set_tag_markers(self, markers: list):
        self._tag_markers = markers
        self.viewport().update()

    def highlight_tag(self, tag_id: str):
        self._highlight_tag_id = tag_id
        self._highlight_frame  = 16
        self._highlight_timer.start()
        self.viewport().update()

    def _highlight_tick(self):
        self._highlight_frame -= 1
        self.viewport().update()
        if self._highlight_frame <= 0:
            self._highlight_timer.stop()
            self._highlight_tag_id = None

    def set_tag_mode(self, active: bool):
        self._tag_mode = active
        self.setCursor(Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._ext_cursor is not None:
            x, y = self._ext_cursor.x(), self._ext_cursor.y()
            arm = 12 / max(self.transform().m11(), 0.001)
            pen = QPen(QColor(0, 210, 255, 255))
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.drawLine(QPointF(x - arm, y), QPointF(x + arm, y))
            painter.drawLine(QPointF(x, y - arm), QPointF(x, y + arm))

        if self._tag_markers:
            zoom = max(self.transform().m11(), 0.001)
            r = 7 / zoom
            border_pen = QPen(QColor(255, 255, 255, 200))
            border_pen.setCosmetic(True)
            border_pen.setWidthF(2.0)
            painter.setPen(border_pen)
            for col, row, hex_color, _tag_id, _note in self._tag_markers:
                color = QColor(hex_color)
                painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 200)))
                painter.drawEllipse(QPointF(col, row), r, r)

            if self._highlight_tag_id and self._highlight_frame > 0:
                zoom = max(self.transform().m11(), 0.001)
                frac = self._highlight_frame / 16
                ring_r = (7 + (1.0 - frac) * 16) / zoom
                alpha  = int(frac * 230)
                ring_pen = QPen(QColor(255, 255, 255, alpha))
                ring_pen.setCosmetic(True)
                ring_pen.setWidthF(2.5)
                painter.setPen(ring_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for col, row, _color, tag_id, _note in self._tag_markers:
                    if tag_id == self._highlight_tag_id:
                        painter.drawEllipse(QPointF(col, row), ring_r, ring_r)
                        break

        if self._anchor_marker is not None:
            zoom = max(self.transform().m11(), 0.001)
            ax, ay = self._anchor_marker.x(), self._anchor_marker.y()
            r = 7 / zoom
            fill = QColor(44, 230, 127, 230) if self._anchor_confirmed else QColor(255, 200, 0, 230)
            border_pen = QPen(QColor(255, 255, 255, 200))
            border_pen.setCosmetic(True)
            border_pen.setWidthF(2.0)
            painter.setPen(border_pen)
            painter.setBrush(QBrush(fill))
            painter.drawEllipse(QPointF(ax, ay), r, r)

    def set_anchor_mode(self, active: bool):
        self._anchor_mode = active
        self.setCursor(
            Qt.CursorShape.CrossCursor if active else
            (Qt.CursorShape.CrossCursor if self._tag_mode else Qt.CursorShape.ArrowCursor)
        )

    def set_anchor_marker(self, x: float, y: float, confirmed: bool):
        self._anchor_marker = QPointF(x, y)
        self._anchor_confirmed = confirmed
        self.viewport().update()

    def clear_anchor_marker(self):
        self._anchor_marker = None
        self._anchor_confirmed = False
        self.viewport().update()

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
