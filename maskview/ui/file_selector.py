from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QMenu, QWidgetAction

from ..files.resolver import FILE_TYPE_LABELS, FILE_TYPE_ORDER


class FileSelector(QMenu):
    """Dropdown checklist of file types, attached to the 'Files' toolbar button."""

    file_toggled = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checks: dict[str, QCheckBox] = {}
        self.setMinimumWidth(200)
        self.setStyleSheet("""
            QMenu {
                background: #1e1e1e;
                border: 1px solid #444;
                padding: 4px 0;
            }
        """)
        for ft in FILE_TYPE_ORDER:
            cb = QCheckBox(FILE_TYPE_LABELS[ft])
            cb.setEnabled(False)
            cb.setMinimumWidth(200)
            cb.setStyleSheet(
                "QCheckBox { color: #888; font-size: 11px; padding: 5px 10px; }"
                "QCheckBox:enabled { color: #ccc; }"
                "QCheckBox:disabled { color: #444; }"
            )
            cb.toggled.connect(lambda checked, f=ft: self.file_toggled.emit(f, checked))
            self._checks[ft] = cb
            wa = QWidgetAction(self)
            wa.setDefaultWidget(cb)
            self.addAction(wa)

    def update_availability(self, available: dict[str, bool], loaded: set[str]):
        for ft, cb in self._checks.items():
            cb.blockSignals(True)
            exists = available.get(ft, False)
            cb.setEnabled(exists)
            cb.setChecked(exists and ft in loaded)
            cb.blockSignals(False)

    def set_loaded(self, ft: str, loaded: bool):
        cb = self._checks.get(ft)
        if cb:
            cb.blockSignals(True)
            cb.setChecked(loaded)
            cb.blockSignals(False)
