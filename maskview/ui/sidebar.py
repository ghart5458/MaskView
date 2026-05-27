from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton,
    QRadioButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from .. import settings as _settings
from ..files.resolver import FILE_TYPE_LABELS, FILE_TYPE_ORDER
from ..par.parser import Individual
from .annotations import BTN_TO_VALUE, VALUE_TO_BTN
from .viewer_panel import _ColorSwatchPicker

_SIDEBAR_W = 280


class _WheelIsolator(QObject):
    """Installed on a scroll widget's viewport; consumes wheel events so they
    never escape to the outer sidebar scroll area when the widget hits its limit.

    Sets the scrollbar value directly (never via sendEvent, which re-propagates
    when the bar is at its limit). Uses pixelDelta for trackpad precision with a
    fractional accumulator, falls back to angleDelta for mouse wheels."""

    def __init__(self, widget):
        super().__init__(widget)          # QObject child → kept alive with the widget
        self._bar   = widget.verticalScrollBar()
        self._accum = 0.0
        widget.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            bar = self._bar
            py  = event.pixelDelta().y()
            if py != 0:
                # Trackpad sends pixel-precise deltas. Convert to scrollbar units
                # using pageStep/viewport-height so this works for both QScrollArea
                # (value = pixels, pageStep ≈ viewport height → scale ≈ 1) and
                # QListWidget (value = items, pageStep = visible items → scale =
                # 1/item_height), without needing to know the widget type.
                scale = max(bar.pageStep(), 1) / max(obj.height(), 1)
                self._accum += py * scale
                step = int(self._accum)
                if step:
                    bar.setValue(bar.value() - step)
                    self._accum -= step
            else:
                dy = event.angleDelta().y()
                if dy:
                    bar.setValue(bar.value() - (bar.singleStep() if dy > 0 else -bar.singleStep()))
                self._accum = 0.0
            return True   # always consumed — parent scroll area never sees it
        return False


_TURBO_OFF_STYLE = (
    "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
    " border-radius: 3px; font-size: 11px; font-weight: bold; }"
    "QPushButton:hover { color: #999; background: #2e2e2e; }"
)
_TURBO_ON_STYLE = (
    "QPushButton { background: #0f2a1a; color: #2ce67f; border: 1px solid #147a3f;"
    " border-radius: 3px; font-size: 11px; font-weight: bold; }"
    "QPushButton:hover { background: #147a3f; color: #fff; }"
)


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


class _ManualFileSelectDialog(QDialog):
    """One Browse row per file type — lets the user pick each MHD independently."""

    def __init__(self, file_types: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select files manually")
        self.setMinimumWidth(500)
        self._paths: dict = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        intro = QLabel("Browse to each file individually.")
        intro.setStyleSheet("color: #aaa; font-size: 12px; padding-bottom: 2px;")
        layout.addWidget(intro)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)
        gl.setColumnStretch(1, 1)

        self._path_labels: dict = {}
        for i, ft in enumerate(file_types):
            type_lbl = QLabel(FILE_TYPE_LABELS.get(ft, ft))
            type_lbl.setStyleSheet("color: #ccc; font-size: 12px;")
            type_lbl.setFixedWidth(90)

            path_lbl = QLabel("—")
            path_lbl.setStyleSheet("color: #555; font-size: 11px; font-style: italic;")
            self._path_labels[ft] = path_lbl

            browse_btn = QPushButton("Browse…")
            browse_btn.setFixedWidth(72)
            browse_btn.setStyleSheet(
                "QPushButton { background: #252525; color: #999; border: 1px solid #3a3a3a;"
                " border-radius: 3px; font-size: 11px; padding: 2px 6px; }"
                "QPushButton:hover { background: #2e2e2e; color: #ddd; }"
            )
            browse_btn.clicked.connect(lambda _, f=ft: self._browse_one(f))

            gl.addWidget(type_lbl, i, 0)
            gl.addWidget(path_lbl, i, 1)
            gl.addWidget(browse_btn, i, 2)

        layout.addWidget(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #999; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 4px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #2e2e2e; color: #ddd; }"
        )
        cancel_btn.clicked.connect(self.reject)
        load_btn = QPushButton("Load")
        load_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a; border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 4px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; border-color: #3a8a52; }"
        )
        load_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(load_btn)
        layout.addLayout(btn_row)

    def _browse_one(self, ft: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {FILE_TYPE_LABELS.get(ft, ft)}", "",
            "MHD files (*.mhd);;All files (*)"
        )
        if path:
            self._paths[ft] = Path(path)
            self._path_labels[ft].setText(Path(path).name)
            self._path_labels[ft].setStyleSheet(
                "color: #aaa; font-size: 11px; font-style: normal;"
            )

    @property
    def selected_paths(self) -> dict:
        return dict(self._paths)


