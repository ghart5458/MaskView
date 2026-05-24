from pathlib import Path

from PyQt6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QListWidget, QPushButton,
    QRadioButton, QScrollArea, QVBoxLayout, QWidget,
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
        "color: #888; font-size: 9px; font-weight: bold;"
        " letter-spacing: 1px; padding: 4px 0 2px 0;"
    )
    return lbl


class _Section(QWidget):
    """Collapsible sidebar section — state preserved across sidebar open/close."""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._hdr = QWidget()
        self._hdr.setFixedHeight(26)
        self._hdr.setStyleSheet(
            "QWidget { background: #1f1f1f; }"
            "QWidget:hover { background: #242424; }"
        )
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hrow = QHBoxLayout(self._hdr)
        hrow.setContentsMargins(8, 0, 8, 0)
        hrow.setSpacing(6)

        self._arrow = QLabel("▾" if expanded else "▸")
        self._arrow.setStyleSheet("color: #777; font-size: 10px;")
        self._arrow.setFixedWidth(10)

        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setStyleSheet(
            "color: #999; font-size: 9px; font-weight: bold; letter-spacing: 1px;"
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

    def set_theme(self, mode: str):
        if mode == "light":
            self._hdr.setStyleSheet(
                "QWidget { background: #e0e0e0; } QWidget:hover { background: #d8d8d8; }"
            )
            self._arrow.setStyleSheet("color: #555; font-size: 10px;")
            self._title_lbl.setStyleSheet(
                "color: #333; font-size: 9px; font-weight: bold; letter-spacing: 1px;"
            )
            self._body.setStyleSheet("background: #f0f0f0;")
        else:
            self._hdr.setStyleSheet(
                "QWidget { background: #1f1f1f; } QWidget:hover { background: #242424; }"
            )
            self._arrow.setStyleSheet("color: #777; font-size: 10px;")
            self._title_lbl.setStyleSheet(
                "color: #999; font-size: 9px; font-weight: bold; letter-spacing: 1px;"
            )
            self._body.setStyleSheet("background: #181818;")

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
    individual_selected = pyqtSignal(int)
    theme_changed       = pyqtSignal(str)       # "dark" | "light"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._file_checks: dict[str, QCheckBox] = {}
        self._file_available: dict[str, bool] = {}
        self._annot_groups: dict[str, QButtonGroup] = {}
        self._is_open = False
        self._current_theme = "dark"

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
            "color: #2ce67f; font-size: 13px;"
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
        panel_col.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("background: #181818;")
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

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

        self._build_file_section()
        self._build_tools_section()
        self._build_annotations_section([])
        self._build_individuals_section()

    def _build_file_section(self):
        body = self._sec_file.body
        body.setContentsMargins(8, 6, 8, 4)
        body.setSpacing(3)

        self._par_btn = QPushButton("Select PAR…")
        self._par_btn.setStyleSheet(
            "QPushButton { background: #0f2a1a; color: #5fd49a; border: none;"
            " border-radius: 3px; padding: 5px 8px; font-size: 11px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; }"
        )
        self._par_btn.clicked.connect(self._browse_par)
        body.addWidget(self._par_btn)

        self._par_label = QLabel("No file loaded")
        self._par_label.setStyleSheet(
            "color: #666; font-size: 10px; font-style: italic; padding: 1px 0;"
        )
        self._par_label.setWordWrap(True)
        body.addWidget(self._par_label)

        body.addWidget(_sep())
        self._sec_display = _Section("Display", expanded=True)
        self._sec_display.body.setContentsMargins(8, 4, 8, 6)
        self._sec_display.body.setSpacing(3)
        for ft in FILE_TYPE_ORDER:
            cb = QCheckBox(FILE_TYPE_LABELS[ft])
            cb.setEnabled(False)
            cb.setStyleSheet(
                "QCheckBox { color: #888; font-size: 11px; padding: 1px 0; }"
                "QCheckBox:enabled { color: #ccc; }"
                "QCheckBox:disabled { color: #4a4a4a; }"
            )
            self._file_checks[ft] = cb
            self._sec_display.body.addWidget(cb)
        body.addWidget(self._sec_display)

        body.addWidget(_sep())
        self._apply_btn = QPushButton("Update")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #0f2a1a; color: #5fd49a; border: none;"
            " border-radius: 3px; padding: 5px 8px; font-size: 11px; }"
            "QPushButton:hover:enabled { background: #147a3f; color: #fff; }"
            "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; }"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        body.addWidget(self._apply_btn)

    def _build_tools_section(self):
        body = self._sec_tools.body
        body.setContentsMargins(8, 6, 8, 4)
        body.setSpacing(4)

        body.addWidget(_mini_label("ORIENTATION"))
        orient_row = QWidget()
        orow = QHBoxLayout(orient_row)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.setSpacing(10)
        self._orient_group = QButtonGroup(self)
        for text in ("XY", "XZ", "YZ"):
            rb = QRadioButton(text)
            rb.setChecked(text == "XY")
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 11px; }")
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
            rb.setStyleSheet("QRadioButton { color: #bbb; font-size: 11px; }")
            rb.toggled.connect(
                lambda chk, t=text: self.layout_changed.emit(t) if chk else None
            )
            self._layout_group.addButton(rb)
            lrow.addWidget(rb)
        lrow.addStretch()
        body.addWidget(layout_row)

        body.addWidget(_sep())
        self._sync_cb = QCheckBox("Synchronize windows")
        self._sync_cb.setChecked(True)
        self._sync_cb.setStyleSheet("QCheckBox { color: #bbb; font-size: 11px; }")
        self._sync_cb.toggled.connect(self.sync_toggled)
        body.addWidget(self._sync_cb)

        body.addWidget(_sep())
        self._placeholder_lbls = []
        for placeholder in ("Threshold", "Color overlay", "Tagging"):
            lbl = QLabel(placeholder)
            lbl.setStyleSheet("color: #4a4a4a; font-size: 11px; padding: 1px 0;")
            self._placeholder_lbls.append(lbl)
            body.addWidget(lbl)

        body.addWidget(_sep())
        self._theme_btn = QPushButton("☀  Light mode")
        self._theme_btn.setCheckable(True)
        self._theme_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #bbb; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 4px 8px; font-size: 11px; text-align: left; }"
            "QPushButton:checked { background: #1a2820; color: #2ce67f; border-color: #1ab864; }"
            "QPushButton:hover { border-color: #555; }"
        )
        self._theme_btn.toggled.connect(self._on_theme_toggle)
        body.addWidget(self._theme_btn)

    def _build_annotations_section(self, file_types: list[str]):
        body = self._sec_annot.body
        body.setContentsMargins(8, 6, 8, 4)
        body.setSpacing(3)

        while body.count():
            item = body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._annot_groups.clear()

        if not file_types:
            lbl = QLabel("No panels open")
            lbl.setStyleSheet("color: #666; font-size: 10px;")
            body.addWidget(lbl)
            return

        for ft in file_types:
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rrow = QHBoxLayout(row_w)
            rrow.setContentsMargins(0, 1, 0, 1)
            rrow.setSpacing(3)

            lbl = QLabel(FILE_TYPE_LABELS.get(ft, ft))
            lbl.setStyleSheet("color: #aaa; font-size: 10px;")
            rrow.addWidget(lbl, stretch=1)

            group = QButtonGroup(row_w)
            group.setExclusive(True)
            for text, color in (("Pass", "#1d4a27"), ("Rev", "#4a3c12"), ("Fail", "#4a1616")):
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.setFixedHeight(18)
                btn.setStyleSheet(
                    "QPushButton { background: #252525; color: #888; border: none;"
                    f" border-radius: 2px; font-size: 9px; padding: 0 5px; }}"
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
        self._indiv_list.setFixedHeight(220)
        self._indiv_list.setStyleSheet("""
            QListWidget {
                background: #141414; border: none;
                color: #ccc; font-size: 10px;
            }
            QListWidget::item { padding: 3px 10px; border-bottom: 1px solid #1c1c1c; }
            QListWidget::item:selected { background: #147a3f; color: #fff; }
            QListWidget::item:hover:!selected { background: #1e1e1e; }
        """)
        self._indiv_list.currentRowChanged.connect(self._on_row_changed)
        body.addWidget(self._indiv_list)

        nav = QWidget()
        nav.setStyleSheet("background: #141414;")
        nrow = QHBoxLayout(nav)
        nrow.setContentsMargins(6, 3, 6, 4)
        nrow.setSpacing(4)

        btn_style = (
            "QPushButton { background: #202020; color: #999; border: none;"
            " border-radius: 3px; font-size: 10px; padding: 2px 6px; }"
            "QPushButton:hover:enabled { background: #2c2c2c; color: #ddd; }"
            "QPushButton:disabled { color: #3a3a3a; }"
        )
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.clicked.connect(self._go_prev)

        self._counter = QLabel("— / —")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet("color: #888; font-size: 10px;")

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedWidth(28)
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.clicked.connect(self._go_next)

        nrow.addWidget(self._prev_btn)
        nrow.addWidget(self._counter, stretch=1)
        nrow.addWidget(self._next_btn)
        body.addWidget(nav)
        self._refresh_nav(-1)

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
        if not self._is_open:
            self._animate_to(_OPEN_W)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._is_open:
            self._poll.start()
        super().leaveEvent(event)

    def _check_cursor(self):
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
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

    def set_file_loaded(self, ft: str, loaded: bool):
        cb = self._file_checks.get(ft)
        if cb:
            cb.blockSignals(True)
            cb.setChecked(loaded)
            cb.blockSignals(False)

    def update_annotations(self, file_types: list[str]):
        self._build_annotations_section(file_types)

    def set_par_label(self, path: Path | None):
        if path is None:
            self._par_label.setText("No file loaded")
            self._par_label.setStyleSheet(
                "color: #666; font-size: 10px; font-style: italic; padding: 1px 0;"
            )
        else:
            self._par_label.setText(path.name)
            self._par_label.setStyleSheet(
                "color: #aaa; font-size: 10px; font-style: normal; padding: 1px 0;"
            )

    def set_controls_enabled(self, enabled: bool):
        self._indiv_list.setEnabled(enabled)
        self._prev_btn.setEnabled(enabled and self._current_idx > 0)
        self._next_btn.setEnabled(
            enabled and 0 <= self._current_idx < len(self._individuals) - 1
        )
        for ft, cb in self._file_checks.items():
            cb.setEnabled(enabled and self._file_available.get(ft, False))
        self._apply_btn.setEnabled(enabled and self._current_idx >= 0)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_apply(self):
        selected = [ft for ft, cb in self._file_checks.items() if cb.isChecked()]
        self.files_applied.emit(selected)

    def _on_theme_toggle(self, checked: bool):
        self._theme_btn.setText("☾  Dark mode" if checked else "☀  Light mode")
        self._apply_theme("light" if checked else "dark")

    def _apply_theme(self, mode: str):
        self._current_theme = mode
        dark = (mode == "dark")

        if dark:
            self._trigger.setStyleSheet(
                "QWidget { background: #0d1a10; border-right: 2px solid #147a3f; }"
            )
            self._icon_lbl.setStyleSheet(
                "color: #2ce67f; font-size: 13px; background: transparent; border: none;"
            )
            self._panel.setStyleSheet("background: #181818; border-right: 1px solid #2c2c2c;")
            gbtn = (
                "QPushButton { background: #0f2a1a; color: #5fd49a; border: none;"
                " border-radius: 3px; padding: 5px 8px; font-size: 11px; }"
                "QPushButton:hover:enabled { background: #147a3f; color: #fff; }"
                "QPushButton:disabled { background: #1a1a1a; color: #3a3a3a; }"
            )
            cb_style = (
                "QCheckBox { color: #888; font-size: 11px; padding: 1px 0; }"
                "QCheckBox:enabled { color: #ccc; }"
                "QCheckBox:disabled { color: #4a4a4a; }"
            )
            rb_col = "#bbb";  tool_col = "#bbb";  ph_col = "#4a4a4a"
            list_style = (
                "QListWidget { background: #141414; border: none; color: #ccc; font-size: 10px; }"
                "QListWidget::item { padding: 3px 10px; border-bottom: 1px solid #1c1c1c; }"
                "QListWidget::item:selected { background: #147a3f; color: #fff; }"
                "QListWidget::item:hover:!selected { background: #1e1e1e; }"
            )
            nav_btn = (
                "QPushButton { background: #202020; color: #999; border: none;"
                " border-radius: 3px; font-size: 10px; padding: 2px 6px; }"
                "QPushButton:hover:enabled { background: #2c2c2c; color: #ddd; }"
                "QPushButton:disabled { color: #3a3a3a; }"
            )
            counter_col = "#888"
        else:
            self._trigger.setStyleSheet(
                "QWidget { background: #e8f5ee; border-right: 2px solid #2ce67f; }"
            )
            self._icon_lbl.setStyleSheet(
                "color: #147a3f; font-size: 13px; background: transparent; border: none;"
            )
            self._panel.setStyleSheet("background: #f5f5f5; border-right: 1px solid #d0d0d0;")
            gbtn = (
                "QPushButton { background: #d0eedd; color: #0a5c28; border: none;"
                " border-radius: 3px; padding: 5px 8px; font-size: 11px; }"
                "QPushButton:hover:enabled { background: #2ce67f; color: #000; }"
                "QPushButton:disabled { background: #f0f0f0; color: #bbb; }"
            )
            cb_style = (
                "QCheckBox { color: #666; font-size: 11px; padding: 1px 0; }"
                "QCheckBox:enabled { color: #111; }"
                "QCheckBox:disabled { color: #bbb; }"
            )
            rb_col = "#111";  tool_col = "#111";  ph_col = "#bbb"
            list_style = (
                "QListWidget { background: #fff; border: none; color: #111; font-size: 10px; }"
                "QListWidget::item { padding: 3px 10px; border-bottom: 1px solid #e0e0e0; }"
                "QListWidget::item:selected { background: #2ce67f; color: #000; }"
                "QListWidget::item:hover:!selected { background: #f0f0f0; }"
            )
            nav_btn = (
                "QPushButton { background: #e0e0e0; color: #333; border: none;"
                " border-radius: 3px; font-size: 10px; padding: 2px 6px; }"
                "QPushButton:hover:enabled { background: #d0d0d0; color: #111; }"
                "QPushButton:disabled { color: #bbb; }"
            )
            counter_col = "#555"

        for sec in (self._sec_file, self._sec_display, self._sec_tools, self._sec_annot, self._sec_indiv):
            sec.set_theme(mode)
        self._par_btn.setStyleSheet(gbtn)
        self._apply_btn.setStyleSheet(gbtn)
        for cb in self._file_checks.values():
            cb.setStyleSheet(cb_style)
        for rb in list(self._orient_group.buttons()) + list(self._layout_group.buttons()):
            rb.setStyleSheet(f"QRadioButton {{ color: {rb_col}; font-size: 11px; }}")
        self._sync_cb.setStyleSheet(f"QCheckBox {{ color: {tool_col}; font-size: 11px; }}")
        if dark:
            self._theme_btn.setStyleSheet(
                "QPushButton { background: #252525; color: #bbb; border: 1px solid #3a3a3a;"
                " border-radius: 3px; padding: 4px 8px; font-size: 11px; text-align: left; }"
                "QPushButton:checked { background: #1a2820; color: #2ce67f; border-color: #1ab864; }"
                "QPushButton:hover { border-color: #555; }"
            )
        else:
            self._theme_btn.setStyleSheet(
                "QPushButton { background: #e0e0e0; color: #333; border: 1px solid #ccc;"
                " border-radius: 3px; padding: 4px 8px; font-size: 11px; text-align: left; }"
                "QPushButton:checked { background: #d0eedd; color: #0a5c28; border-color: #2ce67f; }"
                "QPushButton:hover { border-color: #aaa; }"
            )
        for lbl in self._placeholder_lbls:
            lbl.setStyleSheet(f"color: {ph_col}; font-size: 11px; padding: 1px 0;")
        self._indiv_list.setStyleSheet(list_style)
        self._prev_btn.setStyleSheet(nav_btn)
        self._next_btn.setStyleSheet(nav_btn)
        self._counter.setStyleSheet(f"color: {counter_col}; font-size: 10px;")
        self.theme_changed.emit(mode)

    def _browse_par(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PAR file", "", "PAR files (*.par);;All files (*)"
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
