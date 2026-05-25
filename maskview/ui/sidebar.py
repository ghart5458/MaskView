from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPoint, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListWidget, QPushButton,
    QRadioButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from ..files.resolver import FILE_TYPE_LABELS, FILE_TYPE_ORDER
from ..par.parser import Individual

_TRIGGER_W = 22
_PANEL_W   = 270
_OPEN_W    = _TRIGGER_W + _PANEL_W


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("border: none; border-top: 1px solid #2e2e2e; margin: 3px 0;")
    f.setFixedHeight(7)
    return f


def _mini_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: #888; font-size: 12px; font-weight: bold;"
        " letter-spacing: 1px; padding: 4px 0 2px 0;"
    )
    return lbl


class _ScrollLockCombo(QComboBox):
    """QComboBox that ignores scroll-wheel events to prevent accidental selection changes."""
    def wheelEvent(self, event):
        event.ignore()


class _Section(QWidget):
    """Collapsible sidebar section — state preserved across sidebar open/close."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._hdr = QWidget()
        self._hdr.setFixedHeight(28)
        self._hdr.setStyleSheet(
            "QWidget { background: #1f1f1f; }"
            "QWidget:hover { background: #242424; }"
        )
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hrow = QHBoxLayout(self._hdr)
        hrow.setContentsMargins(8, 0, 8, 0)
        hrow.setSpacing(6)

        self._arrow = QLabel("▾" if expanded else "▸")
        self._arrow.setStyleSheet("color: #777; font-size: 12px;")
        self._arrow.setFixedWidth(10)

        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setStyleSheet(
            "color: #999; font-size: 12px; font-weight: bold; letter-spacing: 1px;"
        )
        hrow.addWidget(self._arrow)
        hrow.addWidget(self._title_lbl, stretch=1)
        layout.addWidget(self._hdr)
        self._hdr.mousePressEvent = lambda _: self._toggle()

        self._body = QWidget()
        self._body.setStyleSheet("background: #181818;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 4, 0, 8)
        self._body_layout.setSpacing(2)
        self._body.setVisible(expanded)
        layout.addWidget(self._body)

    def _toggle(self):
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow.setText("▾" if self._expanded else "▸")

    @property
    def body(self) -> QVBoxLayout:
        return self._body_layout


class Sidebar(QWidget):
    """Hover-activated overlay sidebar with four collapsible sections."""

    par_selected        = pyqtSignal(object)    # Path
    files_applied       = pyqtSignal(list)      # list[str] of checked file types
    orientation_changed = pyqtSignal(str)
    layout_changed      = pyqtSignal(str)
    sync_toggled        = pyqtSignal(bool)
    turbo_changed       = pyqtSignal(int)   # emits stride: 1, 2, or 4
    individual_selected = pyqtSignal(int)
    composite_requested     = pyqtSignal(list)   # list of (file_type, (r,g,b), opacity)
    composite_updated       = pyqtSignal(list)   # same format — live-update existing composite
    composite_blend_changed = pyqtSignal(str)    # "screen" or "alpha"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._file_checks: dict[str, QCheckBox] = {}
        self._file_available: dict[str, bool] = {}
        self._annot_groups: dict[str, QButtonGroup] = {}
        self._is_open      = False
        self._pinned       = False  # True while a color dialog is open
        self._dialog_grace = False  # True during the post-dialog grace period

        self._setup_ui()
        self._setup_animation()
        self._setup_hover()

        self.setFixedWidth(_TRIGGER_W)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # ── Trigger strip ────────────────────────────────────────────────────
        self._trigger = QWidget()
        self._trigger.setFixedWidth(_TRIGGER_W)
        self._trigger.setStyleSheet(
            "QWidget { background: #0d1a10; border-right: 2px solid #147a3f; }"
        )
        self._trigger.setCursor(Qt.CursorShape.PointingHandCursor)
        self._trigger.setToolTip("Show panel")

        icon = QLabel("☰")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            "color: #2ce67f; font-size: 14px;"
            " background: transparent; border: none;"
        )
        self._icon_lbl = icon
        trig_col = QVBoxLayout(self._trigger)
        trig_col.setContentsMargins(0, 0, 0, 0)
        trig_col.addStretch()
        trig_col.addWidget(icon)
        trig_col.addStretch()
        row.addWidget(self._trigger)

        # ── Sliding panel ────────────────────────────────────────────────────
        self._panel = QWidget()
        self._panel.setMinimumWidth(0)
        self._panel.setFixedWidth(0)
        self._panel.setStyleSheet(
            "background: #181818; border-right: 1px solid #2c2c2c;"
        )
        self._panel.hide()
        row.addWidget(self._panel)

        panel_col = QVBoxLayout(self._panel)
        panel_col.setContentsMargins(0, 0, 0, 0)
        panel_col.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: #181818; }
            QScrollBar:vertical {
                background: #141414; width: 5px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 2px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        panel_col.addWidget(scroll, 1)

        content = QWidget()
        content.setStyleSheet("background: #181818;")
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # Collapse all / Expand all
        ca_row = QWidget()
        ca_row.setStyleSheet("background: #111;")
        ca_layout = QHBoxLayout(ca_row)
        ca_layout.setContentsMargins(8, 4, 8, 4)
        ca_layout.setSpacing(6)
        _ca_style = (
            "QPushButton { background: transparent; color: #888; border: none;"
            " font-size: 12px; padding: 0; }"
            "QPushButton:hover { color: #bbb; }"
        )
        btn_collapse = QPushButton("collapse all")
        btn_collapse.setStyleSheet(_ca_style)
        btn_collapse.clicked.connect(lambda: self._set_all_sections(False))
        btn_expand = QPushButton("expand all")
        btn_expand.setStyleSheet(_ca_style)
        btn_expand.clicked.connect(lambda: self._set_all_sections(True))
        ca_layout.addWidget(btn_collapse)
        _div = QLabel("|")
        _div.setStyleSheet("color: #444; font-size: 12px;")
        ca_layout.addWidget(_div)
        ca_layout.addWidget(btn_expand)
        ca_layout.addStretch()
        col.addWidget(ca_row)

        self._sec_file  = _Section("File",        expanded=True)
        self._sec_tools = _Section("Tools",       expanded=True)
        self._sec_annot = _Section("Annotations", expanded=True)
        self._sec_indiv = _Section("Individuals", expanded=True)

        col.addWidget(self._sec_file)
        col.addWidget(self._sec_tools)
        col.addWidget(self._sec_annot)
        col.addWidget(self._sec_indiv)
        col.addStretch()
        scroll.setWidget(content)

        # ── Nav bar pinned to bottom of panel ─────────────────────────────────
        nav_bar = QWidget()
        nav_bar.setStyleSheet(
            "background: #141414; border-top: 1px solid #2e2e2e;"
        )
        _nrow = QHBoxLayout(nav_bar)
        _nrow.setContentsMargins(6, 4, 6, 6)
        _nrow.setSpacing(4)
        _btn_style = (
            "QPushButton { background: #202020; color: #999; border: none;"
            " border-radius: 3px; font-size: 12px; padding: 2px 6px; }"
            "QPushButton:hover:enabled { background: #2c2c2c; color: #ddd; }"
            "QPushButton:disabled { color: #3a3a3a; }"
        )
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.setStyleSheet(_btn_style)
        self._prev_btn.clicked.connect(self._go_prev)
        self._counter = QLabel("— / —")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet("color: #888; font-size: 12px;")
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(28)
        self._next_btn.setStyleSheet(_btn_style)
        self._next_btn.clicked.connect(self._go_next)
        _nrow.addWidget(self._prev_btn)
        _nrow.addWidget(self._counter, stretch=1)
        _nrow.addWidget(self._next_btn)
        panel_col.addWidget(nav_bar)
        self._refresh_nav(-1)

        self._build_file_section()
        self._build_tools_section()
        self._build_annotations_section([])
        self._build_individuals_section()

    def _build_file_section(self):
        body = self._sec_file.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(4)

        self._par_btn = QPushButton("Select PAR / CSV…")
        self._par_btn.setStyleSheet(
            "QPushButton { background: #0f2a1a; color: #5fd49a; border: none;"
            " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        self._par_btn.clicked.connect(self._browse_par)
        body.addWidget(self._par_btn)

        self._par_label = QLabel("No file loaded")
        self._par_label.setStyleSheet(
            "color: #666; font-size: 12px; font-style: italic; padding: 1px 0;"
        )
        self._par_label.setWordWrap(True)
        body.addWidget(self._par_label)

        body.addWidget(_sep())
        self._sec_display = _Section("Display", expanded=True)
        db = self._sec_display.body
        db.setContentsMargins(8, 6, 8, 8)
        db.setSpacing(4)

        _cb_style = (
            "QCheckBox { color: #888; font-size: 12px; padding: 1px 0; }"
            "QCheckBox:enabled { color: #ccc; }"
            "QCheckBox:disabled { color: #4a4a4a; }"
        )
        cb_content = QWidget()
        cb_content.setStyleSheet("background: #181818;")
        cb_col = QVBoxLayout(cb_content)
        cb_col.setContentsMargins(4, 2, 4, 2)
        cb_col.setSpacing(3)
        _default_checked = {"original", "maskseg"}
        for ft in FILE_TYPE_ORDER:
            cb = QCheckBox(FILE_TYPE_LABELS[ft])
            cb.setEnabled(True)
            cb.setChecked(ft in _default_checked)
            cb.setStyleSheet(_cb_style)
            cb.toggled.connect(lambda _: self._update_file_count())
            self._file_checks[ft] = cb
            cb_col.addWidget(cb)
        cb_col.addStretch()

        cb_scroll = QScrollArea()
        cb_scroll.setWidgetResizable(True)
        cb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        cb_scroll.setFixedHeight(120)
        cb_scroll.setStyleSheet("""
            QScrollArea { border: 1px solid #2e2e2e; background: #181818; }
            QScrollBar:vertical { background: #141414; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        cb_scroll.setWidget(cb_content)
        db.addWidget(cb_scroll)

        self._file_count_lbl = QLabel()
        self._file_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._file_count_lbl.setStyleSheet(
            "color: #666; font-size: 11px; padding: 0 2px 0 0;"
        )
        db.addWidget(self._file_count_lbl)
        self._update_file_count()

        db.addWidget(_sep())
        self._apply_btn = QPushButton("Load")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #147a3f; color: #fff;"
            " border-color: #3a8a52; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a;"
            " border: 1px solid #252525; }"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        db.addWidget(self._apply_btn)

        db.addWidget(_sep())
        db.addWidget(_mini_label("ORIENTATION"))
        orient_row = QWidget()
        orow = QHBoxLayout(orient_row)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.setSpacing(10)
        self._orient_group = QButtonGroup(self)
        for text in ("XY", "XZ", "YZ"):
            rb = QRadioButton(text)
            rb.setChecked(text == "XY")
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 12px; }")
            rb.toggled.connect(
                lambda chk, t=text: self.orientation_changed.emit(t) if chk else None
            )
            self._orient_group.addButton(rb)
            orow.addWidget(rb)
        orow.addStretch()
        db.addWidget(orient_row)

        db.addWidget(_sep())
        db.addWidget(_mini_label("LAYOUT"))
        layout_row = QWidget()
        lrow = QHBoxLayout(layout_row)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(10)
        self._layout_group = QButtonGroup(self)
        for text in ("2×2", "4×1"):
            rb = QRadioButton(text)
            rb.setChecked(text == "2×2")
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 12px; }")
            rb.toggled.connect(
                lambda chk, t=text: self.layout_changed.emit(t) if chk else None
            )
            self._layout_group.addButton(rb)
            lrow.addWidget(rb)
        lrow.addStretch()
        db.addWidget(layout_row)

        db.addWidget(_sep())
        ds_row = QWidget()
        ds_row.setStyleSheet("background: transparent;")
        drow = QHBoxLayout(ds_row)
        drow.setContentsMargins(0, 2, 0, 0)
        drow.setSpacing(6)
        drow.addWidget(_mini_label("DOWNSAMPLING"))
        drow.addStretch()
        _ds_combo_style = (
            "QComboBox { background: #252525; color: #ccc; border: 1px solid #3a3a3a;"
            " border-radius: 2px; font-size: 11px; padding: 1px 3px; }"
            "QComboBox::drop-down { border: none; width: 14px; }"
            "QComboBox QAbstractItemView { background: #252525; color: #ccc;"
            " selection-background-color: #147a3f; }"
        )
        self._downsample_combo = _ScrollLockCombo()
        self._downsample_combo.addItem("None", 1)
        self._downsample_combo.addItem("2×",   2)
        self._downsample_combo.addItem("4×",   4)
        self._downsample_combo.setFixedWidth(52)
        self._downsample_combo.setStyleSheet(_ds_combo_style)
        self._downsample_combo.setToolTip(
            "Downsample volumes on load (stride per axis).\n"
            "2× = half resolution in each dimension.\n"
            "4× = quarter resolution in each dimension.\n"
            "Takes effect on the next Load."
        )
        self._downsample_combo.currentIndexChanged.connect(
            lambda _: self.turbo_changed.emit(self._downsample_combo.currentData())
        )
        drow.addWidget(self._downsample_combo)
        db.addWidget(ds_row)

        body.addWidget(self._sec_display)

    def _build_tools_section(self):
        body = self._sec_tools.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(5)

        self._sync_cb = QCheckBox("Synchronize windows")
        self._sync_cb.setChecked(True)
        self._sync_cb.setStyleSheet("QCheckBox { color: #bbb; font-size: 12px; }")
        self._sync_cb.toggled.connect(self.sync_toggled)
        body.addWidget(self._sync_cb)

        body.addWidget(_sep())
        body.addWidget(_mini_label("COLOR OVERLAY"))

        _combo_style = (
            "QComboBox { background: #252525; color: #ccc; border: 1px solid #3a3a3a;"
            " border-radius: 2px; font-size: 11px; padding: 1px 3px; }"
            "QComboBox::drop-down { border: none; width: 14px; }"
            "QComboBox QAbstractItemView { background: #252525; color: #ccc;"
            " selection-background-color: #147a3f; }"
        )
        _slider_style = (
            "QSlider::groove:horizontal { height: 3px; background: #2a2a2a; border-radius: 1px; }"
            "QSlider::handle:horizontal { width: 10px; height: 10px; margin: -3px 0;"
            " background: #666; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #3a3a3a; border-radius: 1px; }"
        )

        self._overlay_channels = []  # (checkbox, combo, color_btn, opacity_slider)
        self._overlay_colors   = [(220, 80, 80), (80, 200, 80), (80, 120, 220)]
        _defaults = [
            ("original", 0),
            ("maskseg",  1),
            ("seg",      2),
        ]
        for ch_idx, (ft_default, color_idx) in enumerate(_defaults):
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rrow = QHBoxLayout(row_w)
            rrow.setContentsMargins(0, 1, 0, 1)
            rrow.setSpacing(4)

            combo = _ScrollLockCombo()
            combo.addItem("— None —", None)
            for ft in FILE_TYPE_ORDER:
                combo.addItem(FILE_TYPE_LABELS[ft], ft)
            combo.setCurrentIndex(0)
            combo.setStyleSheet(_combo_style)
            rrow.addWidget(combo, stretch=1)

            r, g, b = self._overlay_colors[color_idx]
            color_btn = QPushButton()
            color_btn.setFixedSize(22, 22)
            color_btn.setToolTip("Click to pick color")
            color_btn.setStyleSheet(
                f"QPushButton {{ background: rgb({r},{g},{b});"
                " border: 1px solid #555; border-radius: 2px; }"
                "QPushButton:hover { border: 1px solid #999; }"
            )
            color_btn.clicked.connect(
                lambda _, i=ch_idx: self._pick_overlay_color(i)
            )
            rrow.addWidget(color_btn)

            opacity_slider = QSlider(Qt.Orientation.Horizontal)
            opacity_slider.setRange(0, 100)
            opacity_slider.setValue(100)
            opacity_slider.setFixedWidth(54)
            opacity_slider.setToolTip("Opacity")
            opacity_slider.setStyleSheet(_slider_style)
            opacity_slider.valueChanged.connect(lambda _: self._on_composite_controls_changed())
            rrow.addWidget(opacity_slider)

            self._overlay_channels.append((combo, color_btn, opacity_slider))
            body.addWidget(row_w)

        blend_row = QWidget()
        blend_row.setStyleSheet("background: transparent;")
        brow_l = QHBoxLayout(blend_row)
        brow_l.setContentsMargins(0, 2, 0, 2)
        brow_l.setSpacing(6)
        blend_lbl = QLabel("Overlay mode:")
        blend_lbl.setStyleSheet("color: #888; font-size: 12px;")
        brow_l.addWidget(blend_lbl)
        self._blend_combo = _ScrollLockCombo()
        self._blend_combo.addItem("Screen", "screen")
        self._blend_combo.addItem("Alpha",  "alpha")
        self._blend_combo.setStyleSheet(_combo_style)
        self._blend_combo.currentIndexChanged.connect(lambda _: self._on_blend_mode_changed())
        brow_l.addWidget(self._blend_combo, stretch=1)
        body.addWidget(blend_row)

        self._create_composite_btn = QPushButton("Create Composite")
        self._create_composite_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff;"
            " border-color: #3a8a52; }"
        )
        self._create_composite_btn.clicked.connect(self._on_create_composite)
        body.addWidget(self._create_composite_btn)

        body.addWidget(_sep())
        lbl = QLabel("Tagging")
        lbl.setStyleSheet("color: #4a4a4a; font-size: 12px; padding: 1px 0;")
        body.addWidget(lbl)

    def _pick_overlay_color(self, idx: int):
        r, g, b = self._overlay_colors[idx]
        dlg = QColorDialog(QColor(r, g, b), self)
        dlg.setWindowTitle("Pick channel color")
        # Place dialog just to the right of the sidebar so it doesn't trigger close
        dlg.move(self.mapToGlobal(QPoint(self.width() + 4, 40)))

        self._pinned = True
        dlg.exec()
        self._pinned = False

        # If the cursor left while the dialog was open, grant one forgiven poll tick
        # before actually closing (gives the user time to move back to the sidebar).
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._dialog_grace = True
            self._poll.setInterval(500)  # slow first tick; grace clears it, then normal 80ms
            self._poll.start()

        color = dlg.selectedColor()
        if not color.isValid():
            return
        self._overlay_colors[idx] = (color.red(), color.green(), color.blue())
        r, g, b = self._overlay_colors[idx]
        _, btn, _ = self._overlay_channels[idx]
        btn.setStyleSheet(
            f"QPushButton {{ background: rgb({r},{g},{b});"
            " border: 1px solid #555; border-radius: 2px; }"
            "QPushButton:hover { border: 1px solid #999; }"
        )
        self._on_composite_controls_changed()

    def update_composite_channels(self, open_file_types: list[str]):
        """Repopulate channel dropdowns to only show currently open file types."""
        for combo, _, _ in self._overlay_channels:
            current_data = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— None —", None)
            for ft in open_file_types:
                combo.addItem(FILE_TYPE_LABELS[ft], ft)
            restore = next(
                (i + 1 for i, ft in enumerate(open_file_types) if ft == current_data),
                0,
            )
            combo.setCurrentIndex(restore)
            combo.blockSignals(False)

    def _build_composite_specs(self) -> list:
        specs = []
        for i, (combo, _, slider) in enumerate(self._overlay_channels):
            ft = combo.currentData()
            if ft is None:
                continue
            specs.append((ft, self._overlay_colors[i], slider.value() / 100.0))
        return specs

    def _on_composite_controls_changed(self):
        specs = self._build_composite_specs()
        if specs:
            self.composite_updated.emit(specs)

    def _on_create_composite(self):
        specs = self._build_composite_specs()
        if specs:
            self.composite_requested.emit(specs)

    @property
    def composite_blend_mode(self) -> str:
        return self._blend_combo.currentData()

    def _on_blend_mode_changed(self):
        self.composite_blend_changed.emit(self._blend_combo.currentData())
        self._on_composite_controls_changed()

    def _build_annotations_section(self, file_types: list[str]):
        body = self._sec_annot.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(4)

        while body.count():
            item = body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._annot_groups.clear()

        if not file_types:
            lbl = QLabel("No panels open")
            lbl.setStyleSheet("color: #666; font-size: 12px;")
            body.addWidget(lbl)
            return

        for ft in file_types:
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rrow = QHBoxLayout(row_w)
            rrow.setContentsMargins(0, 1, 0, 1)
            rrow.setSpacing(3)

            lbl = QLabel(FILE_TYPE_LABELS.get(ft, ft))
            lbl.setStyleSheet("color: #aaa; font-size: 12px;")
            rrow.addWidget(lbl, stretch=1)

            group = QButtonGroup(row_w)
            group.setExclusive(True)
            for text, color in (("Pass", "#1d4a27"), ("Rev", "#4a3c12"), ("Fail", "#4a1616")):
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setFixedHeight(18)
                btn.setStyleSheet(
                    "QPushButton { background: #252525; color: #888; border: none;"
                    f" border-radius: 2px; font-size: 12px; padding: 0 5px; }}"
                    f"QPushButton:checked {{ background: {color}; color: #ddd; }}"
                    "QPushButton:hover:!checked { background: #2e2e2e; color: #aaa; }"
                )
                group.addButton(btn)
                rrow.addWidget(btn)

            self._annot_groups[ft] = group
            body.addWidget(row_w)

    def _build_individuals_section(self):
        body = self._sec_indiv.body
        body.setContentsMargins(0, 2, 0, 0)
        body.setSpacing(0)

        self._indiv_list = QListWidget()
        self._indiv_list.setUniformItemSizes(True)
        self._indiv_list.setFixedHeight(115)
        self._indiv_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self._indiv_list.setStyleSheet("""
            QListWidget {
                background: #141414; border: 1px solid #2e2e2e;
                color: #ccc; font-size: 12px;
            }
            QListWidget::item { padding: 3px 10px; border-bottom: 1px solid #1c1c1c; }
            QListWidget::item:selected { background: #147a3f; color: #fff; }
            QListWidget::item:hover:!selected { background: #1e1e1e; }
            QScrollBar:vertical { background: #141414; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self._indiv_list.currentRowChanged.connect(self._on_row_changed)
        body.addWidget(self._indiv_list)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _setup_animation(self):
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_done)

    def _animate_to(self, target: int):
        if target > _TRIGGER_W:
            self._panel.show()
            self._is_open = True
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_value(self, v):
        w = int(v)
        self.setFixedWidth(w)
        self._panel.setFixedWidth(max(0, w - _TRIGGER_W))

    def _on_anim_done(self):
        if int(self._anim.endValue()) <= _TRIGGER_W:
            self._panel.hide()
            self._panel.setFixedWidth(0)
            self._is_open = False

    # ── Hover ─────────────────────────────────────────────────────────────────

    def _setup_hover(self):
        self._poll = QTimer(self)
        self._poll.setInterval(80)
        self._poll.timeout.connect(self._check_cursor)

    def enterEvent(self, event):
        self._poll.stop()
        self._dialog_grace = False
        self._poll.setInterval(80)
        if not self._is_open:
            self._animate_to(_OPEN_W)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._is_open and not self._pinned:
            self._poll.start()
        super().leaveEvent(event)

    def _check_cursor(self):
        if self._pinned:
            return
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            if self._dialog_grace:
                # First check after dialog — forgive and reset to normal poll speed
                self._dialog_grace = False
                self._poll.setInterval(80)
                return
            self._animate_to(_TRIGGER_W)
            self._poll.stop()

    # ── Public API ────────────────────────────────────────────────────────────

    def open_now(self):
        """Expand without animation — used on startup."""
        self._panel.show()
        self._panel.setFixedWidth(_PANEL_W)
        self.setFixedWidth(_OPEN_W)
        self._is_open = True

    def load_individuals(self, individuals: list[Individual]):
        self._individuals = individuals
        self._current_idx = -1
        self._indiv_list.blockSignals(True)
        self._indiv_list.clear()
        for ind in individuals:
            self._indiv_list.addItem(ind.oldname)
        self._indiv_list.blockSignals(False)
        self._counter.setText("— / —")
        self._refresh_nav(-1)
        self._apply_btn.setEnabled(False)

    def select_individual(self, idx: int):
        if 0 <= idx < len(self._individuals):
            self._indiv_list.setCurrentRow(idx)

    def select_individual_silent(self, idx: int):
        """Highlight individual in list without emitting individual_selected."""
        if 0 <= idx < len(self._individuals):
            self._indiv_list.blockSignals(True)
            self._indiv_list.setCurrentRow(idx)
            self._indiv_list.blockSignals(False)
            self._current_idx = idx
            n = len(self._individuals)
            self._counter.setText(f"{idx + 1} / {n}")
            self._refresh_nav(idx)
            self._apply_btn.setEnabled(True)

    def update_file_availability(self, available: dict[str, bool], loaded: set[str]):
        self._file_available = {ft: available.get(ft, False) for ft in self._file_checks}
        for ft, cb in self._file_checks.items():
            cb.blockSignals(True)
            exists = self._file_available[ft]
            cb.setEnabled(exists)
            cb.setChecked(exists and ft in loaded)
            cb.blockSignals(False)
        self._update_file_count()

    def set_file_loaded(self, ft: str, loaded: bool):
        cb = self._file_checks.get(ft)
        if cb:
            cb.blockSignals(True)
            cb.setChecked(loaded)
            cb.blockSignals(False)
        self._update_file_count()

    def update_annotations(self, file_types: list[str]):
        self._build_annotations_section(file_types)

    def set_par_label(self, path: Path | None):
        if path is None:
            self._par_label.setText("No file loaded")
            self._par_label.setStyleSheet(
                "color: #666; font-size: 12px; font-style: italic; padding: 1px 0;"
            )
        else:
            self._par_label.setText(path.name)
            self._par_label.setStyleSheet(
                "color: #aaa; font-size: 12px; font-style: normal; padding: 1px 0;"
            )

    def set_controls_enabled(self, enabled: bool):
        # Navigation stays active so the user can cancel a slow load by moving elsewhere.
        # Only the file selector is locked while loading.
        for ft, cb in self._file_checks.items():
            cb.setEnabled(enabled and self._file_available.get(ft, False))
        self._apply_btn.setEnabled(enabled and self._current_idx >= 0)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _update_file_count(self):
        n = sum(1 for cb in self._file_checks.values() if cb.isChecked())
        self._file_count_lbl.setText(f"{n} selected")

    def _set_all_sections(self, expanded: bool):
        for sec in (self._sec_file, self._sec_display, self._sec_tools,
                    self._sec_annot, self._sec_indiv):
            if sec._expanded != expanded:
                sec._toggle()

    def _on_apply(self):
        selected = [ft for ft, cb in self._file_checks.items() if cb.isChecked()]
        self.files_applied.emit(selected)

    def _browse_par(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PAR or CSV file", "",
            "PAR / CSV files (*.par *.csv);;All files (*)"
        )
        if path:
            self.par_selected.emit(Path(path))

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._current_idx = row
        n = len(self._individuals)
        self._counter.setText(f"{row + 1} / {n}")
        self._refresh_nav(row)
        self.individual_selected.emit(row)

    def _go_prev(self):
        if self._current_idx > 0:
            self._indiv_list.setCurrentRow(self._current_idx - 1)

    def _go_next(self):
        if self._current_idx < len(self._individuals) - 1:
            self._indiv_list.setCurrentRow(self._current_idx + 1)

    def _refresh_nav(self, row: int):
        n = len(self._individuals)
        self._prev_btn.setEnabled(row > 0)
        self._next_btn.setEnabled(0 <= row < n - 1)
