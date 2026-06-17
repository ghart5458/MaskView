import csv
import ctypes
import json
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from .. import settings as _settings
from ..files.loader import compute_display_range, load_volume
from ..files.resolver import (
    FILE_TYPE_LABELS, FILE_TYPE_ORDER, display_max, resolve_file,
    resolve_file_from_scan, infer_file_type_from_path,
)
from ..par.parser import Individual, parse_file
from .annotations import AnnotationManager
from .composite_panel import COMPOSITE_TYPE, OverlaySpec
from .multi_viewer import MultiViewer
from .notifications import NotifManager
from .sidebar import Sidebar

_DEFAULT_FILE_TYPES = ["original", "maskseg"]
_PRELOAD_AHEAD      = 3
_PRELOAD_BEHIND     = 0


# ── RAM helpers ───────────────────────────────────────────────────────────────

def _available_ram_gb() -> float:
    try:
        class _MEMSTATEX(ctypes.Structure):
            _fields_ = [
                ("dwLength",                ctypes.c_ulong),
                ("dwMemoryLoad",            ctypes.c_ulong),
                ("ullTotalPhys",            ctypes.c_uint64),
                ("ullAvailPhys",            ctypes.c_uint64),
                ("ullTotalPageFile",        ctypes.c_uint64),
                ("ullAvailPageFile",        ctypes.c_uint64),
                ("ullTotalVirtual",         ctypes.c_uint64),
                ("ullAvailVirtual",         ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]
        s = _MEMSTATEX()
        s.dwLength = ctypes.sizeof(s)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return s.ullAvailPhys / (1024 ** 3)
    except Exception:
        return float("inf")


def _estimate_load_gb(ind: Individual, file_types: list[str], turbo_step: int) -> float:
    """Rough upper-bound estimate of additional RAM for file_types not yet in memory."""
    d1 = max(1, ind.dim1 // turbo_step)
    d2 = max(1, ind.dim2 // turbo_step)
    d3 = max(1, ind.dim3 // turbo_step)
    return len(file_types) * d1 * d2 * d3 * 2 / (1024 ** 3)  # uint16 estimate


# ── Background loader ─────────────────────────────────────────────────────────

class _LoaderThread(QThread):
    file_starting = pyqtSignal(str)
    file_loaded   = pyqtSignal(str, object, float, float)
    file_failed   = pyqtSignal(str, str)
    all_done      = pyqtSignal()

    def __init__(self, ind: Individual, file_types: list[str],
                 turbo_step: int = 1, parent=None):
        super().__init__(parent)
        self._ind        = ind
        self._file_types = file_types
        self._turbo_step = turbo_step
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        for ft in self._file_types:
            if self._stop:
                break
            self.file_starting.emit(ft)
            path = resolve_file(self._ind, ft)
            if path is None:
                self.file_failed.emit(ft, "not found")
                continue
            try:
                data, _ = load_volume(path, use_memmap=False, turbo_step=self._turbo_step)
                dmax = display_max(ft)
                lo, hi = (0.0, float(dmax)) if dmax is not None else compute_display_range(data)
                self.file_loaded.emit(ft, data, lo, hi)
            except Exception as exc:
                self.file_failed.emit(ft, str(exc))
        self.all_done.emit()


# ── Background pre-loader ─────────────────────────────────────────────────────

class _PreloaderThread(QThread):
    chunk_ready = pyqtSignal(int, str, object, float, float)
    all_done    = pyqtSignal()

    def __init__(self, ind_idx: int, ind: Individual, file_types: list[str],
                 turbo_step: int = 1, parent=None):
        super().__init__(parent)
        self._idx        = ind_idx
        self._ind        = ind
        self._file_types = file_types
        self._turbo_step = turbo_step
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        for ft in self._file_types:
            if self._stop:
                break
            path = resolve_file(self._ind, ft)
            if path is None:
                continue
            try:
                data, _ = load_volume(path, use_memmap=False, turbo_step=self._turbo_step)
                dmax = display_max(ft)
                lo, hi = (0.0, float(dmax)) if dmax is not None else compute_display_range(data)
                self.chunk_ready.emit(self._idx, ft, data, lo, hi)
            except Exception:
                pass
            self.msleep(200)
        self.all_done.emit()


# ── Composite channel loader ──────────────────────────────────────────────────

class _CompositeLoaderThread(QThread):
    channel_ready = pyqtSignal(str, object, float, float)
    all_done      = pyqtSignal()

    def __init__(self, ind: Individual, file_types: list[str],
                 turbo_step: int = 1, parent=None):
        super().__init__(parent)
        self._ind        = ind
        self._file_types = file_types
        self._turbo_step = turbo_step
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        for ft in self._file_types:
            if self._stop:
                break
            path = resolve_file(self._ind, ft)
            if path is None:
                continue
            try:
                data, _ = load_volume(path, use_memmap=False, turbo_step=self._turbo_step)
                dmax = display_max(ft)
                lo, hi = (0.0, float(dmax)) if dmax is not None else compute_display_range(data)
                self.channel_ready.emit(ft, data, lo, hi)
            except Exception:
                pass
        self.all_done.emit()


# ── Direct (pre-resolved) loader ──────────────────────────────────────────────

class _DirectLoaderThread(QThread):
    """Like _LoaderThread but takes a pre-resolved {file_type: Path} dict."""
    file_starting = pyqtSignal(str)
    file_loaded   = pyqtSignal(str, object, float, float)
    file_failed   = pyqtSignal(str, str)
    all_done      = pyqtSignal()

    def __init__(self, file_paths: dict, turbo_step: int = 1, parent=None):
        super().__init__(parent)
        self._file_paths = file_paths
        self._turbo_step = turbo_step
        self._stop       = False

    def stop(self):
        self._stop = True

    def run(self):
        for ft, path in self._file_paths.items():
            if self._stop:
                break
            self.file_starting.emit(ft)
            try:
                data, _ = load_volume(path, use_memmap=False, turbo_step=self._turbo_step)
                dmax = display_max(ft)
                lo, hi = (0.0, float(dmax)) if dmax is not None else compute_display_range(data)
                self.file_loaded.emit(ft, data, lo, hi)
            except Exception as exc:
                self.file_failed.emit(ft, str(exc))
        self.all_done.emit()


# ── Session restore overlay ────────────────────────────────────────────────────

class _SessionRestoreOverlay(QWidget):
    """Full-window dim overlay with a centered card asking whether to resume."""
    resume_requested = pyqtSignal()
    dismissed        = pyqtSignal()

    def __init__(self, par_name: str, ind_name: str, parent: QWidget):
        super().__init__(parent)
        self.setGeometry(parent.rect())

        self._card = QWidget(self)
        self._card.setFixedWidth(320)
        self._card.setStyleSheet(
            "QWidget { background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 6px; }"
        )
        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(20, 16, 20, 16)
        card_lay.setSpacing(10)

        title = QLabel("Resume last session?")
        title.setStyleSheet("color: #eee; font-size: 14px; font-weight: bold; border: none;")
        card_lay.addWidget(title)

        par_lbl = QLabel(par_name)
        par_lbl.setStyleSheet("color: #888; font-size: 12px; border: none;")
        par_lbl.setWordWrap(True)
        card_lay.addWidget(par_lbl)

        if ind_name:
            ind_lbl = QLabel(f"Last viewed: {ind_name}")
            ind_lbl.setStyleSheet("color: #666; font-size: 12px; border: none;")
            ind_lbl.setWordWrap(True)
            card_lay.addWidget(ind_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        resume_btn = QPushButton("Resume")
        resume_btn.setStyleSheet(
            "QPushButton { background: #1a3d26; color: #5fd49a;"
            " border: 1px solid #2e6e42; border-radius: 3px; padding: 5px 14px; font-size: 13px; }"
            "QPushButton:hover { background: #147a3f; color: #fff; border: none; }"
        )
        resume_btn.clicked.connect(self._on_resume)

        fresh_btn = QPushButton("Start fresh")
        fresh_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #888;"
            " border: 1px solid #3a3a3a; border-radius: 3px; padding: 5px 14px; font-size: 13px; }"
            "QPushButton:hover { background: #303030; color: #ccc; border: none; }"
        )
        fresh_btn.clicked.connect(self._dismiss)

        btn_row.addWidget(resume_btn)
        btn_row.addWidget(fresh_btn)
        card_lay.addLayout(btn_row)

        self._card.adjustSize()
        self._center_card()
        self.grabKeyboard()

    def _center_card(self):
        self._card.adjustSize()
        self._card.move(
            (self.width()  - self._card.width())  // 2,
            (self.height() - self._card.height()) // 2,
        )

    def _on_resume(self):
        self.resume_requested.emit()
        self._dismiss()

    def _dismiss(self):
        self.releaseKeyboard()
        self.dismissed.emit()
        self.setParent(None)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_resume()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if not self._card.geometry().contains(event.pos()):
            self._dismiss()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 160))


# ── Selective-clear dialog ────────────────────────────────────────────────────

class _SelectiveClearDialog(QDialog):
    """Checkbox dialog for picking which file-type categories to wipe."""

    def __init__(
        self,
        file_types: list[str],
        labels: dict[str, str],
        *,
        include_notes: bool = False,
        title: str = "Clear data",
        warning_text: str = "This action cannot be undone.",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self.setStyleSheet("QDialog { background: #1e1e1e; color: #ddd; }")

        self.selected_file_types: list[str] = []
        self.clear_notes: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        warn = QLabel(warning_text)
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #c8a84a; font-size: 12px;")
        layout.addWidget(warn)

        prompt = QLabel("Select what to delete:")
        prompt.setStyleSheet("color: #ccc; font-size: 12px; margin-top: 4px;")
        layout.addWidget(prompt)

        self._checkboxes: dict[str, QCheckBox] = {}
        for ft in file_types:
            cb = QCheckBox(labels.get(ft, ft))
            cb.setStyleSheet(
                "QCheckBox { color: #ddd; font-size: 12px; spacing: 6px; }"
            )
            layout.addWidget(cb)
            self._checkboxes[ft] = cb

        if include_notes:
            self._notes_cb = QCheckBox("Individual notes")
            self._notes_cb.setStyleSheet(
                "QCheckBox { color: #ddd; font-size: 12px; spacing: 6px; }"
            )
            layout.addWidget(self._notes_cb)
        else:
            self._notes_cb = None

        layout.addSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton { background: #252525; color: #888; border: 1px solid #3a3a3a;"
            " border-radius: 3px; padding: 5px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #303030; color: #ccc; }"
        )
        cancel_btn.clicked.connect(self.reject)

        clear_btn = QPushButton("Clear selected")
        clear_btn.setStyleSheet(
            "QPushButton { background: #3d1a1a; color: #cc3333; border: 1px solid #6e2e2e;"
            " border-radius: 3px; padding: 5px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #5a1a1a; color: #ff5555; border: none; }"
        )
        clear_btn.clicked.connect(self._on_clear)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

    def _on_clear(self):
        self.selected_file_types = [
            ft for ft, cb in self._checkboxes.items() if cb.isChecked()
        ]
        self.clear_notes = self._notes_cb is not None and self._notes_cb.isChecked()
        if not self.selected_file_types and not self.clear_notes:
            return
        self.accept()


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._par_path: Path | None = None

        _saved = _settings.load()
        self._session_types: list[str] = _saved.get('checked_file_types', list(_DEFAULT_FILE_TYPES))
        self._saved_turbo_stride: int  = _saved.get('turbo_stride', 1)
        self._loading = False
        self._loader: _LoaderThread | None = None
        self._turbo_step = self._saved_turbo_stride

        self._single_scan_mode    = False
        self._scan_base_path: Path | None = None
        self._scan_resolved_paths: dict   = {}

        self._preload_cache: dict[int, dict[str, tuple]] = {}
        self._preload_complete: set[int] = set()
        self._prev_idx: int | None = None
        self._preload_queue: list[int] = []
        self._preloaders: list[_PreloaderThread] = []
        self._zombie_preloaders: list[_PreloaderThread] = []

        self._cache_cleanup_timer = QTimer(self)
        self._cache_cleanup_timer.setSingleShot(True)
        self._cache_cleanup_timer.timeout.connect(self._cleanup_stale_cache)

        self._overlay_cache: dict[int, OverlaySpec] = {}
        self._composite_loader: _CompositeLoaderThread | None = None
        self._pending_spec: OverlaySpec | None = None
        self._pending_channels: list | None = None
        self._deferred_composite: list | None = None

        self._annot_mgr = AnnotationManager()

        self.setAcceptDrops(True)
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("MaskView")
        self.setStyleSheet("QMainWindow { background: #111; }")

        self._sidebar = Sidebar()
        self._sidebar.apply_saved_settings(self._saved_turbo_stride, self._session_types)
        self._viewer  = MultiViewer()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #2c2c2c; }"
            "QSplitter::handle:hover { background: #3a3a3a; }"
        )
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._viewer)
        splitter.setSizes([280, 1200])
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, False)

        content = QWidget()
        content.setStyleSheet("background: #111;")
        vbox = QVBoxLayout(content)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(splitter)
        self.setCentralWidget(content)

        self._notifs = NotifManager(content)

        self._sidebar.par_selected.connect(self._on_par_selected)
        self._sidebar.par_refresh_requested.connect(self._on_par_refresh)
        self._sidebar.load_requested.connect(self._on_load_requested)
        self._sidebar.scan_selected.connect(self._on_scan_selected)
        self._sidebar.manual_files_selected.connect(self._on_manual_files_selected)
        self._sidebar.files_applied.connect(self._on_files_applied)
        self._sidebar.orientation_changed.connect(self._viewer.set_orientation)
        self._sidebar.layout_changed.connect(self._viewer.set_layout_mode)
        self._sidebar.sync_toggled.connect(self._viewer.set_sync)
        self._sidebar.turbo_changed.connect(self._on_turbo_changed)
        self._sidebar.individual_selected.connect(self._on_individual_selected)
        self._sidebar.composite_requested.connect(self._on_composite_requested)
        self._sidebar.composite_updated.connect(self._on_composite_updated)
        self._sidebar.composite_blend_changed.connect(self._on_blend_changed)

        self._sidebar.annotation_changed.connect(self._on_annotation_changed)
        self._sidebar.annotation_note_changed.connect(self._on_annotation_note_changed)
        self._sidebar.filter_changed.connect(self._on_filter_changed)
        self._sidebar.export_annotations_requested.connect(self._on_annotations_export)
        self._sidebar.clear_annotations_requested.connect(self._on_annotations_clear_all)
        self._sidebar.export_tags_requested.connect(self._on_export_tags)
        self._sidebar.tags_visible_changed.connect(self._viewer.set_tags_visible)
        self._sidebar.tag_selected.connect(self._on_tag_selected)
        self._sidebar.tag_edit_requested.connect(self._on_tag_edit_from_sidebar)
        self._sidebar.tag_delete_requested.connect(self._on_tag_delete_from_sidebar)
        self._sidebar.tags_delete_many_requested.connect(self._on_tags_delete_many)
        self._sidebar.tags_clear_requested.connect(self._on_tags_clear)
        self._sidebar.tags_clear_all_requested.connect(self._on_tags_clear_all)
        self._viewer.panel_tags_changed.connect(self._sidebar.update_tag_list)

        self._viewer.panel_closed.connect(self._on_panel_closed)
        self._viewer.composite_target_selected.connect(self._on_composite_target_selected)

        self._sidebar.anchor_mode_requested.connect(self._viewer.enter_anchor_mode)
        self._sidebar.anchor_apply_requested.connect(self._on_anchor_apply)
        self._sidebar.anchor_cancel_requested.connect(self._on_anchor_cancel)
        self._sidebar.anchor_clear_requested.connect(self._on_anchor_clear)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_shown_once', False):
            self._shown_once = True
            QTimer.singleShot(150, self._maybe_show_session_restore)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._notifs.reposition()
        overlay = getattr(self, '_session_overlay', None)
        if overlay is not None:
            content = self.centralWidget()
            if content:
                overlay.setGeometry(content.rect())

    _DROP_SUFFIXES = {'.par', '.csv', '.mhd'}

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and all(
            Path(u.toLocalFile()).suffix.lower() in self._DROP_SUFFIXES
            for u in urls
            if u.isLocalFile()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        suffix = path.suffix.lower()
        if suffix in ('.par', '.csv'):
            self._on_par_selected(path)
        elif suffix == '.mhd':
            self._on_scan_selected(path)

    def _maybe_show_session_restore(self):
        saved = _settings.load()
        last_par = saved.get('last_par_file')
        if not last_par or not Path(last_par).exists():
            return
        last_idx  = saved.get('last_individual_idx', 0)
        last_name = saved.get('last_individual_name', '')
        content = self.centralWidget()
        if content is None:
            return
        overlay = _SessionRestoreOverlay(Path(last_par).name, last_name, content)
        overlay.resume_requested.connect(lambda: self._on_session_resume(last_par, last_idx))
        overlay.dismissed.connect(self._on_session_overlay_dismissed)
        self._session_overlay = overlay
        overlay.show()
        overlay.raise_()

    def _on_session_overlay_dismissed(self):
        self._session_overlay = None

    def _on_session_resume(self, par_path_str: str, individual_idx: int):
        path = Path(par_path_str)
        if not path.exists():
            return
        self._on_par_selected(path)
        n = len(self._individuals)
        if n == 0:
            return
        idx = min(individual_idx, n - 1)
        if idx > 0:
            self._sidebar.select_individual_silent(idx)
            self._current_idx = idx
        ind = self._individuals[idx]
        # _on_par_selected resets last_individual_idx to 0; write the real value back.
        _settings.save({'last_individual_idx': idx, 'last_individual_name': ind.oldname})
        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        to_load = [ft for ft in self._session_types if available.get(ft)]
        self._sidebar.update_file_availability(available, set(to_load))
        self._start_load(ind, to_load)

    # ── Anchor sync ───────────────────────────────────────────────────────────

    def _on_anchor_apply(self):
        self._viewer.apply_anchor_sync()
        self._sidebar.update_anchor_state(active=False, has_anchors=True)

    def _on_anchor_cancel(self):
        self._viewer.exit_anchor_mode()
        self._sidebar.update_anchor_state(
            active=False, has_anchors=bool(self._viewer.anchors)
        )

    def _on_anchor_clear(self):
        self._viewer.clear_anchors()
        self._sidebar.update_anchor_state(active=False, has_anchors=False)

    def _clear_anchors_on_nav(self):
        if self._viewer.anchors or self._viewer._anchor_mode:
            self._viewer.clear_anchors()
            self._sidebar.update_anchor_state(active=False, has_anchors=False)

    # ── Session ───────────────────────────────────────────────────────────────

    def _on_turbo_changed(self, step: int):
        self._turbo_step = step
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None
        self._cache_cleanup_timer.stop()
        _settings.save({'turbo_stride': step})

    def _on_par_selected(self, path: Path):
        self._clear_anchors_on_nav()
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None
        self._cache_cleanup_timer.stop()
        self._single_scan_mode = False
        self._par_path = path
        _settings.save({'last_par_file': str(path), 'last_individual_idx': 0, 'last_individual_name': ''})
        self._sidebar.set_par_label(path)
        self._individuals = parse_file(path)
        self._annot_mgr.load(
            path,
            [ind.oldname for ind in self._individuals],
            FILE_TYPE_ORDER,
        )
        self._sidebar.load_individuals(self._individuals)
        self._init_annotation_indicators()
        if not self._individuals:
            return
        self._current_idx = 0
        ind = self._individuals[0]
        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        default_checked = {ft for ft in self._session_types if available.get(ft)}
        self._sidebar.update_file_availability(available, default_checked)
        self._sidebar.select_individual_silent(0)

    def _on_par_refresh(self):
        if self._par_path is None or self._single_scan_mode:
            return
        current_oldname = (
            self._individuals[self._current_idx].oldname
            if 0 <= self._current_idx < len(self._individuals) else None
        )
        prev_oldname = (
            self._individuals[self._prev_idx].oldname
            if self._prev_idx is not None and 0 <= self._prev_idx < len(self._individuals) else None
        )

        # Stop in-progress preloaders (evicts their partial cache entries).
        self._cancel_preloaders()
        self._cache_cleanup_timer.stop()

        # Snapshot the completed cache keyed by oldname before clearing index
        # structures.  Partial entries were already evicted by _cancel_preloaders.
        cached_by_name = {
            self._individuals[idx].oldname: data
            for idx, data in self._preload_cache.items()
            if 0 <= idx < len(self._individuals)
        }
        complete_by_name = {
            self._individuals[idx].oldname
            for idx in self._preload_complete
            if 0 <= idx < len(self._individuals)
        }

        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None

        self._individuals = parse_file(self._par_path)
        self._annot_mgr.load(
            self._par_path,
            [ind.oldname for ind in self._individuals],
            FILE_TYPE_ORDER,
        )
        self._sidebar.load_individuals(self._individuals)
        self._init_annotation_indicators()

        if not self._individuals:
            self._current_idx = -1
            return

        # Remap surviving cache entries to the new index positions.
        for new_idx, ind in enumerate(self._individuals):
            if ind.oldname in cached_by_name:
                self._preload_cache[new_idx] = cached_by_name[ind.oldname]
            if ind.oldname in complete_by_name:
                self._preload_complete.add(new_idx)

        # Remap prev_idx by name so the eviction guard still works.
        if prev_oldname:
            for i, ind in enumerate(self._individuals):
                if ind.oldname == prev_oldname:
                    self._prev_idx = i
                    break

        if current_oldname:
            for i, ind in enumerate(self._individuals):
                if ind.oldname == current_oldname:
                    self._current_idx = i
                    self._sidebar.select_individual_silent(i)
                    self._refresh_preload_indicators()
                    return
        self._current_idx = 0
        self._sidebar.select_individual_silent(0)
        self._refresh_preload_indicators()

    def _on_load_requested(self, idx: int, file_types: list[str]):
        self._session_types = list(file_types)
        _settings.save({'checked_file_types': file_types})
        self._on_individual_selected(idx)

    def _on_scan_selected(self, path: Path):
        self._clear_anchors_on_nav()
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None
        self._cache_cleanup_timer.stop()
        self._single_scan_mode = True
        self._par_path = None

        # Derive base_path: if the MHD is inside a numbered subfolder (e.g. 00_Original),
        # go up two levels; otherwise use the immediate parent.
        parent_name = path.parent.name
        if parent_name and parent_name[0].isdigit():
            base_path = path.parent.parent
        else:
            base_path = path.parent
        self._scan_base_path = base_path

        # Always include the file the user actually selected, regardless of checkbox state
        selected_ft = infer_file_type_from_path(path)

        # Resolve companion files for every currently-checked file type
        checked = self._sidebar.checked_file_types()
        want = list(checked)
        if selected_ft and selected_ft not in want:
            want.append(selected_ft)

        resolved: dict = {}
        for ft in want:
            if ft == selected_ft:
                resolved[ft] = path          # use the file the user picked directly
            else:
                p = resolve_file_from_scan(base_path, ft)
                if p is not None:
                    resolved[ft] = p
        self._scan_resolved_paths = dict(resolved)

        missing = [ft for ft in want if ft not in resolved]
        if missing:
            labels = ", ".join(FILE_TYPE_LABELS.get(ft, ft) for ft in missing)
            self._notifs.show(
                "Files not found",
                f"Could not locate: {labels}",
                "warning",
            )

        if not resolved:
            return

        oldname = path.stem
        ind = Individual(
            oldname=oldname, name=oldname,
            res=0.0, dim1=0, dim2=0, dim3=0,
            kc='0', kpoint='0', kout='0', kin='0',
            path=str(base_path.parent),
            species='', population='', specimen='', bone='', portion='',
            raw_fields={},
        )
        self._individuals    = [ind]
        self._current_idx    = 0

        self._annot_mgr.load(
            base_path / oldname,
            [oldname],
            FILE_TYPE_ORDER,
        )

        self._sidebar.set_par_label(None)
        available = {ft: (ft in resolved) for ft in FILE_TYPE_ORDER}
        self._sidebar.update_file_availability(available, set(resolved.keys()))
        self._sidebar.load_individuals([ind])
        self._init_annotation_indicators()
        self._sidebar.select_individual_silent(0)
        self._sidebar.update_tag_list([], "")

        self._start_load_direct(resolved)

    def _on_manual_files_selected(self, file_paths: dict):
        if not file_paths:
            return
        self._clear_anchors_on_nav()
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None
        self._cache_cleanup_timer.stop()
        self._single_scan_mode = True
        self._par_path = None

        first_path = next(iter(file_paths.values()))
        base_path = first_path.parent
        self._scan_base_path = base_path
        self._scan_resolved_paths = dict(file_paths)

        oldname = first_path.stem
        ind = Individual(
            oldname=oldname, name=oldname,
            res=0.0, dim1=0, dim2=0, dim3=0,
            kc='0', kpoint='0', kout='0', kin='0',
            path=str(base_path.parent),
            species='', population='', specimen='', bone='', portion='',
            raw_fields={},
        )
        self._individuals = [ind]
        self._current_idx = 0

        self._annot_mgr.load(
            base_path / oldname,
            [oldname],
            FILE_TYPE_ORDER,
        )

        self._sidebar.set_par_label(None)
        available = {ft: (ft in file_paths) for ft in FILE_TYPE_ORDER}
        self._sidebar.update_file_availability(available, set(file_paths.keys()))
        self._sidebar.load_individuals([ind])
        self._init_annotation_indicators()
        self._sidebar.select_individual_silent(0)
        self._sidebar.update_tag_list([], "")

        self._start_load_direct(file_paths)

    def _start_load_direct(self, file_paths: dict):
        self._cancel_loader()
        self._viewer.clear()
        self._refresh_annotations([])
        self._update_composite_channels()
        self._sidebar.update_tag_list([], "")
        self._loader_resolved = dict(file_paths)  # paths already resolved
        for ft in file_paths:
            self._viewer.add_empty_panel(ft)
        self._refresh_annotations(self._panel_fts_for_annotations())
        self._update_composite_channels()
        self._loading = True
        self._sidebar.set_controls_enabled(False)
        self._loader = _DirectLoaderThread(file_paths, self._turbo_step)
        self._loader.file_loaded.connect(self._on_file_loaded)
        self._loader.file_failed.connect(self._on_file_failed)
        self._loader.all_done.connect(self._on_load_done)
        self._loader.start()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_individual_selected(self, idx: int):
        if idx == self._current_idx and self._loading:
            return
        self._cancel_deferred_composite()
        self._clear_anchors_on_nav()
        self._save_current_to_cache(next_idx=idx)
        self._current_idx = idx
        ind = self._individuals[idx]
        _settings.save({'last_individual_idx': idx, 'last_individual_name': ind.oldname})

        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        to_load = [ft for ft in self._session_types if available.get(ft)]
        self._sidebar.update_file_availability(available, set(to_load))

        cached = self._preload_cache.pop(idx, {})
        if cached:
            self._cancel_loader()
            self._viewer.clear()
            self._refresh_annotations([])

            for ft in to_load:
                if ft in cached:
                    data, lo, hi = cached[ft]
                    self._viewer.add_panel(ft, data, lo, hi)
                    panel = next((p for p in self._viewer.panels if p.file_type == ft), None)
                    if panel is not None:
                        panel.viewer.set_turbo_step(self._turbo_step)
                    self._sidebar.set_file_loaded(ft, True)
                    path = resolve_file(ind, ft)
                    if path:
                        self._viewer.set_panel_filename(ft, path)

            missing = [ft for ft in to_load if ft not in cached]
            if missing:
                self._launch_loader(ind, missing)
            else:
                self._sidebar.set_controls_enabled(True)
                QTimer.singleShot(0, self._viewer.sync_all)
                QTimer.singleShot(0, self._viewer.emit_active_panel_tags)
                QTimer.singleShot(200, lambda: self._start_preload(idx))
                self._cache_cleanup_timer.start(6000)
                self._maybe_restore_overlay()

            self._refresh_annotations(self._panel_fts_for_annotations())
            self._update_composite_channels()
        else:
            self._start_load(ind, to_load)

    def _save_current_to_cache(self, next_idx: int):
        cur = self._current_idx
        if cur < 0 or cur in self._preload_cache or self._loading:
            return
        entry = {
            p.file_type: (p.viewer.data, *p.viewer.display_range)
            for p in self._viewer.panels
            if p.viewer.data is not None
        }
        if not entry:
            return
        self._preload_cache[cur] = entry
        self._preload_complete.add(cur)
        self._prev_idx = cur
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._preload_cache) > max_cached:
            candidates = [k for k in self._preload_cache if k != self._prev_idx]
            if candidates:
                farthest = max(candidates, key=lambda k: abs(k - next_idx))
                del self._preload_cache[farthest]
        self._refresh_preload_indicators()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _start_load(self, ind: Individual, file_types: list[str]):
        self._cancel_deferred_composite()
        self._cancel_loader()
        self._cancel_preloaders()
        self._viewer.clear()
        self._refresh_annotations([])
        self._update_composite_channels()
        self._sidebar.update_tag_list([], "")
        self._launch_loader(ind, file_types)

    def _launch_loader(self, ind: Individual, file_types: list[str]):
        if not file_types:
            return
        # Resolve paths now, before I/O starts, so _on_file_loaded never calls
        # resolve_file() on the main thread while the disk is busy loading.
        self._loader_resolved = {ft: resolve_file(ind, ft) for ft in file_types}
        for ft in file_types:
            self._viewer.add_empty_panel(ft)
        self._refresh_annotations(self._panel_fts_for_annotations())
        self._update_composite_channels()
        self._loading = True
        self._sidebar.set_controls_enabled(False)
        self._loader = _LoaderThread(ind, file_types, self._turbo_step)
        self._loader.file_loaded.connect(self._on_file_loaded)
        self._loader.file_failed.connect(self._on_file_failed)
        self._loader.all_done.connect(self._on_load_done)
        self._loader.start()
        self._refresh_preload_indicators()

    def _cancel_loader(self):
        if self._loader is not None:
            for sig in (self._loader.file_loaded,
                        self._loader.file_failed, self._loader.all_done):
                sig.disconnect()
            self._loader.stop()
            self._loader = None
        self._loading = False

    def _on_file_loaded(self, ft: str, data: object, lo: float, hi: float):
        self._viewer.fill_panel(ft, data, lo, hi)
        panel = next((p for p in self._viewer.panels if p.file_type == ft), None)
        if panel is not None:
            panel.viewer.set_turbo_step(self._turbo_step)
        self._sidebar.set_file_loaded(ft, True)
        # Use paths pre-resolved before loading started — no filesystem calls here.
        path = getattr(self, '_loader_resolved', {}).get(ft)
        if path:
            self._viewer.set_panel_filename(ft, path)

    def _on_file_failed(self, ft: str, _msg: str):
        self._viewer.close_panel(ft)

    def _on_load_done(self):
        self._loading = False
        self._sidebar.set_controls_enabled(True)
        self._loader = None
        self._refresh_preload_indicators()
        QTimer.singleShot(0, self._viewer.sync_all)
        QTimer.singleShot(0, self._viewer.emit_active_panel_tags)
        if not self._single_scan_mode:
            QTimer.singleShot(200, lambda: self._start_preload(self._current_idx))
        self._cache_cleanup_timer.start(6000)
        self._check_dimension_mismatch()
        self._maybe_restore_overlay()
        self._refresh_annotations(self._panel_fts_for_annotations())
        self._update_composite_channels()
        if self._deferred_composite is not None:
            specs = self._deferred_composite
            self._deferred_composite = None
            self._sidebar.set_composite_pending(False)
            self._on_composite_requested(specs)

    # ── Pre-loading ───────────────────────────────────────────────────────────

    def _cleanup_stale_cache(self):
        """Evict cache entries outside the current preload range (except prev_idx).

        Called 6 seconds after settling on an individual, giving a grace period
        for misclicks before stale green indicators are removed.
        """
        lo = self._current_idx - _PRELOAD_BEHIND
        hi = self._current_idx + _PRELOAD_AHEAD
        stale = [k for k in list(self._preload_cache)
                 if k != self._prev_idx and not (lo <= k <= hi)]
        for k in stale:
            del self._preload_cache[k]
        if stale:
            self._refresh_preload_indicators()

    def _start_preload(self, current_idx: int):
        self._cancel_preloaders()
        n = len(self._individuals)
        mode = self._sidebar.filter_mode
        if mode == "All":
            forward  = range(current_idx + 1, min(current_idx + _PRELOAD_AHEAD + 1, n))
            backward = range(max(0, current_idx - _PRELOAD_BEHIND), current_idx)
            candidates = list(forward) + list(backward)
        else:
            filtered = self._sidebar.filtered_indices
            try:
                pos = filtered.index(current_idx)
            except ValueError:
                pos = -1
            if pos >= 0:
                ahead  = filtered[pos + 1: pos + 1 + _PRELOAD_AHEAD]
                behind = filtered[max(0, pos - _PRELOAD_BEHIND): pos]
            else:
                ahead  = filtered[:_PRELOAD_AHEAD]
                behind = []
            candidates = list(ahead) + list(behind)
        self._preload_queue = [i for i in candidates if i not in self._preload_cache]
        self._start_next_preload()

    def _start_next_preload(self):
        if any(p.isRunning() for p in self._preloaders):
            return  # one preloader at a time
        while self._preload_queue:
            idx = self._preload_queue.pop(0)
            if idx in self._preload_cache:
                continue
            ind = self._individuals[idx]
            loader = _PreloaderThread(idx, ind, self._session_types, self._turbo_step)
            loader.chunk_ready.connect(self._on_preload_chunk)
            loader.start(QThread.Priority.LowPriority)
            self._preloaders.append(loader)
            self._refresh_preload_indicators()
            QTimer.singleShot(100, self._poll_preloaders)
            return

    def _poll_preloaders(self):
        finished = [p for p in self._preloaders if not p.isRunning()]
        for loader in finished:
            self._preloaders.remove(loader)
            self._zombie_preloaders.append(loader)
            self._preload_complete.add(loader._idx)
        if finished:
            self._refresh_preload_indicators()
            self._start_next_preload()
        elif self._preloaders:
            QTimer.singleShot(100, self._poll_preloaders)

    def _cancel_preloaders(self):
        self._preload_queue = []
        self._zombie_preloaders = [l for l in self._zombie_preloaders if l.isRunning()]
        for loader in self._preloaders:
            try:
                loader.chunk_ready.disconnect()
            except RuntimeError:
                pass
            loader.stop()
            # Evict partial cache entries — a cancelled preloader may have written
            # some but not all file types. Leaving a partial entry would cause
            # _start_preload to skip re-queuing this individual indefinitely.
            if (loader._idx in self._preload_cache
                    and loader._idx not in self._preload_complete):
                del self._preload_cache[loader._idx]
        self._zombie_preloaders.extend(self._preloaders)
        self._preloaders.clear()
        self._refresh_preload_indicators()

    def _on_preload_chunk(self, idx: int, ft: str, data: object, lo: float, hi: float):
        if idx not in self._preload_cache:
            self._preload_cache[idx] = {}
        self._preload_cache[idx][ft] = (data, lo, hi)
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._preload_cache) > max_cached:
            candidates = [k for k in self._preload_cache
                          if k != self._prev_idx and k != idx]
            if candidates:
                farthest = max(candidates, key=lambda k: abs(k - self._current_idx))
                del self._preload_cache[farthest]
            elif self._prev_idx in self._preload_cache and self._prev_idx != idx:
                del self._preload_cache[self._prev_idx]
        self._refresh_preload_indicators()

    def _refresh_preload_indicators(self):
        # Green only for fully-loaded individuals (thread finished), not just
        # any individual with a partial cache entry (first file arrived).
        cached  = self._preload_complete & set(self._preload_cache.keys())
        loading = {p._idx for p in self._preloaders if p.isRunning()}
        if self._current_idx >= 0:
            if self._loader is not None and self._loader.isRunning():
                loading.add(self._current_idx)
            elif not self._loading:
                cached.add(self._current_idx)
        self._sidebar.update_preload_indicators(cached, loading)

    # ── File selection ────────────────────────────────────────────────────────

    def _on_files_applied(self, file_types: list[str]):
        if self._current_idx < 0:
            return
        self._session_types = list(file_types)
        _settings.save({'checked_file_types': file_types})
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._preload_complete.clear()
        self._prev_idx = None
        self._cache_cleanup_timer.stop()
        self._cancel_loader()

        for panel in list(self._viewer.panels):
            if panel.file_type not in file_types and panel.file_type != COMPOSITE_TYPE:
                self._viewer.close_panel(panel.file_type)

        already_open = {p.file_type for p in self._viewer.panels}
        to_load = [ft for ft in file_types if ft not in already_open]

        if to_load:
            if self._single_scan_mode:
                resolved = {}
                for ft in to_load:
                    p = resolve_file_from_scan(self._scan_base_path, ft)
                    if p is not None:
                        resolved[ft] = p
                        self._scan_resolved_paths[ft] = p
                missing = [ft for ft in to_load if ft not in resolved]
                if missing:
                    labels = ", ".join(FILE_TYPE_LABELS.get(ft, ft) for ft in missing)
                    self._notifs.show("Files not found",
                                      f"Could not locate: {labels}", "warning")
                if resolved:
                    self._start_load_direct(resolved)
            else:
                ind = self._individuals[self._current_idx]
                self._launch_loader(ind, to_load)

        self._refresh_annotations(self._panel_fts_for_annotations())

    def _on_panel_closed(self, ft: str):
        if ft != COMPOSITE_TYPE:
            self._sidebar.set_file_loaded(ft, False)
        self._refresh_annotations(self._panel_fts_for_annotations())
        self._update_composite_channels()

    # ── Warnings ──────────────────────────────────────────────────────────────

    def _check_dimension_mismatch(self):
        panels_with_data = [
            p for p in self._viewer.panels
            if p.file_type != COMPOSITE_TYPE and p.viewer.data is not None
        ]
        if len(panels_with_data) < 2:
            return
        shapes = {p.viewer.data.shape for p in panels_with_data}
        if len(shapes) > 1:
            self._notifs.show(
                "Dimension Mismatch",
                "Open volumes have different dimensions — use ANCHOR SYNC in the sidebar "
                "to place matching reference points and enable offset sync.",
                "warning",
            )

    # ── Color composite ───────────────────────────────────────────────────────

    def _cancel_deferred_composite(self):
        if self._deferred_composite is not None:
            self._deferred_composite = None
            self._sidebar.set_composite_pending(False)

    def _on_composite_requested(self, specs: list):
        if self._current_idx < 0:
            return
        if self._loading:
            self._deferred_composite = specs
            self._sidebar.set_composite_pending(True)
            return
        ind = self._individuals[self._current_idx]

        file_types = [s[0] for s in specs]
        colors     = [s[1] for s in specs]
        opacities  = [s[2] for s in specs]

        spec = OverlaySpec(
            file_types=file_types,
            colors=colors,
            opacities=opacities,
            replaces=None,
        )

        needs_loading = []
        for ft in file_types:
            panel = next((p for p in self._viewer.panels if p.file_type == ft), None)
            if panel is not None and panel.viewer.data is not None:
                spec.data[ft] = panel.viewer.data
                spec.display_ranges[ft] = panel.viewer.display_range
            elif ft in self._preload_cache.get(self._current_idx, {}):
                data, lo, hi = self._preload_cache[self._current_idx][ft]
                spec.data[ft] = data
                spec.display_ranges[ft] = (lo, hi)
            else:
                needs_loading.append(ft)

        # RAM warning before any disk I/O
        if needs_loading:
            est_gb   = _estimate_load_gb(ind, needs_loading, self._turbo_step)
            avail_gb = _available_ram_gb()
            if avail_gb < float("inf") and est_gb > avail_gb * 0.7:
                self._notifs.show(
                    "Memory Warning",
                    f"Loading these channels (~{est_gb:.1f} GB) may use most of your "
                    f"available RAM ({avail_gb:.1f} GB free). Consider enabling Turbo "
                    "Mode or reducing preload to 1 individual ahead.",
                    "warning",
                )

        self._pending_spec = spec
        if not needs_loading:
            self._launch_composite(spec)
        else:
            if self._composite_loader is not None:
                self._composite_loader.stop()
            self._composite_loader = _CompositeLoaderThread(
                ind, needs_loading, self._turbo_step
            )
            self._composite_loader.channel_ready.connect(self._on_composite_channel_ready)
            self._composite_loader.all_done.connect(self._on_composite_load_done)
            self._composite_loader.start()

    def _on_composite_channel_ready(self, ft: str, data: object, lo: float, hi: float):
        if self._pending_spec is None:
            return
        self._pending_spec.data[ft] = data
        self._pending_spec.display_ranges[ft] = (lo, hi)

    def _on_composite_load_done(self):
        self._composite_loader = None
        if self._pending_spec is None:
            return
        spec = self._pending_spec
        self._pending_spec = None
        self._launch_composite(spec)

    def _launch_composite(self, spec: OverlaySpec):
        channels = []
        for ft, color, opacity in zip(spec.file_types, spec.colors, spec.opacities):
            if ft in spec.data:
                lo, hi = spec.display_ranges.get(ft, (0.0, 1.0))
                channels.append((spec.data[ft], lo, hi, color, opacity))
        if not channels:
            return

        spec.blend_mode = self._sidebar.composite_blend_mode
        self._pending_channels = channels
        self._pending_spec = spec

        real_panels = [p for p in self._viewer.panels if p.file_type != COMPOSITE_TYPE]
        if len(real_panels) < 4:
            spec.replaces = None
            panel = self._viewer.add_composite_panel(channels)
            panel.viewer.set_blend_mode(spec.blend_mode)
            self._store_overlay(spec)
            self._pending_channels = None
            self._pending_spec = None
        else:
            self._viewer.enter_selection_mode()

    def _on_composite_target_selected(self, file_type: str):
        spec     = self._pending_spec
        channels = self._pending_channels
        self._pending_spec     = None
        self._pending_channels = None
        if spec is None or channels is None:
            return
        spec.replaces = file_type
        panel = self._viewer.replace_with_composite(file_type, channels)
        if panel is not None:
            panel.viewer.set_blend_mode(spec.blend_mode)
        self._store_overlay(spec)
        self._update_composite_channels()

    def _store_overlay(self, spec: OverlaySpec):
        idx = self._current_idx
        self._overlay_cache[idx] = spec
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._overlay_cache) > max_cached:
            farthest = max(self._overlay_cache, key=lambda k: abs(k - idx))
            del self._overlay_cache[farthest]

    def _maybe_restore_overlay(self):
        spec = self._overlay_cache.get(self._current_idx)
        if spec is None:
            return
        channels = []
        for ft, color, opacity in zip(spec.file_types, spec.colors, spec.opacities):
            if ft in spec.data:
                lo, hi = spec.display_ranges.get(ft, (0.0, 1.0))
                channels.append((spec.data[ft], lo, hi, color, opacity))
        if not channels:
            return
        real_panels = [p for p in self._viewer.panels if p.file_type != COMPOSITE_TYPE]
        if len(real_panels) < 4:
            panel = self._viewer.add_composite_panel(channels)
            panel.viewer.set_blend_mode(spec.blend_mode)
        elif spec.replaces and any(p.file_type == spec.replaces for p in self._viewer.panels):
            panel = self._viewer.replace_with_composite(spec.replaces, channels)
            if panel is not None:
                panel.viewer.set_blend_mode(spec.blend_mode)

    def _on_blend_changed(self, mode: str):
        comp_panel = next(
            (p for p in self._viewer.panels if p.file_type == COMPOSITE_TYPE), None
        )
        if comp_panel is not None:
            comp_panel.viewer.set_blend_mode(mode)
        cached = self._overlay_cache.get(self._current_idx)
        if cached is not None:
            cached.blend_mode = mode

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_annotations(self, fts: list[str]) -> None:
        """Rebuild annotation buttons and restore saved state for the current individual."""
        self._sidebar.update_annotations(fts)
        if 0 <= self._current_idx < len(self._individuals):
            ind = self._individuals[self._current_idx]
            if fts:
                self._sidebar.set_annotations(self._annot_mgr.get_row(ind.oldname))
            self._sidebar.set_annotation_note(self._annot_mgr.get_note(ind.oldname))

    def _on_tag_selected(self, file_type: str, x: int, y: int, z: int, tag_id: str) -> None:
        panel = next((p for p in self._viewer.panels if p.file_type == file_type), None)
        if panel is None:
            return
        orientation = self._viewer.orientation
        s = self._turbo_step
        vox_idx = z if orientation == "XY" else (y if orientation == "XZ" else x)
        panel.viewer.jump_to_slice(vox_idx // s)
        panel.highlight_tag(tag_id)

    def _on_tag_edit_from_sidebar(self, file_type: str, tag_id: str) -> None:
        panel = next((p for p in self._viewer.panels if p.file_type == file_type), None)
        if panel is not None:
            panel.edit_tag(tag_id)

    def _on_tag_delete_from_sidebar(self, file_type: str, tag_id: str) -> None:
        panel = next((p for p in self._viewer.panels if p.file_type == file_type), None)
        if panel is not None:
            panel.delete_tag(tag_id)

    def _on_tags_delete_many(self, file_type: str, tag_ids: list) -> None:
        panel = next((p for p in self._viewer.panels if p.file_type == file_type), None)
        if panel is not None:
            panel.delete_tags(tag_ids)

    def _on_tags_clear(self, file_type: str) -> None:
        panel = next((p for p in self._viewer.panels if p.file_type == file_type), None)
        if panel is not None:
            panel.clear_tags()

    def _on_tags_clear_all(self) -> None:
        if not self._individuals:
            return
        dlg = _SelectiveClearDialog(
            FILE_TYPE_ORDER,
            FILE_TYPE_LABELS,
            include_notes=False,
            title="Clear all tags",
            warning_text=(
                "This will permanently remove tag JSON files from disk for every individual "
                "in the selected file types. This cannot be undone."
            ),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fts_to_clear = set(dlg.selected_file_types)
        for panel in self._viewer.panels:
            if panel.file_type in fts_to_clear:
                panel.clear_tags()
        deleted = 0
        for ind in self._individuals:
            for ft in fts_to_clear:
                vol_path = resolve_file(ind, ft)
                if vol_path is None:
                    continue
                json_path = vol_path.with_name(vol_path.stem + "_MV_tags.json")
                if json_path.exists():
                    try:
                        json_path.unlink()
                        deleted += 1
                    except Exception:
                        pass
        self._notifs.show("Tags cleared", f"Removed tags from {deleted} file(s)", "info")

    def _on_annotations_export(self) -> None:
        default_path = self._annot_mgr.default_export_path()
        if default_path is None:
            default_path = Path("annotations.csv")
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export annotations",
            str(default_path),
            "CSV files (*.csv);;All files (*)",
        )
        if not path_str:
            return
        try:
            self._annot_mgr.export(Path(path_str))
            self._notifs.show("Annotations exported", Path(path_str).name, "info")
        except Exception as e:
            self._notifs.show("Export failed", str(e), "warning")

    def _on_annotations_clear_all(self) -> None:
        if not self._individuals:
            return
        dlg = _SelectiveClearDialog(
            FILE_TYPE_ORDER,
            FILE_TYPE_LABELS,
            include_notes=True,
            title="Clear annotations",
            warning_text=(
                "This will permanently delete the selected annotation categories "
                "for every individual in memory. This cannot be undone."
            ),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fts = dlg.selected_file_types
        clear_notes = dlg.clear_notes
        self._annot_mgr.clear_file_types(fts, clear_notes=clear_notes)
        self._init_annotation_indicators()
        if 0 <= self._current_idx < len(self._individuals):
            ind = self._individuals[self._current_idx]
            self._sidebar.set_annotations(self._annot_mgr.get_row(ind.oldname))
            if clear_notes:
                self._sidebar.force_clear_annotation_note()
        self._refresh_active_filter()
        count = len(fts) + (1 if clear_notes else 0)
        self._notifs.show(
            "Annotations cleared",
            f"Cleared {count} categor{'y' if count == 1 else 'ies'} for all individuals",
            "info",
        )

    def _annotation_summary(self, ind: Individual) -> str:
        """Worst-case annotation across all file types: Fail > Review > Pass > ''."""
        vals = set(self._annot_mgr.get_row(ind.oldname).values())
        if "Fail"   in vals: return "Fail"
        if "Review" in vals: return "Review"
        if "Pass"   in vals: return "Pass"
        return ""

    def _init_annotation_indicators(self) -> None:
        indicators = {i: self._annotation_summary(ind)
                      for i, ind in enumerate(self._individuals)}
        self._sidebar.set_all_annotation_indicators(indicators)

    def _on_filter_changed(self, mode: str) -> None:
        if not self._individuals:
            return
        matching = self._compute_filter_matches(mode)
        self._sidebar.apply_filter(mode, matching)
        if self._current_idx >= 0:
            self._start_preload(self._current_idx)

    def _compute_filter_matches(self, mode: str) -> list[int]:
        if mode == "All":
            return list(range(len(self._individuals)))
        return [
            i for i, ind in enumerate(self._individuals)
            if any(v == mode for v in self._annot_mgr.get_row(ind.oldname).values())
        ]

    def _refresh_active_filter(self) -> None:
        mode = self._sidebar.filter_mode
        if mode != "All" and self._individuals:
            matching = self._compute_filter_matches(mode)
            self._sidebar.apply_filter(mode, matching)

    def _on_annotation_changed(self, ft: str, value: str) -> None:
        if self._current_idx < 0:
            return
        ind = self._individuals[self._current_idx]
        self._annot_mgr.set(ind.oldname, ft, value)
        self._sidebar.set_annotation_indicator(self._current_idx, self._annotation_summary(ind))
        self._refresh_active_filter()

    def _on_annotation_note_changed(self, text: str) -> None:
        if self._current_idx < 0:
            return
        ind = self._individuals[self._current_idx]
        self._annot_mgr.set_note(ind.oldname, text)

    def _on_export_tags(self) -> None:
        if self._par_path is None or not self._individuals:
            return
        out_path = self._par_path.parent / (self._par_path.stem + "_tags_full.csv")
        fieldnames = ["oldname", "file_type", "tag_number", "x", "y", "z", "note", "color"]
        try:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for ind in self._individuals:
                    for ft in FILE_TYPE_ORDER:
                        vol_path = resolve_file(ind, ft)
                        if vol_path is None:
                            continue
                        json_path = vol_path.with_name(vol_path.stem + "_MV_tags.json")
                        if not json_path.exists():
                            continue
                        try:
                            raw = json.loads(json_path.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        for i, tag in enumerate(raw, start=1):
                            writer.writerow({
                                "oldname":    ind.oldname,
                                "file_type":  ft,
                                "tag_number": i,
                                "x":          tag.get("x", ""),
                                "y":          tag.get("y", ""),
                                "z":          tag.get("z", ""),
                                "note":       tag.get("note", ""),
                                "color":      tag.get("color", ""),
                            })
        except Exception as e:
            self._notifs.show("Export failed", str(e), "warning")
            return
        self._notifs.show("Tags exported", out_path.name, "info")

    def _panel_fts_for_annotations(self) -> list[str]:
        return [p.file_type for p in self._viewer.panels if p.file_type != COMPOSITE_TYPE]

    def _update_composite_channels(self):
        open_fts = [p.file_type for p in self._viewer.panels if p.file_type != COMPOSITE_TYPE]
        self._sidebar.update_composite_channels(open_fts)

    def _on_composite_updated(self, specs: list):
        """Live-update an existing composite panel — no loading, just rebuild from open panels."""
        comp_panel = next(
            (p for p in self._viewer.panels if p.file_type == COMPOSITE_TYPE), None
        )
        if comp_panel is None:
            return

        # Fetch cached data first — needed as fallback when a source panel was replaced
        # by the composite itself (its widget is gone but the numpy array lives in the cache).
        cached = self._overlay_cache.get(self._current_idx)

        channels = []
        for ft, color, opacity in specs:
            src = next((p for p in self._viewer.panels if p.file_type == ft), None)
            if src is not None and src.viewer.data is not None:
                lo, hi = src.viewer.display_range
                channels.append((src.viewer.data, lo, hi, color, opacity))
            elif cached is not None and ft in cached.data:
                lo, hi = cached.display_ranges.get(ft, (0.0, 1.0))
                channels.append((cached.data[ft], lo, hi, color, opacity))

        if not channels:
            return

        comp_panel.viewer.update_channels(channels)

        # Keep the overlay cache in sync so navigation away/back restores the latest settings
        if cached is not None:
            cached.file_types = [s[0] for s in specs]
            cached.colors     = [s[1] for s in specs]
            cached.opacities  = [s[2] for s in specs]
            for ft, color, opacity in specs:
                src = next((p for p in self._viewer.panels if p.file_type == ft), None)
                if src is not None and src.viewer.data is not None:
                    cached.data[ft]           = src.viewer.data
                    cached.display_ranges[ft] = src.viewer.display_range
                # else: data already in cached.data from creation — preserve it
