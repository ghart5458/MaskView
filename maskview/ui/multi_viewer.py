import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .viewer_panel import ViewerPanel

_HANDLE_STYLE = "QSplitter::handle { background: #333; }"


class MultiViewer(QWidget):
    """Pure viewer container — 1–4 panels in quadrant or linear layout.

    All controls (orientation, layout, sync) are driven externally
    via set_orientation / set_layout_mode / set_sync.
    """

    panel_closed = pyqtSignal(str)  # file_type of the closed panel

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panels: list[ViewerPanel] = []
        self._sync_enabled = True
        self._orientation  = "XY"
        self._layout_mode  = "2×2"
        self._bottom_dummy: QWidget | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._outer = QSplitter(Qt.Orientation.Vertical)
        self._outer.setHandleWidth(3)
        self._outer.setStyleSheet(_HANDLE_STYLE)

        self._top = QSplitter(Qt.Orientation.Horizontal)
        self._top.setHandleWidth(3)
        self._top.setStyleSheet(_HANDLE_STYLE)

        self._bottom = QSplitter(Qt.Orientation.Horizontal)
        self._bottom.setHandleWidth(3)
        self._bottom.setStyleSheet(_HANDLE_STYLE)
        self._bottom.hide()

        self._outer.addWidget(self._top)
        self._outer.addWidget(self._bottom)
        layout.addWidget(self._outer, stretch=1)

    # ── Panel management ──────────────────────────────────────────────────────

    def add_empty_panel(self, file_type: str) -> ViewerPanel:
        """Create a placeholder panel with no data and add it to the layout."""
        panel = ViewerPanel(file_type)
        panel.viewer.set_orientation(self._orientation)
        self._connect_panel(panel)
        panel.closed.connect(lambda p=panel: self._remove_panel(p))
        self._panels.append(panel)
        self._rebuild_layout()
        panel.start_loading()
        return panel

    def fill_panel(self, file_type: str, data: np.ndarray,
                   lo: float, hi: float) -> bool:
        """Load data into an existing placeholder panel. Returns False if not found."""
        for panel in self._panels:
            if panel.file_type == file_type:
                panel.stop_loading()
                panel.load(data, lo, hi)
                return True
        return False

    def add_panel(self, file_type: str, data: np.ndarray,
                  lo: float, hi: float) -> ViewerPanel:
        panel = self.add_empty_panel(file_type)
        panel.stop_loading()   # data is ready — no spinner needed
        panel.load(data, lo, hi)
        return panel

    @property
    def panels(self) -> list[ViewerPanel]:
        return list(self._panels)

    def close_panel(self, file_type: str) -> None:
        for panel in list(self._panels):
            if panel.file_type == file_type:
                self._remove_panel(panel)
                break

    def clear(self) -> None:
        for panel in list(self._panels):
            self._remove_panel(panel)

    # ── External control setters (called by MainWindow from sidebar signals) ──

    def set_orientation(self, orientation: str):
        self._orientation = orientation
        for panel in self._panels:
            panel.viewer.set_orientation(orientation)

    def set_layout_mode(self, mode: str):
        self._layout_mode = mode
        self._rebuild_layout()

    def set_sync(self, enabled: bool):
        self._sync_enabled = enabled
        if enabled and len(self._panels) > 1:
            self.sync_all()

    def sync_all(self):
        """Sync all panels to panel 0's slice/zoom/pan."""
        if len(self._panels) < 2:
            return
        ref = self._panels[0].viewer
        z, zoom = ref.current_slice, ref.current_zoom
        x, y = ref.current_pan
        for panel in self._panels[1:]:
            panel.viewer.set_slice(z)
            panel.viewer.set_zoom(zoom)
            panel.viewer.set_pan(x, y)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _rebuild_layout(self):
        if self._bottom_dummy is not None:
            self._bottom_dummy.setParent(None)
            self._bottom_dummy = None

        n = len(self._panels)
        use_quad = (self._layout_mode == "2×2" and n > 2)

        if use_quad:
            for i, panel in enumerate(self._panels):
                (self._top if i < 2 else self._bottom).insertWidget(i % 2, panel)
            if n - 2 < 2:
                self._bottom_dummy = QWidget()
                self._bottom_dummy.setStyleSheet("background: #111;")
                self._bottom.addWidget(self._bottom_dummy)
            self._bottom.setVisible(True)
            self._equalize(self._top, 2)
            self._equalize(self._bottom, 2)
            h = self._outer.height() or 600
            self._outer.setSizes([h // 2, h // 2])
        else:
            for i, panel in enumerate(self._panels):
                self._top.insertWidget(i, panel)
            self._bottom.hide()
            self._equalize(self._top, n)

    def _equalize(self, splitter: QSplitter, count: int):
        if count > 0:
            w = splitter.width() or 800
            splitter.setSizes([w // count] * count)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _remove_panel(self, panel: ViewerPanel):
        ft = panel.file_type
        if panel in self._panels:
            self._panels.remove(panel)
        panel.setParent(None)
        self.panel_closed.emit(ft)
        self._rebuild_layout()

    def _connect_panel(self, panel: ViewerPanel):
        v = panel.viewer
        v.slice_changed.connect(self._on_slice_changed)
        v.zoom_changed.connect(self._on_zoom_changed)
        v.pan_changed.connect(self._on_pan_changed)
        v.cursor_moved.connect(self._on_cursor_moved)
        v.cursor_left.connect(self._on_cursor_left)

    def _on_slice_changed(self, z: int):
        if not self._sync_enabled:
            return
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_slice(z)

    def _on_zoom_changed(self, factor: float):
        if not self._sync_enabled:
            return
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_zoom(factor)

    def _on_pan_changed(self, x: float, y: float):
        if not self._sync_enabled:
            return
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_pan(x, y)

    def _on_cursor_moved(self, x: float, y: float):
        if not self._sync_enabled:
            return
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_external_cursor(x, y)

    def _on_cursor_left(self):
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.clear_external_cursor()
