from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..files.resolver import FILE_TYPE_LABELS
from .viewer import VolumeViewer


class ViewerPanel(QWidget):
    """VolumeViewer with a dark title bar and a close button."""

    closed = pyqtSignal()

    def __init__(self, file_type: str, parent=None):
        super().__init__(parent)
        self._file_type = file_type

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(28)
        title_bar.setStyleSheet("#titleBar { background: #2d2d2d; }")
        bar = QHBoxLayout(title_bar)
        bar.setContentsMargins(8, 3, 4, 3)

        self._label = QLabel(FILE_TYPE_LABELS.get(file_type, file_type))
        self._label.setStyleSheet(
            "color: #dddddd; font-weight: bold; font-size: 11px; background: transparent;"
        )
        bar.addWidget(self._label, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton { color: #aaa; border: none; font-size: 10px; background: transparent; }"
            "QPushButton:hover { color: white; background: #c0392b; border-radius: 3px; }"
        )
        close_btn.clicked.connect(self.closed)
        bar.addWidget(close_btn)

        layout.addWidget(title_bar)

        self._viewer = VolumeViewer()
        layout.addWidget(self._viewer, stretch=1)

    @property
    def viewer(self) -> VolumeViewer:
        return self._viewer

    @property
    def file_type(self) -> str:
        return self._file_type
