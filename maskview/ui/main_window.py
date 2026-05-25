import ctypes
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from ..files.loader import compute_display_range, load_volume
from ..files.resolver import (
    FILE_TYPE_LABELS, FILE_TYPE_ORDER, display_max, resolve_file,
)
from ..par.parser import Individual, parse_file
from .annotations import AnnotationManager
from .composite_panel import COMPOSITE_TYPE, OverlaySpec
from .multi_viewer import MultiViewer
from .notifications import NotifManager
from .sidebar import Sidebar

_DEFAULT_FILE_TYPES = ["original", "maskseg"]
_PRELOAD_AHEAD      = 2
_PRELOAD_BEHIND     = 1


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
            self.msleep(80)


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

        self._preload_cache: dict[int, dict[str, tuple]] = {}
        self._preloaders: list[_PreloaderThread] = []
        self._zombie_preloaders: list[_PreloaderThread] = []

        self._overlay_cache: dict[int, OverlaySpec] = {}
        self._composite_loader: _CompositeLoaderThread | None = None
        self._pending_spec: OverlaySpec | None = None
        self._pending_channels: list | None = None

        self._annot_mgr = AnnotationManager()

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("MaskView")
        self.setStyleSheet("QMainWindow { background: #111; }")

        self._sidebar = Sidebar()
        self._viewer  = MultiViewer()

        content = QWidget()
        content.setStyleSheet("background: #111;")
        hbox = QHBoxLayout(content)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(0)
        hbox.addWidget(self._sidebar)
        hbox.addWidget(self._viewer, stretch=1)
        self.setCentralWidget(content)

        self._notifs = NotifManager(content)

        self._sidebar.open_now()

        self._sidebar.par_selected.connect(self._on_par_selected)
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

        self._viewer.panel_closed.connect(self._on_panel_closed)
        self._viewer.composite_target_selected.connect(self._on_composite_target_selected)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._notifs.reposition()

    # ── Session ───────────────────────────────────────────────────────────────

    def _on_turbo_changed(self, step: int):
        self._turbo_step = step
        self._cancel_preloaders()
        self._preload_cache.clear()

    def _on_par_selected(self, path: Path):
        self._cancel_preloaders()
        self._preload_cache.clear()
        self._par_path = path
        self._sidebar.set_par_label(path)
        self._individuals = parse_file(path)
        self._annot_mgr.load(
            path,
            [ind.oldname for ind in self._individuals],
            FILE_TYPE_ORDER,
        )
        self._sidebar.load_individuals(self._individuals)
        if not self._individuals:
            return
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
        self._save_current_to_cache(next_idx=idx)
        self._current_idx = idx
        ind = self._individuals[idx]

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
                QTimer.singleShot(200, lambda: self._start_preload(idx))
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
        max_cached = _PRELOAD_AHEAD + _PRELOAD_BEHIND + 1
        if len(self._preload_cache) > max_cached:
            farthest = max(self._preload_cache, key=lambda k: abs(k - next_idx))
            del self._preload_cache[farthest]

    # ── Loading ───────────────────────────────────────────────────────────────

    def _start_load(self, ind: Individual, file_types: list[str]):
        self._cancel_loader()
        self._cancel_preloaders()
        self._viewer.clear()
        self._refresh_annotations([])
        self._update_composite_channels()
        self._launch_loader(ind, file_types)

    def _launch_loader(self, ind: Individual, file_types: list[str]):
        if not file_types:
            return
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
        if 0 <= self._current_idx < len(self._individuals):
            path = resolve_file(self._individuals[self._current_idx], ft)
            if path:
                self._viewer.set_panel_filename(ft, path)
        self._refresh_annotations(self._panel_fts_for_annotations())
        self._update_composite_channels()

    def _on_file_failed(self, ft: str, _msg: str):
        self._viewer.close_panel(ft)

    def _on_load_done(self):
        self._loading = False
        self._sidebar.set_controls_enabled(True)
        self._loader = None
        QTimer.singleShot(0, self._viewer.sync_all)
        QTimer.singleShot(200, lambda: self._start_preload(self._current_idx))
        self._check_dimension_mismatch()
        self._maybe_restore_overlay()

    # ── Pre-loading ───────────────────────────────────────────────────────────

    def _start_preload(self, current_idx: int):
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
        self._zombie_preloaders = [l for l in self._zombie_preloaders if l.isRunning()]
        for loader in self._preloaders:
            try:
                loader.chunk_ready.disconnect()
            except RuntimeError:
                pass
            loader.stop()
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
        self._cancel_preloaders()
        self._preload_cache.clear()
        ind = self._individuals[self._current_idx]
        self._cancel_loader()

        for panel in list(self._viewer.panels):
            if panel.file_type not in file_types and panel.file_type != COMPOSITE_TYPE:
                self._viewer.close_panel(panel.file_type)

        already_open = {p.file_type for p in self._viewer.panels}
        to_load = [ft for ft in file_types if ft not in already_open]
        if to_load:
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
                "Open volumes have different dimensions — window sync may not align "
                "correctly. A manual anchor-point sync feature is planned for this case.",
                "warning",
            )

    # ── Color composite ───────────────────────────────────────────────────────

    def _on_composite_requested(self, specs: list):
        if self._current_idx < 0:
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
        if fts and 0 <= self._current_idx < len(self._individuals):
            ind = self._individuals[self._current_idx]
            self._sidebar.set_annotations(self._annot_mgr.get_row(ind.oldname))

    def _on_annotation_changed(self, ft: str, value: str) -> None:
        if self._current_idx < 0:
            return
        ind = self._individuals[self._current_idx]
        self._annot_mgr.set(ind.oldname, ft, value)

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
