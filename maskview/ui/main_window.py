from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from ..files.loader import compute_display_range, load_volume
from ..files.resolver import (
    FILE_TYPE_LABELS, FILE_TYPE_ORDER, display_max, resolve_file,
)
from ..par.parser import Individual, parse_file
from .multi_viewer import MultiViewer
from .sidebar import Sidebar

_DEFAULT_FILE_TYPES = ["original", "maskseg"]
_PRELOAD_AHEAD      = 2   # individuals to pre-load after current
_PRELOAD_BEHIND     = 1   # individuals to pre-load before current


# ── Background loader ─────────────────────────────────────────────────────────

class _LoaderThread(QThread):
    file_starting = pyqtSignal(str)
    file_loaded   = pyqtSignal(str, object, float, float)
    file_failed   = pyqtSignal(str, str)
    all_done      = pyqtSignal()

    def __init__(self, ind: Individual, file_types: list[str],
                 turbo_step: int = 1, parent=None):
        super().__init__(parent)
        self._ind = ind
        self._file_types = file_types
        self._turbo_step = turbo_step
        self._stop = False

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
                if dmax is not None:
                    lo, hi = 0.0, float(dmax)
                else:
                    lo, hi = compute_display_range(data)
                self.file_loaded.emit(ft, data, lo, hi)
            except Exception as exc:
                self.file_failed.emit(ft, str(exc))
        self.all_done.emit()


# ── Background pre-loader ─────────────────────────────────────────────────────

