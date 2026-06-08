from PyQt6.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
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
from ..tags.store import TagStore
from .viewer import VolumeViewer


_OVERLAY_BG      = "#1a1a1a"
_OVERLAY_BORDER  = "#444"
_HIDE_MS         = 350
_DEFAULT_THR_RGB = (44, 230, 127)  # theme green (#2ce67f)


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
        self._full_stack_cb.setStyleSheet("color: #ccc; font-size: 13px;")
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


# ── Color swatch picker ───────────────────────────────────────────────────────

class _GrayscaleBtn(QPushButton):
    """Button with a black-to-~75%-gray gradient background and solid white text."""

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        grad = QLinearGradient(0, 0, r.width(), 0)
        grad.setColorAt(0.0, QColor(0, 0, 0))
        grad.setColorAt(1.0, QColor(191, 191, 191))
        p.fillRect(r, grad)
        p.setPen(QColor(85, 85, 85))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())


class _ColorSwatchPicker(QDialog):
    """Frameless grid of preset color swatches — click one to select and close."""

    _SWATCHES = [
        "#ffffff", "#c0c0c0", "#808080", "#404040", "#000000", "#2ce67f",
        "#ff0000", "#dc3c3c", "#ff8888", "#cc0000", "#880000", "#440000",
        "#ff8800", "#ffaa00", "#ffcc44", "#ffdd88", "#cc8800", "#886600",
        "#00ff00", "#44ff88", "#88ffcc", "#00cc44", "#00aa00", "#004400",
        "#00ccff", "#3c78dc", "#0000ff", "#0066cc", "#0044aa", "#000080",
        "#ff00ff", "#ff44aa", "#cc44cc", "#aa00aa", "#660066", "#440044",
    ]
    _COLS = 6

    def __init__(self, initial: QColor, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet("QDialog { background: #1e1e1e; border: 1px solid #3a3a3a; }")
        self._chosen: QColor | None = None
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(3)
        for i, hex_color in enumerate(self._SWATCHES):
            row, col = divmod(i, self._COLS)
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(
                f"QPushButton {{ background: {hex_color};"
                " border: 1px solid #555; border-radius: 2px; }"
                "QPushButton:hover { border: 2px solid #fff; }"
            )
            c = QColor(hex_color)
            btn.clicked.connect(lambda _, color=c: self._select(color))
            grid.addWidget(btn, row, col)
        n_rows = len(self._SWATCHES) // self._COLS
        gray_btn = _GrayscaleBtn("Use grayscale")
        gray_btn.setFixedHeight(24)
        gray_btn.clicked.connect(lambda: self._select(QColor(255, 255, 255)))
        grid.addWidget(gray_btn, n_rows, 0, 1, self._COLS)
        self.adjustSize()

    def chosen_color(self) -> QColor | None:
        """Returns the selected color, or None if the picker was dismissed."""
        return self._chosen

    def _select(self, color: QColor):
        self._chosen = color
        self.accept()


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
        hl_lbl.setStyleSheet("color: #ccc; font-size: 13px;")
        top_row.addWidget(hl_lbl, stretch=1)

        self._active_btn = QPushButton("OFF")
        self._active_btn.setCheckable(True)
        self._active_btn.setFixedSize(46, 24)
        self._active_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #666; border: 1px solid #444;"
            " border-radius: 3px; font-size: 12px; font-weight: bold; }"
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
            " border-radius: 3px; font-size: 12px; padding: 1px 4px; }"
        )
        _label_style = "color: #ccc; font-size: 13px;"

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
            " border-radius: 3px; font-size: 12px; padding: 3px 8px; }"
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
        dlg = _ColorSwatchPicker(QColor(r, g, b), self)
        dlg.move(self._color_btn.mapToGlobal(self._color_btn.rect().bottomLeft()))
        dlg.exec()
        color = dlg.chosen_color()
        if color is not None:
            self._color_rgb = [color.red(), color.green(), color.blue()]
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


