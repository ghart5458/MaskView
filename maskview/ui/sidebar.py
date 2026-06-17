from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QRadioButton, QScrollArea, QSlider, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QVBoxLayout, QWidget,
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
    " border-radius: 3px; font-size: 12px; font-weight: bold; }"
    "QPushButton:hover { color: #999; background: #2e2e2e; }"
)
_TURBO_ON_STYLE = (
    "QPushButton { background: #0f2a1a; color: #2ce67f; border: 1px solid #147a3f;"
    " border-radius: 3px; font-size: 12px; font-weight: bold; }"
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
        "color: #888; font-size: 13px; font-weight: bold;"
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
        self._hdr.setFixedHeight(30)
        self._hdr.setStyleSheet(
            "QWidget { background: #1f1f1f; }"
            "QWidget:hover { background: #242424; }"
        )
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hrow = QHBoxLayout(self._hdr)
        hrow.setContentsMargins(8, 0, 8, 0)
        hrow.setSpacing(6)

        self._arrow = QLabel("▾" if expanded else "▸")
        self._arrow.setStyleSheet("color: #777; font-size: 13px;")
        self._arrow.setFixedWidth(10)

        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setStyleSheet(
            "color: #999; font-size: 13px; font-weight: bold; letter-spacing: 1px;"
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
        intro.setStyleSheet("color: #aaa; font-size: 13px; padding-bottom: 2px;")
        layout.addWidget(intro)

        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)
        gl.setColumnStretch(1, 1)

        self._path_labels: dict = {}
        for i, ft in enumerate(file_types):
            type_lbl = QLabel(FILE_TYPE_LABELS.get(ft, ft))
            type_lbl.setStyleSheet("color: #ccc; font-size: 13px;")
            type_lbl.setFixedWidth(90)

            path_lbl = QLabel("—")
            path_lbl.setStyleSheet("color: #555; font-size: 12px; font-style: italic;")
            self._path_labels[ft] = path_lbl

            browse_btn = QPushButton("Browse…")
            browse_btn.setFixedWidth(72)
            browse_btn.setStyleSheet(
                "QPushButton { background: #252525; color: #999; border: 1px solid #3a3a3a;"
                " border-radius: 3px; font-size: 12px; padding: 2px 6px; }"
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
            " border-radius: 3px; padding: 4px 12px; font-size: 13px; }"
            "QPushButton:hover { background: #2e2e2e; color: #ddd; }"
        )
        cancel_btn.clicked.connect(self.reject)
        load_btn = QPushButton("Load")
        load_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a; border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 4px 12px; font-size: 13px; }"
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
                "color: #aaa; font-size: 12px; font-style: normal;"
            )

    @property
    def selected_paths(self) -> dict:
        return dict(self._paths)


_DOT_ROLE   = Qt.ItemDataRole.UserRole + 1
_GRAY_ROLE  = Qt.ItemDataRole.UserRole + 2
_ANNOT_ROLE = Qt.ItemDataRole.UserRole + 3

_DOT_COLORS = {
    'cached':  QColor('#1aad5e'),
    'loading': QColor('#c8b84a'),
}

_ANNOT_COLORS = {
    'Pass':   QColor('#1aad5e'),
    'Review': QColor('#c8a84a'),
    'Fail':   QColor('#cc3333'),
}


