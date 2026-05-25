import numpy as np
from PyQt6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter
from PyQt6.QtWidgets import QApplication, QSplitter, QVBoxLayout, QWidget

from .composite_panel import COMPOSITE_TYPE, CompositePanel
from .viewer_panel import ViewerPanel

_HANDLE_STYLE = "QSplitter::handle { background: #333; }"


class _SelectionOverlay(QWidget):
    """Semi-transparent click-to-select overlay shown during composite placement."""

    panel_clicked = pyqtSignal(str)  # file_type of clicked panel

    def __init__(self, panels: list, parent: QWidget):
        super().__init__(parent)
        self._panels = panels
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(parent.rect())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.raise_()
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))
        painter.setPen(QColor(255, 255, 255, 220))
        font = painter.font()
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "Click a panel to place the color composite",
        )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        for panel in self._panels:
            top_left = panel.mapTo(self.parent(), QPoint(0, 0))
            if QRect(top_left, panel.size()).contains(pos):
                self.panel_clicked.emit(panel.file_type)
                return


class MultiViewer(QWidget):
    """Pure viewer container — 1–4 panels in quadrant or linear layout.

    All controls (orientation, layout, sync) are driven externally
    via set_orientation / set_layout_mode / set_sync.
    """

    panel_closed              = pyqtSignal(str)       # file_type of the closed panel
    composite_target_selected = pyqtSignal(str)       # file_type of the panel to replace
    panel_tags_changed        = pyqtSignal(list, str) # (tags, file_type) forwarded from panels

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panels: list = []
        self._sync_enabled = True
        self._orientation  = "XY"
        self._layout_mode  = "2×2"
        self._bottom_dummy: QWidget | None = None
        self._selection_overlay: _SelectionOverlay | None = None
        self._last_active_panel = None
        self._dragging_panel    = None
        self._drag_hover        = None
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
        panel.stop_loading()
        panel.load(data, lo, hi)
        return panel

    def add_composite_panel(self, channels: list) -> CompositePanel:
        """Append a composite panel (data is ready — no loading spinner)."""
        panel = CompositePanel()
        self._connect_panel(panel)
        panel.closed.connect(lambda p=panel: self._remove_panel(p))
        self._panels.append(panel)
        self._rebuild_layout()
        panel.viewer.set_channels(channels)
        panel.viewer.set_orientation(self._orientation)
        QTimer.singleShot(0, self.sync_all)
        return panel

    def replace_with_composite(self, file_type: str, channels: list) -> CompositePanel | None:
        """Replace the named panel with a composite panel.

        Does NOT emit panel_closed so the sidebar keeps the file checked.
        Returns None if no panel with that file_type is found.
        """
        target = next((p for p in self._panels if p.file_type == file_type), None)
        if target is None:
            return None
        idx = self._panels.index(target)
        self._panels.remove(target)
        target.setParent(None)

        panel = CompositePanel()
        self._connect_panel(panel)
        panel.closed.connect(lambda p=panel: self._remove_panel(p))
        self._panels.insert(idx, panel)
        self._rebuild_layout()
        panel.viewer.set_channels(channels)
        panel.viewer.set_orientation(self._orientation)
        QTimer.singleShot(0, self.sync_all)
        return panel

    @property
    def panels(self) -> list:
        return list(self._panels)

    def set_panel_filename(self, file_type: str, path) -> None:
        for panel in self._panels:
            if panel.file_type == file_type and hasattr(panel, "set_filename"):
                panel.set_filename(path)
                return

    def close_panel(self, file_type: str) -> None:
        for panel in list(self._panels):
            if panel.file_type == file_type:
                self._remove_panel(panel)
                break

    def clear(self) -> None:
        for panel in list(self._panels):
            self._remove_panel(panel)

    # ── Selection mode ────────────────────────────────────────────────────────

    def enter_selection_mode(self):
        """Dim the viewer and wait for the user to click a panel to replace."""
        if self._selection_overlay is not None:
            return
        self._selection_overlay = _SelectionOverlay(self._panels, self)
        self._selection_overlay.panel_clicked.connect(self._on_panel_clicked_for_composite)

    def exit_selection_mode(self):
        if self._selection_overlay is not None:
            self._selection_overlay.setParent(None)
            self._selection_overlay = None

    def _on_panel_clicked_for_composite(self, file_type: str):
        self.exit_selection_mode()
        self.composite_target_selected.emit(file_type)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._selection_overlay is not None:
            self._selection_overlay.setGeometry(self.rect())

    # ── External control setters (called by MainWindow from sidebar signals) ──

    def set_orientation(self, orientation: str):
        self._orientation = orientation
        for panel in self._panels:
            panel.viewer.set_orientation(orientation)

    def set_layout_mode(self, mode: str):
        self._layout_mode = mode
        self._rebuild_layout()

    @property
    def orientation(self) -> str:
        return self._orientation

    def set_tags_visible(self, visible: bool):
        for panel in self._panels:
            if hasattr(panel, "set_tags_visible"):
                panel.set_tags_visible(visible)

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

    def _remove_panel(self, panel):
        ft = panel.file_type
        if panel in self._panels:
            self._panels.remove(panel)
        panel.setParent(None)
        self.panel_closed.emit(ft)
        self._rebuild_layout()

    def _connect_panel(self, panel):
        v = panel.viewer
        v.slice_changed.connect(self._on_slice_changed)
        v.zoom_changed.connect(self._on_zoom_changed)
        v.pan_changed.connect(self._on_pan_changed)
        v.cursor_moved.connect(self._on_cursor_moved)
        v.cursor_left.connect(self._on_cursor_left)
        if hasattr(panel, "tags_changed"):
            panel.tags_changed.connect(self.panel_tags_changed)
        if hasattr(panel, "drag_started"):
            panel.drag_started.connect(lambda p=panel: self._on_drag_started(p))

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
        src = self.sender()
        src_panel = next((p for p in self._panels if p.viewer is src), None)
        if src_panel is not None and src_panel is not self._last_active_panel:
            self._last_active_panel = src_panel
            if hasattr(src_panel, "current_tags"):
                tags, ft = src_panel.current_tags()
                self.panel_tags_changed.emit(tags, ft)
        if not self._sync_enabled:
            return
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_external_cursor(x, y)

    def _on_cursor_left(self):
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.clear_external_cursor()

    # ── Panel drag-to-reorder ─────────────────────────────────────────────────

    def _on_drag_started(self, panel):
        self._dragging_panel = panel
        self._drag_hover     = None
        QApplication.instance().installEventFilter(self)
        QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)

    def _panel_at(self, gpos):
        for p in self._panels:
            if p is self._dragging_panel:
                continue
            local = p.mapFromGlobal(gpos)
            if p.rect().contains(local):
                return p
        return None

    def _update_drag(self, gpos):
        target = self._panel_at(gpos)
        if target is not self._drag_hover:
            if self._drag_hover is not None and hasattr(self._drag_hover, "set_swap_highlight"):
                self._drag_hover.set_swap_highlight(False)
            self._drag_hover = target
            if target is not None and hasattr(target, "set_swap_highlight"):
                target.set_swap_highlight(True)

    def _finish_drag(self, gpos):
        target = self._panel_at(gpos)
        if self._drag_hover is not None and hasattr(self._drag_hover, "set_swap_highlight"):
            self._drag_hover.set_swap_highlight(False)
        if target is not None and target is not self._dragging_panel:
            i = self._panels.index(self._dragging_panel)
            j = self._panels.index(target)
            self._panels[i], self._panels[j] = self._panels[j], self._panels[i]
            self._rebuild_layout()
        self._dragging_panel = None
        self._drag_hover     = None
        QApplication.instance().removeEventFilter(self)
        QApplication.restoreOverrideCursor()

    def eventFilter(self, obj, event):
        if self._dragging_panel is None:
            return False
        t = event.type()
        if t == QEvent.Type.MouseMove:
            self._update_drag(QCursor.pos())
        elif t == QEvent.Type.MouseButtonRelease:
            self._finish_drag(QCursor.pos())
        return False