class _PreloaderThread(QThread):
    """Quietly pre-loads an individual's files into a cache. Yields between files
    so it doesn't compete with UI rendering."""

    chunk_ready = pyqtSignal(int, str, object, float, float)  # idx, ft, data, lo, hi

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
                if dmax is not None:
                    lo, hi = 0.0, float(dmax)
                else:
                    lo, hi = compute_display_range(data)
                self.chunk_ready.emit(self._idx, ft, data, lo, hi)
            except Exception:
                pass
            self.msleep(80)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._session_types: list[str] = list(_DEFAULT_FILE_TYPES)
        self._par_path: Path | None = None
        self._loading = False
        self._loader: _LoaderThread | None = None
        self._turbo_step = 1

        # Pre-load cache: {individual_idx: {file_type: (data, lo, hi)}}
        self._preload_cache: dict[int, dict[str, tuple]] = {}
        self._preloaders: list[_PreloaderThread] = []
        self._zombie_preloaders: list[_PreloaderThread] = []  # stopped but still running

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("MaskView")
        self.setStyleSheet("QMainWindow { background: #111; }")

        self._sidebar = Sidebar()
        self._viewer = MultiViewer()

        content = QWidget()
        hbox = QHBoxLayout(content)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(self._sidebar)
        hbox.addWidget(self._viewer, stretch=1)

        self._current_label = QLabel("No file loaded")
        self._current_label.setFixedHeight(22)
        self._current_label.setStyleSheet(
            "color: #999; font-size: 11px; padding: 0 10px;"
            " background: #1a1a1a; border-top: 1px solid #2d2d2d;"
        )

        container = QWidget()
        container.setStyleSheet("background: #111;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(content, stretch=1)
        vbox.addWidget(self._current_label)
        self.setCentralWidget(container)

        self._sidebar.open_now()  # start open — no PAR loaded yet

        # Wire sidebar signals
        self._sidebar.par_selected.connect(self._on_par_selected)
        self._sidebar.files_applied.connect(self._on_files_applied)
        self._sidebar.orientation_changed.connect(self._viewer.set_orientation)
        self._sidebar.layout_changed.connect(self._viewer.set_layout_mode)
        self._sidebar.sync_toggled.connect(self._viewer.set_sync)
        self._sidebar.turbo_toggled.connect(self._on_turbo_toggled)
        self._sidebar.individual_selected.connect(self._on_individual_selected)

        # Viewer signals
        self._viewer.panel_closed.connect(self._on_panel_closed)

    # ── Session ───────────────────────────────────────────────────────────────

    def _on_turbo_toggled(self, enabled: bool):
        self._turbo_step = 4 if enabled else 1
        self._cancel_preloaders()
        self._preload_cache.clear()

    def _on_par_selected(self, path: Path):
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._par_path = path
        self._sidebar.set_par_label(path)
        self._individuals = parse_file(path)
        self._sidebar.load_individuals(self._individuals)
        if not self._individuals:
            return
        # Show availability for individual 0 but don't start loading —
        # let the user confirm file selection via Load first.
        self._current_idx = 0
        ind = self._individuals[0]
        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        default_checked = {ft for ft in self._session_types if available.get(ft)}
        self._sidebar.update_file_availability(available, default_checked)
        self._sidebar.select_individual_silent(0)
        self._update_current_label()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_individual_selected(self, idx: int):
        if idx == self._current_idx and self._loading:
            return
        self._save_current_to_cache(next_idx=idx)
        self._current_idx = idx
        self._update_current_label()
        ind = self._individuals[idx]

        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        to_load = [ft for ft in self._session_types if available.get(ft)]
        self._sidebar.update_file_availability(available, set(to_load))

        cached = self._preload_cache.pop(idx, {})
        if cached:
            # Some or all files already loaded in the background — use them immediately
            self._cancel_loader()
            self._viewer.clear()
            self._sidebar.update_annotations([])

            for ft in to_load:
                if ft in cached:
                    data, lo, hi = cached[ft]
                    self._viewer.add_panel(ft, data, lo, hi)
                    self._sidebar.set_file_loaded(ft, True)

            missing = [ft for ft in to_load if ft not in cached]
            if missing:
                self._launch_loader(ind, missing)
            else:
                self._sidebar.set_controls_enabled(True)
                QTimer.singleShot(0, self._viewer.sync_all)
                QTimer.singleShot(200, lambda: self._start_preload(idx))

            self._sidebar.update_annotations([p.file_type for p in self._viewer.panels])
        else:
            self._start_load(ind, to_load)

    def _save_current_to_cache(self, next_idx: int):
        """Snapshot current viewer data into the preload cache before navigating away."""
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
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._preload_cache) > max_cached:
            farthest = max(self._preload_cache, key=lambda k: abs(k - next_idx))
            del self._preload_cache[farthest]

    def _update_current_label(self):
        if self._current_idx < 0 or not self._individuals:
            self._current_label.setText("No file loaded")
            return
        ind = self._individuals[self._current_idx]
        self._current_label.setText(f"Current:  {ind.oldname}")

    # ── Loading ───────────────────────────────────────────────────────────────

    def _start_load(self, ind: Individual, file_types: list[str]):
        self._cancel_loader()
        self._cancel_preloaders()
        self._viewer.clear()
        self._sidebar.update_annotations([])
        self._launch_loader(ind, file_types)

    def _launch_loader(self, ind: Individual, file_types: list[str]):
        if not file_types:
            return

        # Add placeholder panels immediately so the correct layout appears before data arrives
        for ft in file_types:
            self._viewer.add_empty_panel(ft)

        self._loading = True
        self._sidebar.set_controls_enabled(False)

        self._loader = _LoaderThread(ind, file_types, self._turbo_step)
        self._loader.file_loaded.connect(self._on_file_loaded)
        self._loader.file_failed.connect(self._on_file_failed)
        self._loader.all_done.connect(self._on_load_done)
        self._loader.start()

    def _cancel_loader(self):
        if self._loader is not None:
            for sig in (self._loader.file_loaded,
                        self._loader.file_failed, self._loader.all_done):
                sig.disconnect()
            self._loader.stop()
            self._loader = None

    def _on_file_loaded(self, ft: str, data: object, lo: float, hi: float):
        self._viewer.fill_panel(ft, data, lo, hi)
        self._sidebar.set_file_loaded(ft, True)
        self._sidebar.update_annotations([p.file_type for p in self._viewer.panels])

    def _on_file_failed(self, ft: str, _msg: str):
        self._viewer.close_panel(ft)

    def _on_load_done(self):
        self._loading = False
        self._sidebar.set_controls_enabled(True)
        self._loader = None
        QTimer.singleShot(0, self._viewer.sync_all)
        QTimer.singleShot(200, lambda: self._start_preload(self._current_idx))

    # ── Pre-loading ───────────────────────────────────────────────────────────

    def _start_preload(self, current_idx: int):
        """Pre-load adjacent individuals into cache (forward and backward)."""
        self._cancel_preloaders()
        n = len(self._individuals)
        forward  = range(current_idx + 1, min(current_idx + _PRELOAD_AHEAD + 1, n))
        backward = range(max(0, current_idx - _PRELOAD_BEHIND), current_idx)
        for i in list(forward) + list(backward):
            if i in self._preload_cache:
                continue
            ind = self._individuals[i]
            to_load = [ft for ft in self._session_types
                       if resolve_file(ind, ft) is not None]
            if not to_load:
                continue
            loader = _PreloaderThread(i, ind, to_load, self._turbo_step)
            loader.chunk_ready.connect(self._on_preload_chunk)
            loader.start()
            self._preloaders.append(loader)

    def _cancel_preloaders(self):
        # Reap previously stopped threads that have now finished
        self._zombie_preloaders = [l for l in self._zombie_preloaders if l.isRunning()]
        for loader in self._preloaders:
            try:
                loader.chunk_ready.disconnect()
            except RuntimeError:
                pass
            loader.stop()
        # Move to zombie list — keeps Python reference alive until thread exits
        self._zombie_preloaders.extend(self._preloaders)
        self._preloaders.clear()

    def _on_preload_chunk(self, idx: int, ft: str, data: object, lo: float, hi: float):
        if idx not in self._preload_cache:
            self._preload_cache[idx] = {}
        self._preload_cache[idx][ft] = (data, lo, hi)
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._preload_cache) > max_cached:
            farthest = max(self._preload_cache, key=lambda k: abs(k - self._current_idx))
            del self._preload_cache[farthest]

    # ── File selection ────────────────────────────────────────────────────────

    def _on_files_applied(self, file_types: list[str]):
        if self._current_idx < 0:
            return
        self._session_types = list(file_types)
        # Session types changed — cached data was loaded with the old type list
        self._cancel_preloaders()
        self._preload_cache.clear()

        ind = self._individuals[self._current_idx]
        self._cancel_loader()

        # Close panels not in the new selection
        for panel in list(self._viewer.panels):
            if panel.file_type not in file_types:
                self._viewer.close_panel(panel.file_type)

        # Load only what isn't already open
        already_open = {p.file_type for p in self._viewer.panels}
        to_load = [ft for ft in file_types if ft not in already_open]
        if to_load:
            self._launch_loader(ind, to_load)

        self._sidebar.update_annotations([p.file_type for p in self._viewer.panels])

    def _on_panel_closed(self, ft: str):
        self._sidebar.set_file_loaded(ft, False)
        self._sidebar.update_annotations([p.file_type for p in self._viewer.panels])
