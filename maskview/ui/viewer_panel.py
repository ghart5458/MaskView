from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
import numpy as np

from ..files.resolver import FILE_TYPE_LABELS
from .viewer import VolumeViewer


_OVERLAY_BG      = "#1a1a1a"
_OVERLAY_BORDER  = "#444"
_HIDE_MS         = 350
_DEFAULT_THR_RGB = (0, 200, 160)   # turquoise


# ── Hover event filter ────────────────────────────────────────────────────────

class _HoverFilter(QObject):
    def __init__(self, on_enter, on_leave, parent=None):
        super().__init__(parent)
        self._on_enter = on_enter
        self._on_leave = on_leave

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            self._on_enter()
        elif event.type() == QEvent.Type.Leave:
            self._on_leave()
        return False


# ── Histogram canvas ──────────────────────────────────────────────────────────

class _HistCanvas(QWidget):
    """Log-scale histogram bars with lo/hi marker lines and axis tick labels."""

    _TICK_H = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 100 + self._TICK_H)
        self._bins: np.ndarray | None   = None
        self._counts: np.ndarray | None = None
        self._lo = 0.0
        self._hi = 1.0

    def refresh(self, data: np.ndarray, lo: float, hi: float):
        # Subsample large arrays — histogram shape is robust to modest sampling
        if data.size > 300_000:
            step = max(1, int((data.size / 300_000) ** (1 / max(1, data.ndim))))
            slices = tuple(slice(None, None, step) for _ in range(data.ndim))
            sample = data[slices]
        else:
            sample = data
        flat = sample.ravel().astype(np.float32)
        counts, edges = np.histogram(flat, bins=128)
        self._bins   = edges
        self._counts = counts.astype(np.float64)
        self._lo     = lo
        self._hi     = hi
        self.update()

    def paintEvent(self, event):
        if self._counts is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w, h    = self.width(), self.height()
        bar_h   = h - self._TICK_H
        d_min   = float(self._bins[0])
        d_max   = float(self._bins[-1])
        d_range = d_max - d_min or 1.0

        p.fillRect(0, 0, w, h, QColor(_OVERLAY_BG))

        # Bars
        log_c   = np.log1p(self._counts)
        max_log = log_c.max() or 1.0
        n       = len(log_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#555"))
        for i, lc in enumerate(log_c):
            bh = int(lc / max_log * (bar_h - 2))
            bx = int(i * w / n)
            bw = max(1, int((i + 1) * w / n) - bx)
            p.drawRect(bx, bar_h - bh, bw, bh)

        # lo / hi marker lines (clamped to bar area)
        pen = QPen(QColor("#2ce67f"))
        pen.setWidth(1)
        p.setPen(pen)
        for val in (self._lo, self._hi):
            x = int((val - d_min) / d_range * w)
            p.drawLine(x, 0, x, bar_h)

        # Axis ticks + labels at 0 %, 25 %, 50 %, 75 %, 100 %
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        fm = p.fontMetrics()
        p.setPen(QColor("#999"))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            val   = d_min + frac * d_range
            x_pos = int(frac * (w - 1))
            label = f"{val:.3g}"
            tw    = fm.horizontalAdvance(label)
            tx    = max(0, min(x_pos - tw // 2, w - tw))
            p.drawLine(x_pos, bar_h, x_pos, bar_h + 3)
            p.drawText(tx, h - 2, label)

        p.end()


# ── Histogram overlay ─────────────────────────────────────────────────────────

class _HistogramOverlay(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("histOverlay")
        self.setStyleSheet(
            f"#histOverlay {{ background: {_OVERLAY_BG}; border: 1px solid {_OVERLAY_BORDER};"
            f" border-radius: 4px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(236)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._canvas = _HistCanvas()
        layout.addWidget(self._canvas)

        self._full_stack_cb = QCheckBox("Full stack")
        self._full_stack_cb.setStyleSheet("color: #ccc; font-size: 12px;")
        self._full_stack_cb.setChecked(True)
        self._full_stack_cb.stateChanged.connect(self._draw)
        layout.addWidget(self._full_stack_cb)

        self.adjustSize()
        self.hide()

        self._stack_data: np.ndarray | None = None
        self._slice_data: np.ndarray | None = None
        self._lo = 0.0
        self._hi = 1.0

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_MS)
        self._hide_timer.timeout.connect(self.hide)

    def refresh(self, stack: "np.ndarray | None", slc: "np.ndarray | None",
                lo: float, hi: float):
        self._stack_data = stack
        self._slice_data = slc
        self._lo = lo
        self._hi = hi
        if self.isVisible():
            self._draw()

    def showEvent(self, event):
        super().showEvent(event)
        self._draw()  # render with latest cached data when becoming visible

    def _draw(self):
        data = self._stack_data if self._full_stack_cb.isChecked() else self._slice_data
        if data is not None:
            self._canvas.refresh(data, self._lo, self._hi)

    def start_hide(self):
        self._hide_timer.start()

    def cancel_hide(self):
        self._hide_timer.stop()

    def enterEvent(self, event):
        self.cancel_hide()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.start_hide()
        super().leaveEvent(event)


# ── Threshold overlay ─────────────────────────────────────────────────────────

class _ThresholdOverlay(QFrame):
    range_changed = pyqtSignal(float, float)
    toggled       = pyqtSignal(bool)
    color_changed = pyqtSignal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("thrOverlay")
        self.setStyleSheet(
            f"#thrOverlay {{ background: {_OVERLAY_BG}; border: 1px solid {_OVERLAY_BORDER};"
            f" border-radius: 4px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(236)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._data_min   = 0.0
        self._data_max   = 1.0
        self._lo         = 0.0
        self._hi         = 1.0
        self._stack_data: np.ndarray | None = None
        self._updating   = False
        self._color_rgb  = list(_DEFAULT_THR_RGB)

        # Active toggle + color picker
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        hl_lbl = QLabel("Highlight")
        hl_lbl.setStyleSheet("color: #ccc; font-size: 12px;")
        top_row.addWidget(hl_lbl, stretch=1)

        self._active_btn = QPushButton("OFF")
        self._active_btn.setCheckable(True)
        self._active_btn.setFixedSize(44, 22)
        self._active_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #666; border: 1px solid #444;"
            " border-radius: 3px; font-size: 11px; font-weight: bold; }"
            "QPushButton:checked { background: #0d3320; color: #2ce67f;"
            " border-color: #147a3f; }"
            "QPushButton:hover:!checked { background: #333; }"
            "QPushButton:hover:checked { background: #144a28; }"
        )
        self._active_btn.toggled.connect(self._on_active_toggled)
        top_row.addWidget(self._active_btn)

        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(22, 22)
        self._color_btn.setToolTip("Pick highlight color")
        self._color_btn.clicked.connect(self._pick_color)
        self._refresh_color_btn()
        top_row.addWidget(self._color_btn)
        layout.addLayout(top_row)

        _slider_style = """
            QSlider::groove:horizontal { height: 4px; background: #2a2a2a; border-radius: 2px; }
            QSlider::handle:horizontal {
                width: 14px; height: 14px; margin: -5px 0;
                background: #8a8a8a; border-radius: 2px;
            }
            QSlider::handle:horizontal:hover { background: #bbb; }
            QSlider::sub-page:horizontal { background: #3a3a3a; border-radius: 2px; }
        """
        _edit_style = (
            "QLineEdit { background: #222; color: #ccc; border: 1px solid #444;"
            " border-radius: 3px; font-size: 11px; padding: 1px 4px; }"
        )
        _label_style = "color: #ccc; font-size: 12px;"

        for name, attr in (("Min", "lo"), ("Max", "hi")):
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(name)
            lbl.setStyleSheet(_label_style)
            lbl.setFixedWidth(28)
            row.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)
            slider.setStyleSheet(_slider_style)
            row.addWidget(slider, stretch=1)
            box = QLineEdit()
            box.setFixedWidth(58)
            box.setStyleSheet(_edit_style)
            row.addWidget(box)
            layout.addLayout(row)
            setattr(self, f"_slider_{attr}", slider)
            setattr(self, f"_box_{attr}", box)

        self._slider_lo.valueChanged.connect(lambda v: self._slider_moved("lo", v))
        self._slider_hi.valueChanged.connect(lambda v: self._slider_moved("hi", v))
        self._box_lo.editingFinished.connect(lambda: self._box_edited("lo"))
        self._box_hi.editingFinished.connect(lambda: self._box_edited("hi"))

        otsu_btn = QPushButton("Auto (Otsu)")
        otsu_btn.setStyleSheet(
            "QPushButton { background: #333; color: #ccc; border: 1px solid #555;"
            " border-radius: 3px; font-size: 11px; padding: 3px 8px; }"
            "QPushButton:hover { background: #444; }"
        )
        otsu_btn.clicked.connect(self._apply_otsu)
        layout.addWidget(otsu_btn)

        self.adjustSize()
        self.hide()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_MS)
        self._hide_timer.timeout.connect(self.hide)

    # ── Public API ────────────────────────────────────────────────────────────

    def setup(self, data: np.ndarray, lo: float, hi: float):
        self._stack_data = data
        self._data_min   = float(data.min())
        self._data_max   = float(data.max())
        self._lo         = self._data_min
        self._hi         = self._data_max
        self._active_btn.setChecked(False)
        self._sync_controls()

    @property
    def is_active(self) -> bool:
        return self._active_btn.isChecked()

    @property
    def current_range(self) -> tuple[float, float]:
        return self._lo, self._hi

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_active_toggled(self, checked: bool):
        self._active_btn.setText("ON" if checked else "OFF")
        self.toggled.emit(checked)

    def _refresh_color_btn(self):
        r, g, b   = self._color_rgb
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {hex_color}; border: 1px solid #666; border-radius: 3px; }}"
            f"QPushButton:hover {{ border: 1px solid #aaa; }}"
        )

    def _pick_color(self):
        r, g, b = self._color_rgb
        chosen  = QColorDialog.getColor(QColor(r, g, b), self, "Highlight color")
        if chosen.isValid():
            self._color_rgb = [chosen.red(), chosen.green(), chosen.blue()]
            self._refresh_color_btn()
            self.color_changed.emit(*self._color_rgb)

    def _sync_controls(self):
        self._updating = True
        dmin, dmax = self._data_min, self._data_max
        drange     = dmax - dmin or 1.0

        def _to_int(v: float) -> int:
            return int((v - dmin) / drange * 1000)

        self._slider_lo.setValue(_to_int(self._lo))
        self._slider_hi.setValue(_to_int(self._hi))
        self._box_lo.setText(f"{self._lo:.0f}")
        self._box_hi.setText(f"{self._hi:.0f}")
        self._updating = False

    def _to_val(self, slider_int: int) -> float:
        drange = self._data_max - self._data_min or 1.0
        return float(round(self._data_min + slider_int / 1000.0 * drange))

    def _slider_moved(self, which: str, v: int):
        if self._updating:
            return
        val = self._to_val(v)
        if which == "lo":
            self._lo = val
            self._box_lo.setText(f"{val:.0f}")
        else:
            self._hi = val
            self._box_hi.setText(f"{val:.0f}")
        self.range_changed.emit(self._lo, self._hi)

    def _box_edited(self, which: str):
        if self._updating:
            return
        box = self._box_lo if which == "lo" else self._box_hi
        try:
            val = float(round(float(box.text())))
        except ValueError:
            return
        val = max(self._data_min, min(val, self._data_max))
        if which == "lo":
            self._lo = val
        else:
            self._hi = val
        self._sync_controls()
        self.range_changed.emit(self._lo, self._hi)

    def _apply_otsu(self):
        if self._stack_data is None:
            return
        thresh   = _otsu_threshold(self._stack_data)
        self._lo = thresh            # highlight brighter pixels (above Otsu threshold)
        self._hi = self._data_max
        self._sync_controls()
        self._active_btn.setChecked(True)
        self.range_changed.emit(self._lo, self._hi)

    def start_hide(self):
        self._hide_timer.start()

    def cancel_hide(self):
        self._hide_timer.stop()

    def enterEvent(self, event):
        self.cancel_hide()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.start_hide()
        super().leaveEvent(event)


def _otsu_threshold(data: np.ndarray) -> float:
    # Subsample in N-D before flattening — avoids allocating a large float32 array
    target = 200_000
    if data.size > target:
        step = max(1, int((data.size / target) ** (1 / max(1, data.ndim))))
        slices = tuple(slice(None, None, step) for _ in range(data.ndim))
        sample = data[slices]
    else:
        sample = data
    flat = sample.ravel().astype(np.float32)
    counts, edges = np.histogram(flat, bins=256)
    total = counts.sum()
    if total == 0:
        return float(edges[-1])
    centers  = (edges[:-1] + edges[1:]) / 2.0
    w0       = np.cumsum(counts) / total
    w1       = 1.0 - w0
    mu0      = np.cumsum(counts * centers) / (np.cumsum(counts) + 1e-9)
    mu_total = float((counts * centers).sum() / total)
    mu1      = np.where(w1 > 1e-9, (mu_total - w0 * mu0) / np.maximum(w1, 1e-9), 0.0)
    return float(centers[int(np.argmax(w0 * w1 * (mu0 - mu1) ** 2))])


# ── Loading spinner ───────────────────────────────────────────────────────────

class _LoadingSpinner(QWidget):
    """Rotating arc animation shown on empty panels while data loads."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._label = label
        self._timer = QTimer(self)
        self._timer.setInterval(16)   # ~60 fps
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def start(self):
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 4) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() // 2, self.height() // 2
        r = 28
        arc_top = cy - r - 14   # shift arc upward to leave room for label

        # Background ring
        p.setPen(QPen(QColor("#2a2a2a"), 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(cx - r, arc_top, r * 2, r * 2, 0, 360 * 16)

        # Animated foreground arc
        p.setPen(QPen(QColor("#2ce67f"), 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.drawArc(cx - r, arc_top, r * 2, r * 2,
                  -self._angle * 16, 270 * 16)

        # File type label below arc
        p.setPen(QColor("#666"))
        font = p.font()
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(0, cy + 22, self.width(), 20,
                   Qt.AlignmentFlag.AlignHCenter, self._label)


# ── ViewerPanel ───────────────────────────────────────────────────────────────

class ViewerPanel(QWidget):
    """VolumeViewer with title bar, close button, and floating hist/threshold overlays."""

    closed = pyqtSignal()

    _BTN_W      = 52
    _BTN_H      = 22
    _BTN_MARGIN = 20   # clears 8px scroll bar + comfortable padding

    def __init__(self, file_type: str, parent=None):
        super().__init__(parent)
        self._file_type = file_type
        self._filters: list[_HoverFilter] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(30)
        title_bar.setStyleSheet("#titleBar { background: #2d2d2d; }")
        bar = QHBoxLayout(title_bar)
        bar.setContentsMargins(8, 3, 4, 3)

        self._label = QLabel(FILE_TYPE_LABELS.get(file_type, file_type))
        self._label.setStyleSheet(
            "color: #dddddd; font-weight: bold; font-size: 12px; background: transparent;"
        )
        bar.addWidget(self._label, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: none; font-size: 11px; background: transparent; }"
            "QPushButton:hover { color: white; background: #c0392b; border-radius: 3px; }"
        )
        close_btn.clicked.connect(self.closed)
        bar.addWidget(close_btn)
        layout.addWidget(title_bar)

        # ── Viewer ───────────────────────────────────────────────────────────
        self._viewer = VolumeViewer()
        layout.addWidget(self._viewer, stretch=1)

        # ── Floating buttons (not in layout — positioned absolutely) ─────────
        _btn_style = (
            "QPushButton { background: rgba(30,30,30,180); color: #888;"
            " border: 1px solid #444; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: rgba(60,60,60,220); color: #eee; }"
        )
        self._hist_btn = QPushButton("Hist", self)
        self._hist_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._hist_btn.setStyleSheet(_btn_style)

        self._thr_btn = QPushButton("Thresh", self)
        self._thr_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._thr_btn.setStyleSheet(_btn_style)

        # ── Floating overlays (not in layout — positioned absolutely) ────────
        self._hist_overlay = _HistogramOverlay(self)
        self._thr_overlay  = _ThresholdOverlay(self)

        self._thr_overlay.range_changed.connect(self._on_threshold_range)
        self._thr_overlay.toggled.connect(self._on_threshold_toggled)
        self._thr_overlay.color_changed.connect(self._viewer.set_threshold_color)
        self._viewer.slice_changed.connect(self._refresh_hist)

        self._wire_btn(self._hist_btn, self._hist_overlay, self._thr_overlay)
        self._wire_btn(self._thr_btn,  self._thr_overlay,  self._hist_overlay)

        # Loading spinner (overlaid, hidden until start_loading is called)
        self._spinner = _LoadingSpinner(
            FILE_TYPE_LABELS.get(file_type, file_type), self
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, data: np.ndarray, lo: float, hi: float):
        self._viewer.load(data, lo, hi)
        self._viewer.clear_threshold()
        self._thr_overlay.setup(data, lo, hi)
        self._refresh_hist()

    def start_loading(self):
        self._position_spinner()
        self._spinner.raise_()
        self._spinner.start()

    def stop_loading(self):
        self._spinner.stop()

    @property
    def viewer(self) -> VolumeViewer:
        return self._viewer

    @property
    def file_type(self) -> str:
        return self._file_type

    # ── Internal ──────────────────────────────────────────────────────────────

    def _wire_btn(self, btn: QPushButton,
                  overlay: "_HistogramOverlay | _ThresholdOverlay",
                  other:   "_HistogramOverlay | _ThresholdOverlay"):
        def _enter():
            other.cancel_hide()
            other.hide()
            overlay.cancel_hide()
            if not overlay.isVisible():
                overlay.show()
                self._reposition_overlays()

        def _leave():
            overlay.start_hide()

        filt = _HoverFilter(_enter, _leave, btn)
        btn.installEventFilter(filt)
        self._filters.append(filt)

    def _on_threshold_range(self, lo: float, hi: float):
        if self._thr_overlay.is_active:
            self._viewer.set_threshold(lo, hi)

    def _on_threshold_toggled(self, active: bool):
        if active:
            lo, hi = self._thr_overlay.current_range
            self._viewer.set_threshold(lo, hi)
        else:
            self._viewer.clear_threshold()

    def _refresh_hist(self):
        self._hist_overlay.refresh(
            self._viewer.data,
            self._viewer.current_slice_2d,
            *self._viewer.display_range,
        )

    def _position_spinner(self):
        self._spinner.setGeometry(0, 30, self.width(), self.height() - 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_overlays()
        self._position_spinner()

    def _reposition_overlays(self):
        title_h = 30
        m       = self._BTN_MARGIN
        bw, bh  = self._BTN_W, self._BTN_H
        gap     = 4

        btn_x = self.width() - bw - m
        self._hist_btn.move(btn_x, title_h + m)
        self._thr_btn.move(btn_x,  title_h + m + bh + gap)

        overlay_y = title_h + m + 2 * bh + gap + 2
        for ov in (self._hist_overlay, self._thr_overlay):
            ox = max(0, self.width() - ov.width() - m)
            ov.move(ox, overlay_y)