# ── Brightness / Contrast overlay ────────────────────────────────────────────

class _BrightnessContrastOverlay(QFrame):
    range_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bcOverlay")
        self.setStyleSheet(
            f"#bcOverlay {{ background: {_OVERLAY_BG}; border: 1px solid {_OVERLAY_BORDER};"
            f" border-radius: 4px; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(236)

        self._data_min   = 0.0
        self._data_max   = 1.0
        self._lo         = 0.0
        self._hi         = 1.0
        self._stack_data: np.ndarray | None = None
        self._slice_data: np.ndarray | None = None
        self._updating   = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._canvas = _HistCanvas()
        layout.addWidget(self._canvas)

        self._full_stack_cb = QCheckBox("Full stack")
        self._full_stack_cb.setStyleSheet("color: #ccc; font-size: 13px;")
        self._full_stack_cb.setChecked(True)
        self._full_stack_cb.stateChanged.connect(self._draw_hist)
        layout.addWidget(self._full_stack_cb)

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
            " border-radius: 3px; font-size: 12px; padding: 1px 4px; }"
        )
        _label_style = "color: #ccc; font-size: 13px;"

        for name, attr in (("Min", "lo"), ("Max", "hi")):
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(name)
            lbl.setStyleSheet(_label_style)
            lbl.setFixedWidth(36)
            row.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)
            slider.setStyleSheet(_slider_style)
            row.addWidget(slider, stretch=1)
            box = QLineEdit()
            box.setFixedWidth(54)
            box.setStyleSheet(_edit_style)
            row.addWidget(box)
            layout.addLayout(row)
            setattr(self, f"_slider_{attr}", slider)
            setattr(self, f"_box_{attr}", box)

        self._slider_lo.valueChanged.connect(lambda v: self._min_max_moved("lo", v))
        self._slider_hi.valueChanged.connect(lambda v: self._min_max_moved("hi", v))
        self._box_lo.editingFinished.connect(lambda: self._box_edited("lo"))
        self._box_hi.editingFinished.connect(lambda: self._box_edited("hi"))

        for name, attr in (("Bright", "brightness"), ("Contr", "contrast")):
            row = QHBoxLayout()
            row.setSpacing(6)
            lbl = QLabel(name)
            lbl.setStyleSheet(_label_style)
            lbl.setFixedWidth(36)
            row.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)
            slider.setValue(500)
            slider.setStyleSheet(_slider_style)
            row.addWidget(slider, stretch=1)
            layout.addLayout(row)
            setattr(self, f"_slider_{attr}", slider)

        self._slider_brightness.valueChanged.connect(self._brightness_moved)
        self._slider_contrast.valueChanged.connect(self._contrast_moved)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        _btn_style = (
            "QPushButton { background: #333; color: #ccc; border: 1px solid #555;"
            " border-radius: 3px; font-size: 12px; padding: 3px 8px; }"
            "QPushButton:hover { background: #444; }"
        )
        auto_btn = QPushButton("Auto")
        auto_btn.setStyleSheet(_btn_style)
        auto_btn.clicked.connect(self._auto)
        btn_row.addWidget(auto_btn)
        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(_btn_style)
        reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)

        self.adjustSize()
        self.hide()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(_HIDE_MS)
        self._hide_timer.timeout.connect(self.hide)

    # ── Public API ────────────────────────────────────────────────────────────

    def setup(self, data: np.ndarray, lo: float, hi: float):
        self._stack_data = data
        self._slice_data = None
        self._data_min   = float(data.min())
        self._data_max   = float(data.max())
        self._lo = lo
        self._hi = hi
        self._sync_controls()

    def refresh_hist(self, stack: "np.ndarray | None", slc: "np.ndarray | None"):
        self._stack_data = stack
        self._slice_data = slc
        if self.isVisible():
            self._draw_hist()

    def showEvent(self, event):
        super().showEvent(event)
        self._draw_hist()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _draw_hist(self):
        data = self._stack_data if self._full_stack_cb.isChecked() else self._slice_data
        if data is not None:
            self._canvas.refresh(data, self._lo, self._hi)

    def _sync_controls(self):
        self._updating = True
        dmin, dmax = self._data_min, self._data_max
        drange = dmax - dmin or 1.0
        width  = self._hi - self._lo

        def _to_int(v: float) -> int:
            return int(max(0, min(1000, (v - dmin) / drange * 1000)))

        self._slider_lo.setValue(_to_int(self._lo))
        self._slider_hi.setValue(_to_int(self._hi))
        self._box_lo.setText(f"{self._lo:.0f}")
        self._box_hi.setText(f"{self._hi:.0f}")

        center = (self._lo + self._hi) / 2
        b_val  = int(max(0, min(1000, (center - dmin) / drange * 1000)))
        self._slider_brightness.setValue(b_val)

        w_ratio = width / drange if drange > 0 else 1.0
        if w_ratio >= 1.0:
            c_val = int(max(0, 500 - (w_ratio - 1.0) / 2.0 * 500))
        else:
            c_val = int(500 + (1.0 - w_ratio) / 0.999 * 500)
        self._slider_contrast.setValue(int(max(0, min(1000, c_val))))

        self._updating = False
        if self.isVisible():
            self._draw_hist()

    def _to_data_val(self, slider_int: int) -> float:
        drange = self._data_max - self._data_min or 1.0
        return self._data_min + slider_int / 1000.0 * drange

    def _sync_bc_sliders(self):
        drange = self._data_max - self._data_min or 1.0
        width  = self._hi - self._lo
        center = (self._lo + self._hi) / 2
        b_val  = int(max(0, min(1000, (center - self._data_min) / drange * 1000)))
        self._slider_brightness.setValue(b_val)
        w_ratio = width / drange if drange > 0 else 1.0
        if w_ratio >= 1.0:
            c_val = int(max(0, 500 - (w_ratio - 1.0) / 2.0 * 500))
        else:
            c_val = int(500 + (1.0 - w_ratio) / 0.999 * 500)
        self._slider_contrast.setValue(int(max(0, min(1000, c_val))))

    def _sync_minmax_sliders(self):
        drange = self._data_max - self._data_min or 1.0
        def _to_int(v: float) -> int:
            return int(max(0, min(1000, (v - self._data_min) / drange * 1000)))
        self._slider_lo.setValue(_to_int(self._lo))
        self._slider_hi.setValue(_to_int(self._hi))
        self._box_lo.setText(f"{self._lo:.0f}")
        self._box_hi.setText(f"{self._hi:.0f}")

    def _min_max_moved(self, which: str, v: int):
        if self._updating:
            return
        val = self._to_data_val(v)
        if which == "lo":
            self._lo = val
            self._box_lo.setText(f"{val:.0f}")
        else:
            self._hi = val
            self._box_hi.setText(f"{val:.0f}")
        self._updating = True
        self._sync_bc_sliders()
        self._updating = False
        self.range_changed.emit(self._lo, self._hi)
        if self.isVisible():
            self._draw_hist()

    def _box_edited(self, which: str):
        if self._updating:
            return
        box = self._box_lo if which == "lo" else self._box_hi
        try:
            val = float(box.text())
        except ValueError:
            return
        val = max(self._data_min, min(self._data_max, val))
        if which == "lo":
            self._lo = val
        else:
            self._hi = val
        self._sync_controls()
        self.range_changed.emit(self._lo, self._hi)

    def _brightness_moved(self, v: int):
        if self._updating:
            return
        drange = self._data_max - self._data_min or 1.0
        width  = self._hi - self._lo
        new_center = self._data_min + v / 1000.0 * drange
        lo = new_center - width / 2
        hi = new_center + width / 2
        if lo < self._data_min:
            lo = self._data_min
            hi = lo + width
        if hi > self._data_max:
            hi = self._data_max
            lo = hi - width
        self._lo, self._hi = lo, hi
        self._updating = True
        self._sync_minmax_sliders()
        self._updating = False
        self.range_changed.emit(self._lo, self._hi)
        if self.isVisible():
            self._draw_hist()

    def _contrast_moved(self, v: int):
        if self._updating:
            return
        drange = self._data_max - self._data_min or 1.0
        center  = (self._lo + self._hi) / 2
        w_ratio = 3.0 - v / 250.0 if v <= 500 else 1.0 - 0.999 * (v - 500) / 500.0
        width   = drange * max(w_ratio, 0.001)
        self._lo = center - width / 2
        self._hi = center + width / 2
        self._updating = True
        self._sync_minmax_sliders()
        self._updating = False
        self.range_changed.emit(self._lo, self._hi)
        if self.isVisible():
            self._draw_hist()

    def _auto(self):
        from ..files.loader import compute_display_range
        if self._stack_data is None:
            return
        lo, hi = compute_display_range(self._stack_data)
        self._lo, self._hi = float(lo), float(hi)
        self._sync_controls()
        self.range_changed.emit(self._lo, self._hi)

    def _reset(self):
        self._lo = self._data_min
        self._hi = self._data_max
        self._sync_controls()
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


