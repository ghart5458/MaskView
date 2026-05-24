"""
Dashboard verification: two panels side by side, fully synced.
Run from the project root:
    uv run python test_dashboard.py

Scroll, zoom, or pan in either panel — the other follows.
Uncheck "Sync windows" in the toolbar to break the link.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication

from maskview.par.parser import Individual
from maskview.files.resolver import display_max, resolve_file
from maskview.files.loader import compute_display_range, load_volume
from maskview.ui.multi_viewer import MultiViewer

_par = Path("reference/example_par_file.par")
_lines = [l for l in _par.read_text(encoding="utf-8").splitlines() if l.strip()]
_headers = [h.lstrip("$") for h in _lines[0].split("\t")]
_fields = _lines[1].split("\t")
_row = dict(zip(_headers, _fields))


def _norm(s: str) -> str:
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else s
    except ValueError:
        return s


IND = Individual(
    oldname=_fields[0].lstrip("#"),
    name=_row["name"],
    res=float(_row["res"]),
    dim1=int(_row["dim1"]),
    dim2=int(_row["dim2"]),
    dim3=int(_row["dim3"]),
    kc=_norm(_row["kc"]),
    kpoint=_norm(_row["kpoint"]),
    kout=_norm(_row["kout"]),
    kin=_norm(_row["kin"]),
    path=_row["path"],
    species=_row.get("species", ""),
    population=_row.get("population", ""),
    specimen=_row.get("specimen", ""),
    bone=_row.get("bone", ""),
    portion=_row.get("portion", ""),
    raw_fields=_row,
)


def _load(file_type: str):
    import time
    path = resolve_file(IND, file_type)
    if path is None:
        print(f"  {file_type}: not found — skipping")
        return None, 0.0, 1.0
    print(f"  {file_type}: {path.name}  (loading into RAM...)", end="", flush=True)
    t = time.time()
    data, _ = load_volume(path, use_memmap=False)
    print(f"  {time.time() - t:.1f}s")
    dmax = display_max(file_type)
    if dmax is None:
        lo, hi = compute_display_range(data)
    else:
        lo, hi = 0.0, float(dmax)
    return data, lo, hi


if __name__ == "__main__":
    print(f"Individual: {IND.oldname}\n")

    app = QApplication(sys.argv)
    mv = MultiViewer()

    for ft in ("original", "maskseg"):
        data, lo, hi = _load(ft)
        if data is not None:
            mv.add_panel(ft, data, lo, hi)

    mv.setWindowTitle(f"MaskView  —  {IND.oldname}")
    screen = app.primaryScreen().availableGeometry()
    w, h = int(screen.width() * 0.75), int(screen.height() * 0.75)
    mv.resize(w, h)
    mv.move(screen.x() + (screen.width() - w) // 2,
            screen.y() + (screen.height() - h) // 2)
    mv.show()
    sys.exit(app.exec())
