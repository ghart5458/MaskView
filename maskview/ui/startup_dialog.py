from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..files.resolver import FILE_TYPE_LABELS, FILE_TYPE_ORDER

_DEFAULTS = {'original', 'maskseg'}

_STYLE = """
QDialog          { background: #1e1e1e; }
QGroupBox        { background: #252525; border: 1px solid #444; border-radius: 4px;
                   margin-top: 10px; padding: 10px 8px 8px 8px; color: #ccc; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QLineEdit        { background: #2d2d2d; color: #eee; border: 1px solid #555;
                   border-radius: 3px; padding: 4px 6px; }
QPushButton      { background: #3a3a3a; color: #ccc; border: 1px solid #555;
                   border-radius: 3px; padding: 4px 12px; }
QPushButton:hover { background: #4a4a4a; }
QPushButton:disabled { color: #555; }
QCheckBox        { color: #ccc; font-size: 11px; }
QCheckBox::indicator { width: 13px; height: 13px; }
QLabel           { color: #ccc; }
"""


class StartupDialog(QDialog):
    """Initial dialog: pick a PAR file and choose which file types to load."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open Session — MaskView")
        self.setModal(True)
        self.setMinimumWidth(400)
        self._par_path: Path | None = None
        self._setup_ui()
        self.setStyleSheet(_STYLE)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        par_group = QGroupBox("PAR file")
        par_row = QHBoxLayout(par_group)
        self._par_edit = QLineEdit()
        self._par_edit.setPlaceholderText("No file selected…")
        self._par_edit.setReadOnly(True)
        par_row.addWidget(self._par_edit, stretch=1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        par_row.addWidget(browse)
        layout.addWidget(par_group)

        ft_group = QGroupBox("File types to load")
        ft_col = QVBoxLayout(ft_group)
        ft_col.setSpacing(4)
        self._checks: dict[str, QCheckBox] = {}
        for ft in FILE_TYPE_ORDER:
            cb = QCheckBox(FILE_TYPE_LABELS[ft])
            cb.setChecked(ft in _DEFAULTS)
            self._checks[ft] = cb
            ft_col.addWidget(cb)
        layout.addWidget(ft_group)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self._open_btn = btns.button(QDialogButtonBox.StandardButton.Open)
        self._open_btn.setEnabled(False)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PAR file", "", "PAR files (*.par);;All files (*)"
        )
        if path:
            self._par_path = Path(path)
            self._par_edit.setText(str(self._par_path))
            self._open_btn.setEnabled(True)

    def _accept(self):
        if self._par_path is not None:
            self.accept()

    def par_path(self) -> Path | None:
        return self._par_path

    def selected_file_types(self) -> list[str]:
        return [ft for ft in FILE_TYPE_ORDER if self._checks[ft].isChecked()]