# ── Tag edit dialog ───────────────────────────────────────────────────────────

class _TagEditDialog(QDialog):
    """Small dialog for creating or editing a tag (note + color)."""

    def __init__(self, note: str = "", color: str = "#ffaa00",
                 is_new: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Tag" if is_new else "Edit Tag")
        self.setFixedWidth(260)
        self.setStyleSheet(
            "QDialog { background: #1e1e1e; }"
            "QLabel { color: #ccc; font-size: 13px; }"
            "QLineEdit { background: #252525; color: #ddd; border: 1px solid #444;"
            " border-radius: 3px; font-size: 13px; padding: 3px 5px; }"
            "QPushButton { background: #333; color: #ccc; border: 1px solid #555;"
            " border-radius: 3px; font-size: 13px; padding: 4px 12px; }"
            "QPushButton:hover { background: #444; }"
        )
        self._color   = color
        self._deleted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Note"))
        self._note_edit = QLineEdit(note)
        self._note_edit.setPlaceholderText("Optional description…")
        self._note_edit.returnPressed.connect(self.accept)
        layout.addWidget(self._note_edit)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_row.addWidget(QLabel("Color"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(24, 24)
        self._color_btn.clicked.connect(self._pick_color)
        self._refresh_color_btn()
        color_row.addWidget(self._color_btn)
        color_row.addStretch()
        layout.addLayout(color_row)
        layout.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        if not is_new:
            del_btn = QPushButton("Delete")
            del_btn.setStyleSheet(
                "QPushButton { background: #4a1616; color: #e06060;"
                " border: 1px solid #6a2020; }"
                "QPushButton:hover { background: #5a1c1c; }"
            )
            del_btn.clicked.connect(self._on_delete)
            btn_row.addWidget(del_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        self.adjustSize()

    def _pick_color(self):
        dlg = _ColorSwatchPicker(QColor(self._color), self)
        dlg.move(self._color_btn.mapToGlobal(self._color_btn.rect().bottomLeft()))
        dlg.exec()
        color = dlg.chosen_color()
        if color is not None:
            self._color = color.name()
            self._refresh_color_btn()

    def _refresh_color_btn(self):
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {self._color}; border: 1px solid #666;"
            " border-radius: 2px; }"
            "QPushButton:hover { border: 1px solid #aaa; }"
        )

    def _on_delete(self):
        self._deleted = True
        self.accept()

    @property
    def note(self) -> str:
        return self._note_edit.text().strip()

    @property
    def color(self) -> str:
        return self._color

    @property
    def deleted(self) -> bool:
        return self._deleted


# ── Anchor overlay ────────────────────────────────────────────────────────────

class _AnchorOverlay(QFrame):
    """Floating per-panel widget shown during anchor placement mode."""

    confirm_clicked = pyqtSignal()
    redo_clicked    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("anchorOverlay")
        self.setStyleSheet(
            "#anchorOverlay { background: rgba(20,20,20,210); border: 1px solid #444;"
            " border-radius: 4px; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._lbl = QLabel("Click to place anchor")
        self._lbl.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
        layout.addWidget(self._lbl)

        self._confirm_btn = QPushButton("Confirm")
        self._confirm_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a; border: 1px solid #2e6e42;"
            " border-radius: 3px; font-size: 12px; padding: 2px 8px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        self._confirm_btn.clicked.connect(self.confirm_clicked)
        self._confirm_btn.hide()
        layout.addWidget(self._confirm_btn)

        _redo_style = (
            "QPushButton { background: #252525; color: #999; border: 1px solid #444;"
            " border-radius: 3px; font-size: 12px; padding: 2px 8px; }"
            "QPushButton:hover { background: #333; color: #eee; }"
        )
        self._redo_btn = QPushButton("Redo")
        self._redo_btn.setStyleSheet(_redo_style)
        self._redo_btn.clicked.connect(self.redo_clicked)
        self._redo_btn.hide()
        layout.addWidget(self._redo_btn)
        self.adjustSize()

    def set_waiting(self):
        self._lbl.setText("Click to place anchor")
        self._lbl.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
        self._confirm_btn.hide()
        self._redo_btn.hide()
        self.adjustSize()

    def set_provisional(self, z: int):
        self._lbl.setText(f"Slice {z + 1}")
        self._lbl.setStyleSheet("color: #ccc; font-size: 12px; background: transparent;")
        self._confirm_btn.show()
        self._redo_btn.show()
        self.adjustSize()

    def set_confirmed(self, z: int):
        self._lbl.setText(f"✓  Slice {z + 1}")
        self._lbl.setStyleSheet("color: #2ce67f; font-size: 12px; background: transparent;")
        self._confirm_btn.hide()
        self._redo_btn.show()
        self.adjustSize()


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


# ── Draggable title bar ───────────────────────────────────────────────────────

class _DraggableTitleBar(QWidget):
    drag_started = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None:
            if (event.pos() - self._press_pos).manhattanLength() > 6:
                self._press_pos = None
                self.drag_started.emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        event.accept()


# ── ViewerPanel ───────────────────────────────────────────────────────────────

class ViewerPanel(QWidget):
    """VolumeViewer with title bar, close button, and floating hist/threshold overlays."""

    closed           = pyqtSignal()
    drag_started     = pyqtSignal()
    tags_changed     = pyqtSignal(list, str)   # (tags, file_type)
    anchor_confirmed = pyqtSignal(str, int, float, float)  # (file_type, slice_idx, scene_x, scene_y)
    anchor_cleared   = pyqtSignal(str)         # file_type

    _BTN_W      = 54
    _BTN_H      = 24
    _BTN_MARGIN = 20   # clears 8px scroll bar + comfortable padding

    def __init__(self, file_type: str, parent=None):
        super().__init__(parent)
        self._file_type  = file_type
        self._filters: list[_HoverFilter] = []
        self._tag_store: TagStore | None  = None
        self._show_tags  = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = _DraggableTitleBar()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(32)
        title_bar.setStyleSheet("#titleBar { background: #2d2d2d; }")
        bar = QHBoxLayout(title_bar)
        bar.setContentsMargins(8, 3, 4, 3)

        self._label = QLabel(FILE_TYPE_LABELS.get(file_type, file_type))
        self._label.setStyleSheet(
            "color: #dddddd; font-weight: bold; font-size: 13px; background: transparent;"
        )
        bar.addWidget(self._label)

        self._filename_lbl = QLabel("")
        self._filename_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._filename_lbl.setStyleSheet(
            "color: #999; font-size: 13px; font-style: italic; background: transparent;"
        )
        bar.addWidget(self._filename_lbl, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: none; font-size: 12px; background: transparent; }"
            "QPushButton:hover { color: white; background: #c0392b; border-radius: 3px; }"
        )
        close_btn.clicked.connect(self.closed)
        bar.addWidget(close_btn)
        self._title_bar = title_bar
        title_bar.drag_started.connect(self.drag_started)
        layout.addWidget(title_bar)

        # ── Viewer ───────────────────────────────────────────────────────────
        self._viewer = VolumeViewer()
        layout.addWidget(self._viewer, stretch=1)

        # ── Floating buttons (not in layout — positioned absolutely) ─────────
        _btn_style = (
            "QPushButton { background: rgba(30,30,30,180); color: #888;"
            " border: 1px solid #444; border-radius: 3px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(60,60,60,220); color: #eee; }"
        )
        self._hist_btn = QPushButton("Hist", self)
        self._hist_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._hist_btn.setStyleSheet(_btn_style)

        self._thr_btn = QPushButton("Thresh", self)
        self._thr_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._thr_btn.setStyleSheet(_btn_style)

        self._bc_btn = QPushButton("B/C", self)
        self._bc_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._bc_btn.setStyleSheet(_btn_style)

        self._tag_btn = QPushButton("Tag", self)
        self._tag_btn.setCheckable(True)
        self._tag_btn.setFixedSize(self._BTN_W, self._BTN_H)
        self._tag_btn.setStyleSheet(
            "QPushButton { background: rgba(30,30,30,180); color: #888;"
            " border: 1px solid #444; border-radius: 3px; font-size: 11px; }"
            "QPushButton:hover { background: rgba(60,60,60,220); color: #eee; }"
            "QPushButton:checked { background: rgba(10,50,25,200); color: #2ce67f;"
            " border-color: #147a3f; }"
        )
        self._tag_btn.toggled.connect(self._on_tag_mode_toggled)

        # ── Floating overlays (not in layout — positioned absolutely) ────────
        self._hist_overlay = _HistogramOverlay(self)
        self._thr_overlay  = _ThresholdOverlay(self)
        self._bc_overlay   = _BrightnessContrastOverlay(self)

        self._thr_overlay.range_changed.connect(self._on_threshold_range)
        self._thr_overlay.toggled.connect(self._on_threshold_toggled)
        self._thr_overlay.color_changed.connect(self._viewer.set_threshold_color)
        self._bc_overlay.range_changed.connect(self._on_bc_range_changed)
        self._viewer.slice_changed.connect(self._refresh_hist)
        self._viewer.tag_place_requested.connect(self._on_tag_place_requested)
        self._viewer.tag_edit_requested.connect(self._on_tag_edit_requested)

        self._wire_btn(self._hist_btn, self._hist_overlay, self._thr_overlay, self._bc_overlay)
        self._wire_btn(self._thr_btn,  self._thr_overlay,  self._hist_overlay, self._bc_overlay)
        self._wire_btn(self._bc_btn,   self._bc_overlay,   self._hist_overlay, self._thr_overlay)

        # Loading spinner (overlaid, hidden until start_loading is called)
        self._spinner = _LoadingSpinner(
            FILE_TYPE_LABELS.get(file_type, file_type), self
        )

        self._anchor_overlay: _AnchorOverlay | None = None
        self._anchor_provisional: tuple | None = None  # (slice_idx, scene_x, scene_y)
        self._viewer.anchor_clicked.connect(self._on_anchor_clicked)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_filename(self, path) -> None:
        """Display the file's stem in the title bar (right-aligned, italic)."""
        stem = path.stem if path else ""
        self._filename_lbl.setText(stem)
        self._filename_lbl.setToolTip(str(path) if path else "")
        if path:
            self._tag_store = TagStore(path)
            self._viewer.set_tags(self._tag_store.tags)

    def load(self, data: np.ndarray, lo: float, hi: float):
        self._viewer.load(data, lo, hi)
        self._viewer.clear_threshold()
        self._thr_overlay.setup(data, lo, hi)
        self._bc_overlay.setup(data, lo, hi)
        self._refresh_hist()

    def start_loading(self):
        self._position_spinner()
        self._spinner.raise_()
        self._spinner.start()

    def stop_loading(self):
        self._spinner.stop()

    def set_tags_visible(self, visible: bool):
        self._show_tags = visible
        self._viewer.set_tags_visible(visible)

    def highlight_tag(self, tag_id: str):
        self._viewer.highlight_tag(tag_id)

    def current_tags(self) -> tuple:
        tags = self._tag_store.tags if self._tag_store else []
        return (tags, self._file_type)

    def edit_tag(self, tag_id: str):
        """Open the edit dialog for tag_id (same as clicking the tag in the scene)."""
        self._on_tag_edit_requested(tag_id)

    def delete_tag(self, tag_id: str):
        if self._tag_store is None:
            return
        self._tag_store.remove(tag_id)
        self._viewer.set_tags(self._tag_store.tags)
        self.tags_changed.emit(self._tag_store.tags, self._file_type)

    def delete_tags(self, tag_ids: list[str]) -> None:
        if self._tag_store is None:
            return
        for tag_id in tag_ids:
            self._tag_store.remove(tag_id)
        self._viewer.set_tags(self._tag_store.tags)
        self.tags_changed.emit(self._tag_store.tags, self._file_type)

    def clear_tags(self) -> None:
        if self._tag_store is None:
            return
        self._tag_store.clear()
        self._viewer.set_tags(self._tag_store.tags)
        self.tags_changed.emit(self._tag_store.tags, self._file_type)

    def set_swap_highlight(self, active: bool):
        style = ("#titleBar { background: #2d2d2d; border: 2px solid #2ce67f; }"
                 if active else
                 "#titleBar { background: #2d2d2d; }")
        self._title_bar.setStyleSheet(style)

    def set_anchor_mode(self, active: bool):
        self._viewer.set_anchor_mode(active)
        if active:
            if self._anchor_overlay is None:
                self._anchor_overlay = _AnchorOverlay(self)
                self._anchor_overlay.confirm_clicked.connect(self._on_anchor_confirm)
                self._anchor_overlay.redo_clicked.connect(self._on_anchor_redo)
            self._anchor_overlay.set_waiting()
            self._position_anchor_overlay()
            self._anchor_overlay.show()
            self._anchor_overlay.raise_()
        else:
            if self._anchor_overlay is not None:
                self._anchor_overlay.hide()
            self._viewer.set_anchor_mode(False)
            self._viewer.clear_anchor_marker()
            self._anchor_provisional = None

    def dismiss_anchor_ui(self):
        """Remove overlay and clear marker (called when anchors are cleared entirely)."""
        if self._anchor_overlay is not None:
            self._anchor_overlay.hide()
        self._viewer.clear_anchor_marker()
        self._anchor_provisional = None

    @property
    def viewer(self) -> VolumeViewer:
        return self._viewer

    @property
    def file_type(self) -> str:
        return self._file_type

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_tag_mode_toggled(self, active: bool):
        self._viewer.set_tag_mode(active)
        if self._tag_store is not None:
            self.tags_changed.emit(self._tag_store.tags, self._file_type)

    def _on_tag_place_requested(self, x: int, y: int, z: int):
        if self._tag_store is None:
            return
        tag = self._tag_store.add(x, y, z)
        dlg = _TagEditDialog(tag.note, tag.color, is_new=True, parent=self)
        dlg.move(QCursor.pos())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.note != tag.note or dlg.color != tag.color:
                self._tag_store.update(tag.id, dlg.note, dlg.color)
        else:
            self._tag_store.remove(tag.id)
        self._viewer.set_tags(self._tag_store.tags)
        self.tags_changed.emit(self._tag_store.tags, self._file_type)
        self._tag_btn.setChecked(False)

    def _on_tag_edit_requested(self, tag_id: str):
        if self._tag_store is None:
            return
        tag = next((t for t in self._tag_store.tags if t.id == tag_id), None)
        if tag is None:
            return
        dlg = _TagEditDialog(tag.note, tag.color, is_new=False, parent=self)
        dlg.move(QCursor.pos())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.deleted:
                self._tag_store.remove(tag_id)
            else:
                self._tag_store.update(tag_id, dlg.note, dlg.color)
        self._viewer.set_tags(self._tag_store.tags)
        self.tags_changed.emit(self._tag_store.tags, self._file_type)

    def _on_anchor_clicked(self, x: float, y: float):
        z = self._viewer.current_slice
        self._anchor_provisional = (z, x, y)
        self._viewer.set_anchor_marker(x, y, confirmed=False)
        if self._anchor_overlay is not None:
            self._anchor_overlay.set_provisional(z)
            self._position_anchor_overlay()

    def _on_anchor_confirm(self):
        if self._anchor_provisional is None:
            return
        z, x, y = self._anchor_provisional
        self._viewer.set_anchor_marker(x, y, confirmed=True)
        if self._anchor_overlay is not None:
            self._anchor_overlay.set_confirmed(z)
            self._position_anchor_overlay()
        self.anchor_confirmed.emit(self._file_type, z, x, y)

    def _on_anchor_redo(self):
        self._anchor_provisional = None
        self._viewer.clear_anchor_marker()
        if self._anchor_overlay is not None:
            self._anchor_overlay.set_waiting()
            self._position_anchor_overlay()
        self.anchor_cleared.emit(self._file_type)

    def _position_anchor_overlay(self):
        if self._anchor_overlay is None:
            return
        ov = self._anchor_overlay
        ov.adjustSize()
        x = (self.width() - ov.width()) // 2
        y = self.height() - ov.height() - 40
        ov.move(x, max(35, y))

    def _wire_btn(self, btn: QPushButton, overlay, *others):
        def _enter():
            for o in others:
                o.cancel_hide()
                o.hide()
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

    def _on_bc_range_changed(self, lo: float, hi: float):
        self._viewer.set_display_range(lo, hi)
        self._refresh_hist()

    def _refresh_hist(self):
        stack = self._viewer.data
        slc   = self._viewer.current_slice_2d
        lo, hi = self._viewer.display_range
        self._hist_overlay.refresh(stack, slc, lo, hi)
        self._bc_overlay.refresh_hist(stack, slc)

    def _position_spinner(self):
        self._spinner.setGeometry(0, 30, self.width(), self.height() - 30)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_overlays()
        self._position_spinner()
        self._position_anchor_overlay()

    def _reposition_overlays(self):
        title_h = 30
        m       = self._BTN_MARGIN
        bw, bh  = self._BTN_W, self._BTN_H
        gap     = 4

        btn_x = self.width() - bw - m
        self._hist_btn.move(btn_x, title_h + m)
        self._thr_btn.move(btn_x,  title_h + m + bh + gap)
        self._bc_btn.move(btn_x,   title_h + m + 2 * (bh + gap))
        self._tag_btn.move(btn_x,  title_h + m + 3 * (bh + gap))

        overlay_y = title_h + m + 4 * bh + 3 * gap + 2
        for ov in (self._hist_overlay, self._thr_overlay, self._bc_overlay):
            ox = max(0, self.width() - ov.width() - m)
            ov.move(ox, overlay_y)
