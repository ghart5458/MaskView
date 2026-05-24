from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QProgressBar, QWidget

from ..files.loader import compute_display_range, load_volume
from ..files.resolver import (
    FILE_TYPE_LABELS, FILE_TYPE_ORDER, display_max, resolve_file,
)
from ..par.parser import Individual, parse_par
from .multi_viewer import MultiViewer
from .sidebar import Sidebar

_DEFAULT_FILE_TYPES = ["original", "maskseg"]


# ── Background loader ─────────────────────────────────────────────────────────

class _LoaderThread(QThread):
    file_starting = pyqtSignal(str)
    file_loaded   = pyqtSignal(str, object, float, float)
    file_failed   = pyqtSignal(str, str)
    all_done      = pyqtSignal()

    def __init__(self, ind: Individual, file_types: list[str], parent=None):
        super().__init__(parent)
        self._ind = ind
        self._file_types = file_types
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
                data, _ = load_volume(path, use_memmap=False)
                dmax = display_max(ft)
                lo, hi = (0.0, float(dmax)) if dmax is not None else compute_display_range(data)
                self.file_loaded.emit(ft, data, lo, hi)
            except Exception as exc:
                self.file_failed.emit(ft, str(exc))
        self.all_done.emit()


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._individuals: list[Individual] = []
        self._current_idx = -1
        self._session_types: list[str] = list(_DEFAULT_FILE_TYPES)
        self._par_path: Path | None = None
        self._loaded_count = 0
        self._total_count = 0
        self._loading = False
        self._loading_ind = ""
        self._loader: _LoaderThread | None = None
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("MaskView")
        self.setStyleSheet("QMainWindow { background: #111; }")

        self._sidebar = Sidebar()
        self._viewer = MultiViewer()

        container = QWidget()
        container.setStyleSheet("background: #111;")
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(self._sidebar)
        hbox.addWidget(self._viewer, stretch=1)
        self.setCentralWidget(container)

        self._sidebar.open_now()  # start open — no PAR loaded yet

        # Wire sidebar signals
        self._sidebar.par_selected.connect(self._on_par_selected)
        self._sidebar.files_applied.connect(self._on_files_applied)
        self._sidebar.orientation_changed.connect(self._viewer.set_orientation)
        self._sidebar.layout_changed.connect(self._viewer.set_layout_mode)
        self._sidebar.sync_toggled.connect(self._viewer.set_sync)
        self._sidebar.individual_selected.connect(self._on_individual_selected)

        # Viewer signals
        self._viewer.panel_closed.connect(self._on_panel_closed)

        # Status bar (non-modal progress)
        sb = self.statusBar()
        sb.setStyleSheet("QStatusBar { background: #111; border-top: 1px solid #222; }")
        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #777; font-size: 11px; padding: 0 8px;")
        self._status_prog = QProgressBar()
        self._status_prog.setFixedWidth(160)
        self._status_prog.setFixedHeight(10)
        self._status_prog.setTextVisible(False)
        self._status_prog.setStyleSheet("""
            QProgressBar { background: #222; border: 1px solid #333; border-radius: 2px; }
            QProgressBar::chunk { background: #2ce67f; border-radius: 2px; }
        """)
        sb.addPermanentWidget(self._status_label)
        sb.addPermanentWidget(self._status_prog)
        sb.hide()

    # ── Session ───────────────────────────────────────────────────────────────

    def _on_par_selected(self, path: Path):
        self._par_path = path
        self._sidebar.set_par_label(path)
        self._individuals = parse_par(path)
        self._sidebar.load_individuals(self._individuals)
        if not self._individuals:
            return
        # Show availability for individual 0 but don't start loading —
        # let the user confirm file selection via Update first.
        self._current_idx = 0
        ind = self._individuals[0]
        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        default_checked = {ft for ft in self._session_types if available.get(ft)}
        self._sidebar.update_file_availability(available, default_checked)
        self._sidebar.select_individual_silent(0)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _on_individual_selected(self, idx: int):
        if idx == self._current_idx and self._loading:
            return
        self._current_idx = idx
        ind = self._individuals[idx]

        available = {ft: (resolve_file(ind, ft) is not None) for ft in FILE_TYPE_ORDER}
        to_load = [ft for ft in self._session_types if available.get(ft)]
        self._sidebar.update_file_availability(available, set(to_load))
        self._start_load(ind, to_load)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _start_load(self, ind: Individual, file_types: list[str]):
        self._cancel_loader()
        self._viewer.clear()
        self._sidebar.update_annotations([])
        self._launch_loader(ind, file_types)

    def _launch_loader(self, ind: Individual, file_types: list[str]):
        if not file_types:
            return
        self._loading = True
        self._loading_ind = ind.oldname
        self._loaded_count = 0
        self._total_count = len(file_types)
        self._sidebar.set_controls_enabled(False)

        self._status_label.setText(ind.oldname)
        self._status_prog.setRange(0, 0)  # marquee from the start
        self.statusBar().show()

        self._loader = _LoaderThread(ind, file_types)
        self._loader.file_starting.connect(self._on_file_starting)
        self._loader.file_loaded.connect(self._on_file_loaded)
        self._loader.file_failed.connect(self._on_file_failed)
        self._loader.all_done.connect(self._on_load_done)
        self._loader.start()

    def _cancel_loader(self):
        if self._loader is not None:
            for sig in (self._loader.file_starting, self._loader.file_loaded,
                        self._loader.file_failed, self._loader.all_done):
                sig.disconnect()
            self._loader.stop()
            self._loader = None
        self.statusBar().hide()

    def _on_file_starting(self, ft: str):
        n, done = self._total_count, self._loaded_count
        self._status_label.setText(
            f"{self._loading_ind}  ·  {FILE_TYPE_LABELS.get(ft, ft)}…  "
            f"({done + 1}/{n})"
        )

    def _on_file_loaded(self, ft: str, data: object, lo: float, hi: float):
        self._viewer.add_panel(ft, data, lo, hi)
        self._sidebar.set_file_loaded(ft, True)
        self._loaded_count += 1
        self._status_label.setText(
            f"{self._loading_ind}  ·  {self._loaded_count}/{self._total_count} loaded"
        )
        self._sidebar.update_annotations([p.file_type for p in self._viewer.panels])

    def _on_file_failed(self, _ft: str, _msg: str):
        self._loaded_count += 1
        self._status_label.setText(
            f"{self._loading_ind}  ·  {self._loaded_count}/{self._total_count} loaded"
        )

    def _on_load_done(self):
        self.statusBar().hide()
        self._loading = False
        self._sidebar.set_controls_enabled(True)
        self._loader = None
        # Defer sync so it runs after all pending fit_to_view timers fire
        QTimer.singleShot(0, self._viewer.sync_all)

    # ── File selection ────────────────────────────────────────────────────────

    def _on_files_applied(self, file_types: list[str]):
        if self._current_idx < 0:
            return
        self._session_types = list(file_types)
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

