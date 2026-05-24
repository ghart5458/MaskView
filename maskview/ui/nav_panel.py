from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..par.parser import Individual


class NavPanel(QWidget):
    """Scrollable list of individuals with prev/next navigation."""

    individual_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self._individuals: list[Individual] = []
        self._current_idx: int = -1
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background: #1a1a1a;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background: #111111;")
        header.setFixedHeight(30)
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(8, 0, 8, 0)
        lbl = QLabel("INDIVIDUALS")
        lbl.setStyleSheet("color: #666; font-size: 9px; font-weight: bold; letter-spacing: 1px;")
        hrow.addWidget(lbl)
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: #1a1a1a; border: none; color: #ccc; font-size: 11px;
            }
            QListWidget::item {
                padding: 5px 8px; border-bottom: 1px solid #222;
            }
            QListWidget::item:selected {
                background: #2d5a9e; color: #fff;
            }
            QListWidget::item:hover:!selected {
                background: #252525;
            }
        """)
        self._list.currentRowChanged.connect(self._on_row_changed)

        self._empty_label = QLabel(
            "No active individuals\nfound in this PAR file.\n\n"
            "Rows prefixed with '#'\nare skipped.\n\n"
            "Remove the '#' to\ninclude an individual."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "color: #555; font-size: 10px; padding: 12px;"
        )
        self._empty_label.setWordWrap(True)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._list)       # index 0
        self._stack.addWidget(self._empty_label)  # index 1
        layout.addWidget(self._stack, stretch=1)

        nav = QWidget()
        nav.setStyleSheet("background: #111111;")
        nav.setFixedHeight(36)
        nrow = QHBoxLayout(nav)
        nrow.setContentsMargins(6, 4, 6, 4)
        nrow.setSpacing(4)

        btn_style = (
            "QPushButton { background: #2a2a2a; color: #aaa; border: none; border-radius: 3px; }"
            "QPushButton:hover:enabled { background: #3a3a3a; color: #eee; }"
            "QPushButton:disabled { color: #444; }"
        )
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedSize(28, 24)
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.clicked.connect(self._go_prev)
        nrow.addWidget(self._prev_btn)

        self._counter = QLabel("— / —")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter.setStyleSheet("color: #777; font-size: 10px;")
        nrow.addWidget(self._counter, stretch=1)

        self._next_btn = QPushButton("▶")
        self._next_btn.setFixedSize(28, 24)
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.clicked.connect(self._go_next)
        nrow.addWidget(self._next_btn)

        layout.addWidget(nav)
        self._update_nav(-1)

    def load_individuals(self, individuals: list[Individual]):
        self._individuals = individuals
        self._list.blockSignals(True)
        self._list.clear()
        for ind in individuals:
            self._list.addItem(ind.oldname)
        self._list.blockSignals(False)
        self._current_idx = -1
        self._update_counter(-1)
        self._update_nav(-1)
        self._stack.setCurrentIndex(0 if individuals else 1)

    def select(self, idx: int):
        if 0 <= idx < len(self._individuals):
            self._list.setCurrentRow(idx)

    def _on_row_changed(self, row: int):
        if row < 0:
            return
        self._current_idx = row
        self._update_counter(row)
        self._update_nav(row)
        self.individual_selected.emit(row)

    def _go_prev(self):
        if self._current_idx > 0:
            self._list.setCurrentRow(self._current_idx - 1)

    def _go_next(self):
        if self._current_idx < len(self._individuals) - 1:
            self._list.setCurrentRow(self._current_idx + 1)

    def _update_counter(self, row: int):
        n = len(self._individuals)
        if n == 0 or row < 0:
            self._counter.setText("— / —")
        else:
            self._counter.setText(f"{row + 1} / {n}")

    def _update_nav(self, row: int):
        n = len(self._individuals)
        self._prev_btn.setEnabled(row > 0)
        self._next_btn.setEnabled(0 <= row < n - 1)
