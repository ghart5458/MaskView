"""
Viewer verification: open a real MHD volume in the single-panel viewer.
Run from the project root:
    uv run python test_viewer.py

Change FILE_TYPE below to test different file types.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication

from maskview.par.parser import Individual
from maskview.files.resolver import FILE_TYPE_LABELS, display_max, resolve_file
from maskview.files.loader import compute_display_range, load_volume
from maskview.ui.viewer import VolumeViewer

FILE_TYPE = "original"  # change to: original, seg, rdn_seg, close, outer, inner, thick, trab, masksegin, masksegout

# Build a test Individual from the first data row in the example par.
# All rows in the example are #-prefixed (commented out), so we strip the #
# and use it directly for testing — this is safe since we only read files.
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


TEST_IND = Individual(
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

if __name__ == "__main__":
    mhd_path = resolve_file(TEST_IND, FILE_TYPE)
    if mhd_path is None:
        print(f"File not found for type '{FILE_TYPE}' — check that Z: is mounted.")
        sys.exit(1)

    print(f"Loading {FILE_TYPE_LABELS[FILE_TYPE]}: {mhd_path.name}")
    print("Using memmap — only the displayed slice is read from disk at a time.")

    data, meta = load_volume(mhd_path, use_memmap=True)
    print(f"Volume shape: {data.shape}   dtype: {data.dtype}")

    dmax = display_max(FILE_TYPE)
    if dmax is None:
        lo, hi = compute_display_range(data)
        print(f"Auto B&C: {lo:.1f} – {hi:.1f}")
    else:
        lo, hi = 0.0, float(dmax)
        print(f"Fixed range: {lo:.0f} – {hi:.0f}")

    app = QApplication(sys.argv)
    viewer = VolumeViewer()
    viewer.load(data, lo, hi)
    viewer.setWindowTitle(f"{FILE_TYPE_LABELS[FILE_TYPE]}  —  {TEST_IND.oldname}")
    viewer.resize(900, 900)
    viewer.show()
    sys.exit(app.exec())