class Sidebar(QWidget):
    """Resizable sidebar with four collapsible sections."""

    par_selected           = pyqtSignal(object)   # Path
    scan_selected          = pyqtSignal(object)   # Path
    manual_files_selected  = pyqtSignal(dict)     # {file_type: Path}
    anchor_mode_requested  = pyqtSignal()
    anchor_apply_requested = pyqtSignal()
    anchor_cancel_requested = pyqtSignal()
    anchor_clear_requested  = pyqtSignal()
    files_applied       = pyqtSignal(list)      # list[str] of checked file types
    orientation_changed = pyqtSignal(str)
    layout_changed      = pyqtSignal(str)
    sync_toggled        = pyqtSignal(bool)
    turbo_changed       = pyqtSignal(int)   # emits stride: 1, 2, or 4
    individual_selected = pyqtSignal(int)
    load_requested      = pyqtSignal(int, list)  # (idx, file_types) — new individual + files
    composite_requested     = pyqtSignal(list)   # list of (file_type, (r,g,b), opacity)
    composite_updated       = pyqtSignal(list)   # same format — live-update existing composite
    composite_blend_changed = pyqtSignal(str)    # "screen" or "alpha"
    annotation_changed      = pyqtSignal(str, str)  # (file_type, value: "Pass"/"Review"/"Fail"/"")
    tags_visible_changed    = pyqtSignal(bool)
    tag_selected            = pyqtSignal(str, int, int, int, str)  # (file_type, x, y, z, tag_id)
    tag_edit_requested      = pyqtSignal(str, str)  # (file_type, tag_id)
    tag_delete_requested    = pyqtSignal(str, str)  # (file_type, tag_id)
    par_refresh_requested   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._loaded_idx  = -1
        self._nav_triggered = False
        self._file_checks: dict[str, QCheckBox] = {}
        self._file_available: dict[str, bool] = {}
        self._annot_groups: dict[str, QButtonGroup] = {}

        self._setup_ui()
        self.setMinimumWidth(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet("background: #181818; border-right: 1px solid #2c2c2c;")

        panel_col = QVBoxLayout(self)
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

        self._sec_file    = _Section("File",          expanded=True)
        self._sec_display = _Section("Display",       expanded=True)
        self._sec_overlay = _Section("Color Overlay", expanded=False)
        self._sec_tags    = _Section("Tagging",       expanded=False)
        self._sec_annot   = _Section("Annotations",   expanded=False)
        self._sec_indiv   = _Section("Individuals",   expanded=True)

        col.addWidget(self._sec_file)
        col.addWidget(self._sec_display)
        col.addWidget(self._sec_overlay)
        col.addWidget(self._sec_tags)
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
        self._build_display_section()
        self._build_overlay_section()
        self._build_tags_section()
        self._build_annotations_section([])
        self._build_individuals_section()

    def _build_file_section(self):
        body = self._sec_file.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(4)

        _open_btn_style = (
            "QPushButton { background: #0f2a1a; color: #5fd49a; border: none;"
            " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        _grp_lbl_style = "color: #666; font-size: 11px; font-style: italic; padding: 2px 0 1px 0;"
        _medtool_lbl = QLabel("Medtool directory structure:")
        _medtool_lbl.setStyleSheet(_grp_lbl_style)
        body.addWidget(_medtool_lbl)
        self._par_btn = QPushButton("Select PAR / CSV…")
        self._par_btn.setStyleSheet(_open_btn_style)
        self._par_btn.clicked.connect(self._browse_par)
        body.addWidget(self._par_btn)

        self._scan_btn = QPushButton("Select individual scan…")
        self._scan_btn.setStyleSheet(_open_btn_style)
        self._scan_btn.clicked.connect(self._browse_scan)
        body.addWidget(self._scan_btn)

        body.addWidget(_sep())
        _nonstd_lbl = QLabel("Non-standard directory structure:")
        _nonstd_lbl.setStyleSheet(_grp_lbl_style)
        body.addWidget(_nonstd_lbl)
        self._manual_btn = QPushButton("Select files manually…")
        self._manual_btn.setStyleSheet(_open_btn_style)
        self._manual_btn.clicked.connect(self._browse_manual)
        body.addWidget(self._manual_btn)

    def _build_display_section(self):
        body = self._sec_display.body
        body.setContentsMargins(8, 6, 8, 8)
        body.setSpacing(4)

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
            cb.toggled.connect(lambda _: _settings.save(
                {'checked_file_types': self.checked_file_types()}
            ))
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
            QScrollBar:vertical { background: #181818; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        cb_scroll.setWidget(cb_content)
        _WheelIsolator(cb_scroll)
        body.addWidget(cb_scroll)

        self._file_count_lbl = QLabel()
        self._file_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._file_count_lbl.setStyleSheet(
            "color: #666; font-size: 11px; padding: 0 2px 0 0;"
        )
        body.addWidget(self._file_count_lbl)
        self._update_file_count()

        body.addWidget(_sep())
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
        body.addWidget(self._apply_btn)

        body.addWidget(_sep())
        body.addWidget(_mini_label("ORIENTATION"))
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
        body.addWidget(orient_row)

        body.addWidget(_sep())
        body.addWidget(_mini_label("LAYOUT"))
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
        body.addWidget(layout_row)

        body.addWidget(_sep())
        ds_row = QWidget()
        ds_row.setStyleSheet("background: transparent;")
        drow = QHBoxLayout(ds_row)
        drow.setContentsMargins(0, 2, 0, 0)
        drow.setSpacing(6)
        drow.addWidget(_mini_label("DOWNSAMPLING"))
        drow.addStretch()
        self._turbo_idx = 0
        self._turbo_btn = QPushButton("NONE")
        self._turbo_btn.setFixedSize(42, 22)
        self._turbo_btn.setToolTip(
            "Cycle downsampling on load (stride per axis).\n"
            "2× = half resolution.  4× = quarter resolution.\n"
            "Takes effect on the next Load."
        )
        self._turbo_btn.setStyleSheet(_TURBO_OFF_STYLE)
        self._turbo_btn.clicked.connect(self._on_turbo_cycle)
        drow.addWidget(self._turbo_btn)
        body.addWidget(ds_row)

        body.addWidget(_sep())
        sync_row = QWidget()
        sync_row.setStyleSheet("background: transparent;")
        srow = QHBoxLayout(sync_row)
        srow.setContentsMargins(0, 2, 0, 0)
        srow.setSpacing(6)
        srow.addWidget(_mini_label("SYNCHRONIZE VIEWS"))
        srow.addStretch()
        self._sync_btn = QPushButton("ON")
        self._sync_btn.setCheckable(True)
        self._sync_btn.setChecked(True)
        self._sync_btn.setFixedSize(42, 22)
        self._sync_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { color: #999; background: #2e2e2e; }"
            "QPushButton:checked { background: #0f2a1a; color: #2ce67f;"
            " border-color: #147a3f; }"
            "QPushButton:checked:hover { background: #147a3f; color: #fff; }"
        )
        self._sync_btn.toggled.connect(self._on_sync_toggled)
        srow.addWidget(self._sync_btn)
        body.addWidget(sync_row)

        body.addWidget(_sep())
        anchor_row = QWidget()
        anchor_row.setStyleSheet("background: transparent;")
        arow = QHBoxLayout(anchor_row)
        arow.setContentsMargins(0, 2, 0, 0)
        arow.setSpacing(4)
        arow.addWidget(_mini_label("ANCHOR SYNC"))
        arow.addStretch()
        self._anchor_set_btn = QPushButton("SET")
        self._anchor_set_btn.setFixedSize(42, 22)
        self._anchor_set_btn.setStyleSheet(_TURBO_OFF_STYLE)
        self._anchor_set_btn.setToolTip(
            "Place matching anchor points in each panel to enable\n"
            "offset sync for volumes with mismatched dimensions."
        )
        self._anchor_set_btn.clicked.connect(self._on_anchor_set_clicked)
        arow.addWidget(self._anchor_set_btn)
        self._anchor_apply_btn = QPushButton("APPLY")
        self._anchor_apply_btn.setFixedSize(42, 22)
        self._anchor_apply_btn.setStyleSheet(_TURBO_ON_STYLE)
        self._anchor_apply_btn.setToolTip("Activate offset sync using confirmed anchor points")
        self._anchor_apply_btn.clicked.connect(self._on_anchor_apply_clicked)
        self._anchor_apply_btn.hide()
        arow.addWidget(self._anchor_apply_btn)
        self._anchor_cancel_btn = QPushButton("✕")
        self._anchor_cancel_btn.setFixedSize(22, 22)
        self._anchor_cancel_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { color: #e06060; background: #2e2e2e; }"
        )
        self._anchor_cancel_btn.setToolTip("Cancel anchor placement")
        self._anchor_cancel_btn.clicked.connect(self._on_anchor_cancel_clicked)
        self._anchor_cancel_btn.hide()
        arow.addWidget(self._anchor_cancel_btn)
        body.addWidget(anchor_row)

        self._anchor_clear_btn = QPushButton("Clear anchors")
        self._anchor_clear_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 11px; padding: 3px 6px; }"
            "QPushButton:hover { color: #e06060; background: #2e2e2e; }"
        )
        self._anchor_clear_btn.clicked.connect(self.anchor_clear_requested)
        self._anchor_clear_btn.hide()
        body.addWidget(self._anchor_clear_btn)

    def _build_overlay_section(self):
        body = self._sec_overlay.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(5)

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

        self._overlay_channels = []
        self._overlay_colors   = [(136, 0, 0), (0, 0, 128), (255, 170, 0)]
        for ch_idx, color_idx in enumerate([0, 1, 2]):
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

    def _build_tags_section(self):
        body = self._sec_tags.body
        body.setContentsMargins(8, 8, 8, 6)
        body.setSpacing(4)

        self._show_tags_cb = QCheckBox("Show tags")
        self._show_tags_cb.setChecked(True)
        self._show_tags_cb.setStyleSheet("QCheckBox { color: #bbb; font-size: 12px; }")
        self._show_tags_cb.toggled.connect(self.tags_visible_changed)
        body.addWidget(self._show_tags_cb)

        self._tag_list_header = QLabel("Current: —")
        self._tag_list_header.setStyleSheet(
            "color: #666; font-size: 11px; padding: 1px 0;"
        )
        body.addWidget(self._tag_list_header)

        self._tag_list_content = QWidget()
        self._tag_list_content.setStyleSheet("background: #141414;")
        self._tag_list_col = QVBoxLayout(self._tag_list_content)
        self._tag_list_col.setContentsMargins(0, 2, 0, 2)
        self._tag_list_col.setSpacing(1)
        self._tag_list_empty_lbl = QLabel("No tags placed yet")
        self._tag_list_empty_lbl.setWordWrap(True)
        self._tag_list_empty_lbl.setStyleSheet(
            "color: #555; font-size: 11px; padding: 4px 6px;"
        )
        self._tag_list_col.addWidget(self._tag_list_empty_lbl)
        self._tag_list_col.addStretch()

        tag_scroll = QScrollArea()
        tag_scroll.setWidgetResizable(True)
        tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tag_scroll.setFixedHeight(100)
        tag_scroll.setStyleSheet("""
            QScrollArea { border: 1px solid #2e2e2e; background: #141414; }
            QScrollBar:vertical { background: #141414; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        tag_scroll.setWidget(self._tag_list_content)
        body.addWidget(tag_scroll)

    def _pick_overlay_color(self, idx: int):
        r, g, b = self._overlay_colors[idx]
        dlg = _ColorSwatchPicker(QColor(r, g, b), self)
        _, color_btn, _ = self._overlay_channels[idx]
        sidebar_x = self.mapToGlobal(QPoint(self.width() + 4, 0)).x()
        btn_y     = color_btn.mapToGlobal(color_btn.rect().center()).y()
        dlg.move(sidebar_x, btn_y - dlg.height() // 2)

        dlg.exec()
        color = dlg.chosen_color()
        if color is None:
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

    def _on_annot_btn_clicked(self, ft: str, btn: QPushButton,
                              checked: bool, group: QButtonGroup) -> None:
        if checked:
            for other in group.buttons():
                if other is not btn:
                    other.setChecked(False)
            value = BTN_TO_VALUE[btn.text()]
        else:
            value = ""
        self.annotation_changed.emit(ft, value)

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
            group.setExclusive(False)
            for text, color in (("Pass", "#1d4a27"), ("Rev", "#4a3c12"), ("Fail", "#4a1616")):
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setAutoExclusive(False)
                btn.setFixedHeight(18)
                btn.setStyleSheet(
                    "QPushButton { background: #252525; color: #888; border: none;"
                    f" border-radius: 2px; font-size: 12px; padding: 0 5px; }}"
                    f"QPushButton:checked {{ background: {color}; color: #ddd; }}"
                    "QPushButton:hover:!checked { background: #2e2e2e; color: #aaa; }"
                )
                group.addButton(btn)
                btn.clicked.connect(
                    lambda checked, b=btn, f=ft, grp=group:
                        self._on_annot_btn_clicked(f, b, checked, grp)
                )
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
                background: #181818; border: 1px solid #2e2e2e;
                color: #ccc; font-size: 12px;
            }
            QListWidget::item { padding: 3px 10px; border-bottom: 1px solid #1c1c1c; }
            QListWidget::item:selected { background: #147a3f; color: #fff; }
            QListWidget::item:hover:!selected { background: #1e1e1e; }
            QScrollBar:vertical { background: #181818; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: #181818; height: 8px; border: none; }
            QScrollBar::handle:horizontal {
                background: #3a3a3a; border-radius: 3px; min-width: 16px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        """)
        self._indiv_list.currentRowChanged.connect(self._on_row_changed)
        _WheelIsolator(self._indiv_list)
        body.addWidget(self._indiv_list)

        foot = QWidget()
        foot_row = QHBoxLayout(foot)
        foot_row.setContentsMargins(10, 3, 6, 1)
        foot_row.setSpacing(4)

        self._par_label = QLabel("No file loaded")
        self._par_label.setStyleSheet("color: #555; font-size: 11px; font-style: italic;")
        self._par_label.setWordWrap(True)
        foot_row.addWidget(self._par_label, stretch=1)

        self._par_refresh_btn = QPushButton("↻")
        self._par_refresh_btn.setFixedSize(18, 18)
        self._par_refresh_btn.setToolTip("Refresh PAR file")
        self._par_refresh_btn.setVisible(False)
        self._par_refresh_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 13px; padding: 0; }"
            "QPushButton:hover { color: #aaa; background: #2e2e2e; }"
        )
        self._par_refresh_btn.clicked.connect(self.par_refresh_requested)
        foot_row.addWidget(self._par_refresh_btn)

        body.addWidget(foot)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_individuals(self, individuals: list[Individual]):
        self._individuals = individuals
        self._current_idx = -1
        self._loaded_idx  = -1
        self._indiv_list.blockSignals(True)
        self._indiv_list.clear()
        for i, ind in enumerate(individuals):
            self._indiv_list.addItem(f"{i + 1}.  {ind.oldname}")
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
            self._loaded_idx  = idx
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

    def set_annotations(self, annots: dict[str, str]) -> None:
        """Restore annotation button states from a {file_type: value} dict."""
        for ft, group in self._annot_groups.items():
            target = VALUE_TO_BTN.get(annots.get(ft, ""), "")
            for btn in group.buttons():
                btn.setChecked(btn.text() == target)

    def set_par_label(self, path: Path | None):
        if path is None:
            self._par_label.setText("No file loaded")
            self._par_label.setStyleSheet("color: #666; font-size: 12px; font-style: italic;")
            self._par_refresh_btn.setVisible(False)
        else:
            self._par_label.setText(path.name)
            self._par_label.setStyleSheet("color: #aaa; font-size: 12px; font-style: normal;")
            self._par_refresh_btn.setVisible(True)

    def update_tag_list(self, tags: list, file_type: str) -> None:
        if file_type:
            label = FILE_TYPE_LABELS.get(file_type, file_type)
            self._tag_list_header.setText(f"Current: {label}")
        else:
            self._tag_list_header.setText("Current: —")

        while self._tag_list_col.count():
            item = self._tag_list_col.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tags:
            self._tag_list_empty_lbl = QLabel("No tags placed yet")
            self._tag_list_empty_lbl.setWordWrap(True)
            self._tag_list_empty_lbl.setStyleSheet(
                "color: #555; font-size: 11px; padding: 4px 6px;"
            )
            self._tag_list_col.addWidget(self._tag_list_empty_lbl)
            self._tag_list_col.addStretch()
            return

        for i, tag in enumerate(tags):
            preview = tag.note[:20] + "…" if len(tag.note) > 20 else (tag.note or "—")
            btn = QPushButton(f"#{i + 1}  {preview}")
            btn.setFixedHeight(22)
            coord_tip = f"({tag.x}, {tag.y}, {tag.z})"
            btn.setToolTip(f"{coord_tip}\n{tag.note}" if tag.note else coord_tip)
            btn.setStyleSheet(
                f"QPushButton {{ background: #141414; color: #ccc; border: none;"
                f" border-left: 3px solid {tag.color}; padding: 1px 6px;"
                f" text-align: left; font-size: 11px; }}"
                "QPushButton:hover { background: #1e2a20; color: #eee; }"
            )
            btn.clicked.connect(
                lambda _, t=tag, ft=file_type:
                    self.tag_selected.emit(ft, t.x, t.y, t.z, t.id)
            )
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, t=tag, ft=file_type, b=btn:
                    self._show_tag_context_menu(b.mapToGlobal(pos), ft, t.id)
            )
            self._tag_list_col.addWidget(btn)
        self._tag_list_col.addStretch()

    def apply_saved_settings(self, turbo_stride: int, checked_types: list[str]) -> None:
        """Restore persisted user preferences on startup."""
        _strides = [1, 2, 4]
        _labels  = ["NONE", "2×", "4×"]
        self._turbo_idx = _strides.index(turbo_stride) if turbo_stride in _strides else 0
        self._turbo_btn.setText(_labels[self._turbo_idx])
        self._turbo_btn.setStyleSheet(
            _TURBO_OFF_STYLE if turbo_stride == 1 else _TURBO_ON_STYLE
        )
        for ft, cb in self._file_checks.items():
            cb.blockSignals(True)
            cb.setChecked(ft in checked_types)
            cb.blockSignals(False)
        self._update_file_count()

    def set_composite_pending(self, pending: bool):
        if pending:
            self._create_composite_btn.setText("Queued…")
            self._create_composite_btn.setStyleSheet(
                "QPushButton { background: #2a2a18; color: #c8b84a;"
                " border: 1px solid #6e6012;"
                " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
                "QPushButton:hover { background: #3a3a20; color: #ffe066;"
                " border-color: #9e8a1a; }"
            )
        else:
            self._create_composite_btn.setText("Create Composite")
            self._create_composite_btn.setStyleSheet(
                "QPushButton { background: #1a3d26; color: #5fd49a;"
                " border: 1px solid #2e6e42;"
                " border-radius: 3px; padding: 5px 8px; font-size: 12px; }"
                "QPushButton:hover { background: #147a3f; color: #fff;"
                " border-color: #3a8a52; }"
            )

    def set_controls_enabled(self, enabled: bool):
        # Navigation stays active so the user can cancel a slow load by moving elsewhere.
        # Only the file selector and composite creation are locked while loading.
        for ft, cb in self._file_checks.items():
            cb.setEnabled(enabled and self._file_available.get(ft, False))
        self._apply_btn.setEnabled(enabled and self._current_idx >= 0)
        self._create_composite_btn.setEnabled(enabled)

    def update_anchor_state(self, active: bool, has_anchors: bool):
        """Reflect current anchor mode in the sidebar buttons."""
        if active:
            self._anchor_set_btn.hide()
            self._anchor_apply_btn.show()
            self._anchor_cancel_btn.show()
            self._anchor_clear_btn.hide()
        else:
            self._anchor_set_btn.show()
            self._anchor_apply_btn.hide()
            self._anchor_cancel_btn.hide()
            self._anchor_clear_btn.setVisible(has_anchors)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_anchor_set_clicked(self):
        self._anchor_set_btn.hide()
        self._anchor_apply_btn.show()
        self._anchor_cancel_btn.show()
        self.anchor_mode_requested.emit()

    def _on_anchor_apply_clicked(self):
        self._anchor_apply_btn.hide()
        self._anchor_cancel_btn.hide()
        self._anchor_set_btn.show()
        self._anchor_clear_btn.show()
        self.anchor_apply_requested.emit()

    def _on_anchor_cancel_clicked(self):
        self._anchor_apply_btn.hide()
        self._anchor_cancel_btn.hide()
        self._anchor_set_btn.show()
        self.anchor_cancel_requested.emit()

    def _show_tag_context_menu(self, global_pos, file_type: str, tag_id: str):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #ccc; border: 1px solid #444; }"
            "QMenu::item:selected { background: #147a3f; }"
        )
        edit_act   = menu.addAction("Edit")
        delete_act = menu.addAction("Delete")
        action = menu.exec(global_pos)
        if action == edit_act:
            self.tag_edit_requested.emit(file_type, tag_id)
        elif action == delete_act:
            self.tag_delete_requested.emit(file_type, tag_id)

    def _update_file_count(self):
        n = sum(1 for cb in self._file_checks.values() if cb.isChecked())
        self._file_count_lbl.setText(f"{n} selected")

    def _on_turbo_cycle(self):
        _labels  = ["NONE", "2×", "4×"]
        _strides = [1, 2, 4]
        self._turbo_idx = (self._turbo_idx + 1) % 3
        label  = _labels[self._turbo_idx]
        stride = _strides[self._turbo_idx]
        self._turbo_btn.setText(label)
        self._turbo_btn.setStyleSheet(
            _TURBO_OFF_STYLE if stride == 1 else _TURBO_ON_STYLE
        )
        self.turbo_changed.emit(stride)

    def _on_sync_toggled(self, checked: bool):
        self._sync_btn.setText("ON" if checked else "OFF")
        self.sync_toggled.emit(checked)

    def _set_all_sections(self, expanded: bool):
        for sec in (self._sec_file, self._sec_display,
                    self._sec_overlay, self._sec_tags, self._sec_annot, self._sec_indiv):
            if sec._expanded != expanded:
                sec._toggle()

    def _on_apply(self):
        selected = [ft for ft, cb in self._file_checks.items() if cb.isChecked()]
        if self._current_idx != self._loaded_idx:
            self._loaded_idx = self._current_idx
            self.load_requested.emit(self._current_idx, selected)
        else:
            self.files_applied.emit(selected)

    def _browse_par(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PAR or CSV file", "",
            "PAR / CSV files (*.par *.csv);;All files (*)"
        )
        if path:
            self.par_selected.emit(Path(path))

    def _browse_scan(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select individual scan", "",
            "MHD files (*.mhd);;All files (*)"
        )
        if path:
            self.scan_selected.emit(Path(path))

    def _browse_manual(self):
        file_types = self.checked_file_types() or list(FILE_TYPE_ORDER)
        dlg = _ManualFileSelectDialog(file_types, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            paths = dlg.selected_paths
            if paths:
                self.manual_files_selected.emit(paths)

    def checked_file_types(self) -> list[str]:
        return [ft for ft, cb in self._file_checks.items() if cb.isChecked()]

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._current_idx = row
        n = len(self._individuals)
        self._counter.setText(f"{row + 1} / {n}")
        self._refresh_nav(row)
        if self._nav_triggered or self._loaded_idx >= 0:
            self._nav_triggered = False
            self._loaded_idx = row
            self.individual_selected.emit(row)

    def _go_prev(self):
        if self._current_idx > 0:
            self._nav_triggered = True
            self._indiv_list.setCurrentRow(self._current_idx - 1)

    def _go_next(self):
        if self._current_idx < len(self._individuals) - 1:
            self._nav_triggered = True
            self._indiv_list.setCurrentRow(self._current_idx + 1)

    def _refresh_nav(self, row: int):
        n = len(self._individuals)
        self._prev_btn.setEnabled(row > 0)
        self._next_btn.setEnabled(0 <= row < n - 1)
