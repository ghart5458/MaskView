from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPainter
from PyQt6.QtWidgets import QApplication, QLabel, QSplitter, QVBoxLayout, QWidget

from .composite_panel import COMPOSITE_TYPE, CompositePanel
from .viewer_panel import ViewerPanel

_HANDLE_STYLE = "QSplitter::handle { background: #333; }"


@dataclass
class _AnchorPoint:
    slice_idx: int
    scene_x: float
    scene_y: float


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
        self._anchor_mode  = False
        self._anchors: dict[str, _AnchorPoint] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Welcome overlay — floats above the splitter, visible when no panels are loaded
        self._welcome = QLabel("Select a PAR / CSV or individual scan to begin.", self)
        self._welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._welcome.setStyleSheet(
            "color: #3a3a3a; font-size: 14px; background: #111111;"
        )
        self._welcome.setWordWrap(True)

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

        self._welcome.raise_()
        self._welcome.show()

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
        self._anchors.clear()
        self._anchor_mode = False

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
        self._welcome.setGeometry(self.rect())

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
        if self._panels:
            self._welcome.hide()
        else:
            self._welcome.setGeometry(self.rect())
            self._welcome.show()
            self._welcome.raise_()

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
        if hasattr(panel, "anchor_confirmed"):
            panel.anchor_confirmed.connect(self._on_anchor_confirmed)
        if hasattr(panel, "anchor_cleared"):
            panel.anchor_cleared.connect(self._on_anchor_cleared)

    def _on_slice_changed(self, z: int):
        if not self._sync_enabled or self._anchor_mode:
            return
        src = self.sender()
        src_panel = next((p for p in self._panels if p.viewer is src), None)
        if self._anchors and src_panel is not None:
            src_ft = src_panel.file_type
            if src_ft not in self._anchors:
                return
            src_anchor = self._anchors[src_ft]
            for p in self._panels:
                if p.viewer is src:
                    continue
                ft = p.file_type
                if ft not in self._anchors:
                    continue
                p.viewer.set_slice(
                    max(0, self._anchors[ft].slice_idx + (z - src_anchor.slice_idx))
                )
        else:
            for p in self._panels:
                if p.viewer is not src:
                    p.viewer.set_slice(z)

    def _on_zoom_changed(self, factor: float):
        if not self._sync_enabled or self._anchor_mode:
            return
        src = self.sender()
        for p in self._panels:
            if p.viewer is not src:
                p.viewer.set_zoom(factor)

    def _on_pan_changed(self, x: float, y: float):
        if not self._sync_enabled or self._anchor_mode:
            return
        src = self.sender()
        src_panel = next((p for p in self._panels if p.viewer is src), None)
        if self._anchors and src_panel is not None:
            src_ft = src_panel.file_type
            if src_ft not in self._anchors:
                return
            src_anchor = self._anchors[src_ft]
            for p in self._panels:
                if p.viewer is src:
                    continue
                ft = p.file_type
                if ft not in self._anchors:
                    continue
                tgt = self._anchors[ft]
                p.viewer.set_pan(
                    tgt.scene_x + (x - src_anchor.scene_x),
                    tgt.scene_y + (y - src_anchor.scene_y),
                )
        else:
            for p in self._panels:
                if p.viewer is not src:
                    p.viewer.set_pan(x, y)

    # ── Anchor sync ───────────────────────────────────────────────────────────

    @property
    def anchors(self) -> dict:
        return dict(self._anchors)

    def enter_anchor_mode(self):
        if self._anchor_mode:
            return
        self._anchor_mode = True
        for panel in self._panels:
            if hasattr(panel, "set_anchor_mode"):
                panel.set_anchor_mode(True)

    def exit_anchor_mode(self):
        self._anchor_mode = False
        for panel in self._panels:
            if hasattr(panel, "set_anchor_mode"):
                panel.set_anchor_mode(False)

    def apply_anchor_sync(self):
        """Activate offset sync using confirmed anchors; hide overlays."""
        self._anchor_mode = False
        for panel in self._panels:
            if hasattr(panel, "set_anchor_mode"):
                panel.set_anchor_mode(False)
        # Center each anchored panel on its anchor point
        for panel in self._panels:
            ft = panel.file_type
            if ft in self._anchors:
                a = self._anchors[ft]
                panel.viewer.set_slice(a.slice_idx)
                panel.viewer.set_pan(a.scene_x, a.scene_y)

    def clear_anchors(self):
        """Clear all anchors and return to standard sync."""
        self._anchors.clear()
        self._anchor_mode = False
        for panel in self._panels:
            if hasattr(panel, "set_anchor_mode"):
                panel.set_anchor_mode(False)
            if hasattr(panel, "dismiss_anchor_ui"):
                panel.dismiss_anchor_ui()

    def _on_anchor_confirmed(self, file_type: str, slice_idx: int,
                              scene_x: float, scene_y: float):
        self._anchors[file_type] = _AnchorPoint(slice_idx, scene_x, scene_y)

    def _on_anchor_cleared(self, file_type: str):
        self._anchors.pop(file_type, None)

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
        if self._anchors and src_panel is not None:
            src_anchor = self._anchors.get(src_panel.file_type)
            for p in self._panels:
                if p.viewer is src:
                    continue
                tgt = self._anchors.get(p.file_type)
                if src_anchor is not None and tgt is not None:
                    p.viewer.set_external_cursor(
                        tgt.scene_x + (x - src_anchor.scene_x),
                        tgt.scene_y + (y - src_anchor.scene_y),
                    )
                else:
                    p.viewer.set_external_cursor(x, y)
        else:
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