class _PreloadDotDelegate(QStyledItemDelegate):
    """Draws a small status dot and annotation indicator on each list item."""

    def paint(self, painter, option, index):
        grayed = index.data(_GRAY_ROLE)
        if grayed:
            opt = QStyleOptionViewItem(option)
            is_sel = bool(opt.state & QStyle.StateFlag.State_Selected)
            if not is_sel:
                opt.palette.setColor(QPalette.ColorGroup.Normal,
                                     QPalette.ColorRole.Text, QColor("#4a4a4a"))
            super().paint(painter, opt, index)
        else:
            super().paint(painter, option, index)

        r   = option.rect
        ds  = 8    # preload square side  (was 6px circle)
        das = 10   # annotation square side (was 8px circle)
        gap = 5    # gap between the two squares
        x   = r.right() - ds - 5
        y   = r.top() + (r.height() - ds) // 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── Annotation square (to the left of the preload square) ────────
        annot       = index.data(_ANNOT_ROLE)
        annot_color = _ANNOT_COLORS.get(annot)
        if annot_color is not None:
            xa  = x - 1 - gap - das   # 1=halo margin
            ya  = r.top() + (r.height() - das) // 2
            cx  = xa + das // 2
            cy  = ya + das // 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(annot_color)
            painter.drawRoundedRect(xa, ya, das, das, 1.5, 1.5)
            sym_pen = QPen(QColor("#ffffff"), 1.5)
            sym_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            sym_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(sym_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if annot == "Pass":
                painter.drawLine(cx - 3, cy - 1, cx - 1, cy + 2)
                painter.drawLine(cx - 1, cy + 2, cx + 3, cy - 2)
            elif annot == "Review":
                painter.drawLine(cx, cy - 3, cx, cy + 1)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(cx - 1, cy + 3, 2, 2)
            elif annot == "Fail":
                painter.drawLine(cx - 3, cy - 3, cx + 3, cy + 3)
                painter.drawLine(cx + 3, cy - 3, cx - 3, cy + 3)

        # ── Preload square (always drawn; outline only when not queued) ───
        status    = index.data(_DOT_ROLE)
        dot_color = _DOT_COLORS.get(status)
        if dot_color is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 200))
            painter.drawRoundedRect(x - 1, y - 1, ds + 2, ds + 2, 2.0, 2.0)
            painter.setBrush(dot_color)
            painter.drawRoundedRect(x, y, ds, ds, 1.5, 1.5)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#3a3a3a"), 1.0))
            painter.drawRoundedRect(x, y, ds, ds, 1.5, 1.5)

        painter.restore()


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
    annotation_note_changed = pyqtSignal(str)        # note text for current individual
    filter_changed          = pyqtSignal(str)        # "All", "Pass", "Review", "Fail"
    export_annotations_requested = pyqtSignal()
    clear_annotations_requested  = pyqtSignal()
    export_tags_requested   = pyqtSignal()
    tags_visible_changed      = pyqtSignal(bool)
    tag_selected              = pyqtSignal(str, int, int, int, str)  # (file_type, x, y, z, tag_id)
    tag_edit_requested        = pyqtSignal(str, str)   # (file_type, tag_id)
    tag_delete_requested      = pyqtSignal(str, str)   # (file_type, tag_id)
    tags_delete_many_requested = pyqtSignal(str, list) # (file_type, [tag_ids])
    tags_clear_requested      = pyqtSignal(str)        # file_type
    tags_clear_all_requested  = pyqtSignal()
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
        self._note_draft_idx: int = -1  # which individual owns the in-progress note draft
        self._tag_list_file_type: str = ""
        self._filter_mode: str = "All"
        self._filtered_indices: list[int] = []
        self._filtered_set: set[int] = set()
        self._filter_btns: dict[str, QPushButton] = {}
        self._export_annot_btn: QPushButton | None = None
        self._clear_all_annot_btn: QPushButton | None = None

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
            " font-size: 13px; padding: 0; }"
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
        _div.setStyleSheet("color: #444; font-size: 13px;")
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
            " border-radius: 3px; font-size: 13px; padding: 2px 6px; }"
            "QPushButton:hover:enabled { background: #2c2c2c; color: #ddd; }"
            "QPushButton:disabled { color: #3a3a3a; }"
        )
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(30)
        self._prev_btn.setStyleSheet(_btn_style)
        self._prev_btn.clicked.connect(self._go_prev)
        self._counter = QLabel("— / —")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet("color: #888; font-size: 13px;")
        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(30)
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
            " border-radius: 3px; padding: 5px 8px; font-size: 13px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        _grp_lbl_style = "color: #666; font-size: 12px; font-style: italic; padding: 2px 0 1px 0;"
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
            "QCheckBox { color: #888; font-size: 13px; padding: 1px 0; }"
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
            "color: #666; font-size: 12px; padding: 0 2px 0 0;"
        )
        body.addWidget(self._file_count_lbl)
        self._update_file_count()

        body.addWidget(_sep())
        self._apply_btn = QPushButton("Load")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 5px 8px; font-size: 13px; }"
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
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 13px; }")
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
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 13px; }")
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
        self._turbo_btn.setFixedSize(44, 24)
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
        self._sync_btn.setFixedSize(44, 24)
        self._sync_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 12px; font-weight: bold; }"
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
        self._anchor_set_btn.setFixedSize(44, 24)
        self._anchor_set_btn.setStyleSheet(_TURBO_OFF_STYLE)
        self._anchor_set_btn.setToolTip(
            "Place matching anchor points in each panel to enable\n"
            "offset sync for volumes with mismatched dimensions."
        )
        self._anchor_set_btn.clicked.connect(self._on_anchor_set_clicked)
        arow.addWidget(self._anchor_set_btn)
        self._anchor_apply_btn = QPushButton("APPLY")
        self._anchor_apply_btn.setFixedSize(44, 24)
        self._anchor_apply_btn.setStyleSheet(_TURBO_ON_STYLE)
        self._anchor_apply_btn.setToolTip("Activate offset sync using confirmed anchor points")
        self._anchor_apply_btn.clicked.connect(self._on_anchor_apply_clicked)
        self._anchor_apply_btn.hide()
        arow.addWidget(self._anchor_apply_btn)
        self._anchor_cancel_btn = QPushButton("✕")
        self._anchor_cancel_btn.setFixedSize(24, 24)
        self._anchor_cancel_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 12px; font-weight: bold; }"
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
            " border-radius: 3px; font-size: 12px; padding: 3px 6px; }"
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
            " border-radius: 2px; font-size: 12px; padding: 1px 3px; }"
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
        blend_lbl.setStyleSheet("color: #888; font-size: 13px;")
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
            " border-radius: 3px; padding: 5px 8px; font-size: 13px; }"
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
        self._show_tags_cb.setStyleSheet("QCheckBox { color: #bbb; font-size: 13px; }")
        self._show_tags_cb.toggled.connect(self.tags_visible_changed)
        body.addWidget(self._show_tags_cb)

        self._tag_list_header = QLabel("Current: —")
        self._tag_list_header.setStyleSheet("color: #666; font-size: 12px; padding: 1px 0;")
        body.addWidget(self._tag_list_header)

        self._tag_list = QListWidget()
        self._tag_list.setFixedHeight(100)
        self._tag_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._tag_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tag_list.setStyleSheet("""
            QListWidget {
                background: #141414; border: 1px solid #2e2e2e;
                color: #ccc; font-size: 12px; outline: none;
            }
            QListWidget::item { padding: 2px 6px; border-bottom: 1px solid #1c1c1c; }
            QListWidget::item:selected { background: #1a3d26; color: #fff; }
            QListWidget::item:hover:!selected { background: #1e1e1e; }
            QScrollBar:vertical { background: #141414; width: 8px; border: none; }
            QScrollBar::handle:vertical {
                background: #3a3a3a; border-radius: 3px; min-height: 16px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self._tag_list.customContextMenuRequested.connect(self._on_tag_context_menu)
        self._tag_list.itemClicked.connect(self._on_tag_item_clicked)
        _WheelIsolator(self._tag_list)
        body.addWidget(self._tag_list)

        _btn_style_green = (
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; border-color: #3a8a52; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
        )
        self._export_btn_normal_style = _btn_style_green
        _export_done_style = (
            "QPushButton { background: #1a1a1a; color: #555; border: 1px solid #2a2a2a;"
            " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #222; color: #888; border-color: #3a3a3a; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
        )
        _btn_style_red = (
            "QPushButton { background: #2a1010; color: #e07878;"
            " border: 1px solid #6e2020;"
            " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #4a1a1a; color: #ff9090; border-color: #9e3030; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
        )
        _btn_style_yellow = (
            "QPushButton { background: #2a2410; color: #d4c45e;"
            " border: 1px solid #6e5e20;"
            " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #3a3418; color: #ffe066; border-color: #9e8a1a; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
        )

        self._clear_tags_btn = QPushButton("Clear tags")
        self._clear_tags_btn.setStyleSheet(_btn_style_red)
        self._clear_tags_btn.setEnabled(False)
        self._clear_tags_btn.setToolTip(
            "Remove all tags for the currently displayed file type"
        )
        self._clear_tags_btn.clicked.connect(
            lambda: self.tags_clear_requested.emit(self._tag_list_file_type)
        )
        body.addWidget(self._clear_tags_btn)

        body.addWidget(_sep())

        self._export_tags_btn = QPushButton("Export tags for all individuals")
        self._export_tags_btn.setStyleSheet(_btn_style_green)
        self._export_tags_btn.setEnabled(False)

        def _on_export_click():
            reply = QMessageBox.question(
                self, "Export tags",
                "Export all tags for this PAR to CSV?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Ok:
                self.export_tags_requested.emit()
                self._export_tags_btn.setText("Exported.")
                self._export_tags_btn.setStyleSheet(_export_done_style)

        self._export_tags_btn.clicked.connect(_on_export_click)
        body.addWidget(self._export_tags_btn)

        self._clear_all_tags_btn = QPushButton("Clear tags for all individuals")
        self._clear_all_tags_btn.setStyleSheet(_btn_style_yellow)
        self._clear_all_tags_btn.setEnabled(False)
        self._clear_all_tags_btn.setToolTip(
            "Permanently delete all tags across every individual and file type"
        )
        self._clear_all_tags_btn.clicked.connect(self.tags_clear_all_requested)
        body.addWidget(self._clear_all_tags_btn)

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

        # ── One-time: build stable Export / Clear buttons at the bottom ──────
        if self._export_annot_btn is None:
            body.setContentsMargins(8, 8, 8, 6)
            body.setSpacing(4)

            # Container for the dynamic (per-file-type + note) content
            self._annot_dynamic_widget = QWidget()
            self._annot_dynamic_widget.setStyleSheet("background: transparent;")
            self._annot_dynamic_layout = QVBoxLayout(self._annot_dynamic_widget)
            self._annot_dynamic_layout.setContentsMargins(0, 0, 0, 0)
            self._annot_dynamic_layout.setSpacing(4)
            body.addWidget(self._annot_dynamic_widget)

            body.addWidget(_sep())

            _btn_style_green = (
                "QPushButton { background: #1a3d26; color: #5fd49a;"
                " border: 1px solid #2e6e42;"
                " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
                "QPushButton:hover { background: #147a3f; color: #fff; border-color: #3a8a52; }"
                "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
            )
            _btn_style_yellow = (
                "QPushButton { background: #2a2410; color: #d4c45e;"
                " border: 1px solid #6e5e20;"
                " border-radius: 3px; padding: 4px 8px; font-size: 12px; }"
                "QPushButton:hover { background: #3a3418; color: #ffe066; border-color: #9e8a1a; }"
                "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; border-color: #252525; }"
            )

            self._export_annot_btn = QPushButton("Export annotations for all individuals")
            self._export_annot_btn.setStyleSheet(_btn_style_green)
            self._export_annot_btn.setEnabled(False)
            self._export_annot_btn.clicked.connect(self.export_annotations_requested)
            body.addWidget(self._export_annot_btn)

            self._clear_all_annot_btn = QPushButton("Clear annotations for all individuals")
            self._clear_all_annot_btn.setStyleSheet(_btn_style_yellow)
            self._clear_all_annot_btn.setEnabled(False)
            self._clear_all_annot_btn.setToolTip(
                "Selectively clear annotation data across every individual"
            )
            self._clear_all_annot_btn.clicked.connect(self.clear_annotations_requested)
            body.addWidget(self._clear_all_annot_btn)

        # ── Rebuild the dynamic (per-file-type rows + note) part ─────────────
        dyn = self._annot_dynamic_layout

        # Preserve unsaved note text only when rebuilding for the same individual
        # (e.g. second file finishes loading while user is mid-sentence). Discard
        # if the user navigated to a different individual.
        _draft = ""
        if hasattr(self, "_note_edit") and self._current_idx == self._note_draft_idx:
            _draft = self._note_edit.toPlainText()
        self._note_draft_idx = self._current_idx

        while dyn.count():
            item = dyn.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._annot_groups.clear()

        if not file_types:
            lbl = QLabel("No panels open")
            lbl.setStyleSheet("color: #666; font-size: 13px;")
            dyn.addWidget(lbl)

        for ft in file_types:
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rrow = QHBoxLayout(row_w)
            rrow.setContentsMargins(0, 1, 0, 1)
            rrow.setSpacing(3)

            lbl = QLabel(FILE_TYPE_LABELS.get(ft, ft))
            lbl.setStyleSheet("color: #aaa; font-size: 13px;")
            rrow.addWidget(lbl, stretch=1)

            group = QButtonGroup(row_w)
            group.setExclusive(False)
            for text, color in (("Pass", "#1d4a27"), ("Rev", "#4a3c12"), ("Fail", "#4a1616")):
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setAutoExclusive(False)
                btn.setFixedHeight(20)
                btn.setStyleSheet(
                    "QPushButton { background: #252525; color: #888; border: none;"
                    f" border-radius: 2px; font-size: 13px; padding: 0 5px; }}"
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
            dyn.addWidget(row_w)

        dyn.addWidget(_sep())
        note_lbl = QLabel("Individual note:")
        note_lbl.setStyleSheet("color: #888; font-size: 12px; padding: 2px 0 1px 0;")
        dyn.addWidget(note_lbl)

        self._note_edit = QPlainTextEdit()
        self._note_edit.setFixedHeight(48)   # ~2 lines
        self._note_edit.setStyleSheet(
            "QPlainTextEdit { background: #141414; color: #ccc; border: 1px solid #2e2e2e;"
            " border-radius: 2px; font-size: 13px; padding: 2px 4px; }"
            "QPlainTextEdit:focus { border-color: #3a8a52; }"
        )
        if _draft:
            self._note_edit.setPlainText(_draft)
        dyn.addWidget(self._note_edit)

        _save_normal = (
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42;"
            " border-radius: 3px; padding: 3px 8px; font-size: 12px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; border-color: #3a8a52; }"
        )
        _save_saved = (
            "QPushButton { background: #1e1e1e; color: #555;"
            " border: 1px solid #2a2a2a;"
            " border-radius: 3px; padding: 3px 8px; font-size: 12px; }"
        )
        self._note_save_btn = QPushButton("Save note")
        self._note_save_btn.setStyleSheet(_save_normal)

        def _on_save_note():
            self.annotation_note_changed.emit(self._note_edit.toPlainText())
            self._note_save_btn.setText("Saved.")
            self._note_save_btn.setStyleSheet(_save_saved)

        def _on_note_text_changed():
            if self._note_save_btn.text() == "Saved.":
                self._note_save_btn.setText("Save note")
                self._note_save_btn.setStyleSheet(_save_normal)

        self._note_save_btn.clicked.connect(_on_save_note)
        self._note_edit.textChanged.connect(_on_note_text_changed)
        dyn.addWidget(self._note_save_btn)

    def _build_filter_row(self) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 5, 8, 2)
        layout.setSpacing(4)

        lbl = QLabel("Show:")
        lbl.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(lbl)

        group = QButtonGroup(row)
        group.setExclusive(True)

        _FILTER_STYLES = {
            "All":    ("background:#252525; color:#999;",    "background:#444;    color:#eee;"),
            "Pass":   ("background:#0f2a1a; color:#5fd49a;", "background:#1a5a2a; color:#7eeab0;"),
            "Review": ("background:#2a2010; color:#c8a84a;", "background:#5a4a10; color:#e8c860;"),
            "Fail":   ("background:#2a1010; color:#e05555;", "background:#5a1a14; color:#ff7070;"),
        }
        for label, (normal, checked) in _FILTER_STYLES.items():
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QPushButton {{ {normal} border:none; border-radius:3px;"
                f" font-size:11px; padding:2px 7px; }}"
                f"QPushButton:checked {{ {checked} }}"
                f"QPushButton:hover:!checked {{ background:#2e2e2e; color:#bbb; }}"
            )
            group.addButton(btn)
            self._filter_btns[label] = btn
            layout.addWidget(btn)

        self._filter_btns["All"].setChecked(True)
        layout.addStretch()
        group.buttonToggled.connect(self._on_filter_toggled)
        return row

    def _on_filter_toggled(self, btn: QPushButton, checked: bool) -> None:
        if not checked:
            return
        for label, b in self._filter_btns.items():
            if b is btn:
                self._filter_mode = label
                self.filter_changed.emit(label)
                return

    def _build_individuals_section(self):
        body = self._sec_indiv.body
        body.setContentsMargins(0, 2, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._build_filter_row())

        self._indiv_list = QListWidget()
        self._indiv_list.setUniformItemSizes(True)
        self._indiv_list.setFixedHeight(115)
        self._indiv_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self._indiv_list.setStyleSheet("""
            QListWidget {
                background: #181818; border: 1px solid #2e2e2e;
                color: #ccc; font-size: 13px;
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
        self._indiv_list.setItemDelegate(_PreloadDotDelegate(self._indiv_list))
        self._indiv_list.currentRowChanged.connect(self._on_row_changed)
        _WheelIsolator(self._indiv_list)
        body.addWidget(self._indiv_list)

        foot = QWidget()
        foot_row = QHBoxLayout(foot)
        foot_row.setContentsMargins(10, 3, 6, 1)
        foot_row.setSpacing(4)

        self._par_label = QLabel("No file loaded")
        self._par_label.setStyleSheet("color: #555; font-size: 12px; font-style: italic;")
        self._par_label.setWordWrap(True)
        foot_row.addWidget(self._par_label, stretch=1)

        self._par_refresh_btn = QPushButton("↻")
        self._par_refresh_btn.setFixedSize(20, 20)
        self._par_refresh_btn.setToolTip("Refresh PAR file")
        self._par_refresh_btn.setVisible(False)
        self._par_refresh_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #666; border: 1px solid #333;"
            " border-radius: 3px; font-size: 14px; padding: 0; }"
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
        # Reset filter to All
        self._filter_mode = "All"
        self._filtered_indices = list(range(len(individuals)))
        self._filtered_set = set(self._filtered_indices)
        if self._filter_btns:
            self._filter_btns["All"].blockSignals(True)
            self._filter_btns["All"].setChecked(True)
            self._filter_btns["All"].blockSignals(False)
        self._indiv_list.blockSignals(True)
        self._indiv_list.clear()
        for i, ind in enumerate(individuals):
            item = QListWidgetItem(f"{i + 1}.  {ind.oldname}")
            self._indiv_list.addItem(item)
        self._indiv_list.blockSignals(False)
        self._counter.setText("— / —")
        self._refresh_nav(-1)
        self._apply_btn.setEnabled(False)

    def update_preload_indicators(self, cached: set, loading: set):
        for i in range(self._indiv_list.count()):
            item = self._indiv_list.item(i)
            if item is None:
                continue
            if i in cached:
                item.setData(_DOT_ROLE, 'cached')
            elif i in loading:
                item.setData(_DOT_ROLE, 'loading')
            else:
                item.setData(_DOT_ROLE, None)

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
            self._update_filter_counter()
            self._refresh_nav(idx)
            self._apply_btn.setEnabled(True)

    @property
    def filter_mode(self) -> str:
        return self._filter_mode

    @property
    def filtered_indices(self) -> list[int]:
        return list(self._filtered_indices)

    def apply_filter(self, filter_mode: str, matching_indices: list[int]) -> None:
        """Gray out non-matching individuals and update counter/nav. Called by MainWindow."""
        self._filter_mode = filter_mode
        self._filtered_indices = sorted(matching_indices)
        self._filtered_set = set(matching_indices)
        for i in range(self._indiv_list.count()):
            item = self._indiv_list.item(i)
            if item is None:
                continue
            item.setData(_GRAY_ROLE, None if (filter_mode == "All" or i in self._filtered_set) else True)
        self._update_filter_counter()
        self._refresh_nav(self._current_idx)

    def set_annotation_indicator(self, idx: int, value: str) -> None:
        item = self._indiv_list.item(idx)
        if item is not None:
            item.setData(_ANNOT_ROLE, value or None)

    def set_all_annotation_indicators(self, indicators: dict) -> None:
        for i in range(self._indiv_list.count()):
            item = self._indiv_list.item(i)
            if item is None:
                continue
            v = indicators.get(i, "")
            item.setData(_ANNOT_ROLE, v or None)

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

    def set_annotation_note(self, text: str) -> None:
        """Populate the note text box from disk. Skips if the box already has
        content — that means _build_annotations_section restored a live draft
        for this same individual and we should not clobber it."""
        if self._note_edit.toPlainText():
            return
        self._note_edit.blockSignals(True)
        self._note_edit.setPlainText(text)
        self._note_edit.blockSignals(False)

    def force_clear_annotation_note(self) -> None:
        """Force-clear the note box (used after a bulk note clear)."""
        self._note_edit.blockSignals(True)
        self._note_edit.setPlainText("")
        self._note_edit.blockSignals(False)
        self._note_draft_idx = -1

    def set_par_label(self, path: Path | None):
        has_par = path is not None
        if not has_par:
            self._par_label.setText("No file loaded")
            self._par_label.setStyleSheet("color: #666; font-size: 13px; font-style: italic;")
            self._par_refresh_btn.setVisible(False)
        else:
            self._par_label.setText(path.name)
            self._par_label.setStyleSheet("color: #aaa; font-size: 13px; font-style: normal;")
            self._par_refresh_btn.setVisible(True)
        self._export_tags_btn.setEnabled(has_par)
        if has_par:
            self._export_tags_btn.setText("Export tags for all individuals")
            self._export_tags_btn.setStyleSheet(self._export_btn_normal_style)
        self._clear_all_tags_btn.setEnabled(has_par)
        if self._export_annot_btn is not None:
            self._export_annot_btn.setEnabled(has_par)
        if self._clear_all_annot_btn is not None:
            self._clear_all_annot_btn.setEnabled(has_par)

    def update_tag_list(self, tags: list, file_type: str) -> None:
        self._tag_list_file_type = file_type
        if file_type:
            self._tag_list_header.setText(
                f"Current: {FILE_TYPE_LABELS.get(file_type, file_type)}"
            )
        else:
            self._tag_list_header.setText("Current: —")

        self._tag_list.clear()
        for i, tag in enumerate(tags):
            item = QListWidgetItem(f"#{i + 1}  {tag.note or '—'}")
            coord_tip = f"({tag.x}, {tag.y}, {tag.z})"
            item.setToolTip(f"{coord_tip}\n{tag.note}" if tag.note else coord_tip)
            item.setForeground(QColor(tag.color))
            item.setData(Qt.ItemDataRole.UserRole, (file_type, tag.x, tag.y, tag.z, tag.id))
            self._tag_list.addItem(item)

        self._clear_tags_btn.setEnabled(len(tags) > 0)

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
                " border-radius: 3px; padding: 5px 8px; font-size: 13px; }"
                "QPushButton:hover { background: #3a3a20; color: #ffe066;"
                " border-color: #9e8a1a; }"
            )
        else:
            self._create_composite_btn.setText("Create Composite")
            self._create_composite_btn.setStyleSheet(
                "QPushButton { background: #1a3d26; color: #5fd49a;"
                " border: 1px solid #2e6e42;"
                " border-radius: 3px; padding: 5px 8px; font-size: 13px; }"
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

    def _on_tag_item_clicked(self, item: QListWidgetItem):
        ft, x, y, z, tag_id = item.data(Qt.ItemDataRole.UserRole)
        self.tag_selected.emit(ft, x, y, z, tag_id)

    def _on_tag_context_menu(self, pos):
        selected = self._tag_list.selectedItems()
        if not selected:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #ccc; border: 1px solid #444; }"
            "QMenu::item:selected { background: #147a3f; }"
        )
        edit_act = menu.addAction("Edit") if len(selected) == 1 else None
        delete_act = menu.addAction("Delete")
        action = menu.exec(self._tag_list.mapToGlobal(pos))
        if action is None:
            return
        ft = selected[0].data(Qt.ItemDataRole.UserRole)[0]
        if edit_act is not None and action == edit_act:
            tag_id = selected[0].data(Qt.ItemDataRole.UserRole)[4]
            self.tag_edit_requested.emit(ft, tag_id)
        elif action == delete_act:
            ids = [item.data(Qt.ItemDataRole.UserRole)[4] for item in selected]
            if len(ids) == 1:
                self.tag_delete_requested.emit(ft, ids[0])
            else:
                self.tags_delete_many_requested.emit(ft, ids)

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

    def _update_filter_counter(self) -> None:
        row = self._current_idx
        n = len(self._individuals)
        if n == 0:
            self._counter.setText("— / —")
            return
        if self._filter_mode == "All":
            self._counter.setText(f"{row + 1} / {n}" if row >= 0 else "— / —")
        else:
            total = len(self._filtered_indices)
            if row in self._filtered_set:
                pos = self._filtered_indices.index(row) + 1
                self._counter.setText(f"{pos} / {total}")
            else:
                self._counter.setText(f"— / {total}")

    def _prev_in_filter(self) -> int | None:
        if self._filter_mode == "All":
            return self._current_idx - 1 if self._current_idx > 0 else None
        for idx in reversed(self._filtered_indices):
            if idx < self._current_idx:
                return idx
        return None

    def _next_in_filter(self) -> int | None:
        if self._filter_mode == "All":
            n = len(self._individuals)
            return self._current_idx + 1 if 0 <= self._current_idx < n - 1 else None
        for idx in self._filtered_indices:
            if idx > self._current_idx:
                return idx
        return None

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._current_idx = row
        self._update_filter_counter()
        self._refresh_nav(row)
        if self._nav_triggered or self._loaded_idx >= 0:
            self._nav_triggered = False
            self._loaded_idx = row
            self.individual_selected.emit(row)

    def _go_prev(self):
        prev_idx = self._prev_in_filter()
        if prev_idx is not None:
            self._nav_triggered = True
            self._indiv_list.setCurrentRow(prev_idx)

    def _go_next(self):
        next_idx = self._next_in_filter()
        if next_idx is not None:
            self._nav_triggered = True
            self._indiv_list.setCurrentRow(next_idx)

    def _refresh_nav(self, row: int):
        self._prev_btn.setEnabled(self._prev_in_filter() is not None)
        self._next_btn.setEnabled(self._next_in_filter() is not None)
