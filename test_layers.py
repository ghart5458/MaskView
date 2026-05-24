"""
Layer verification: PAR parser → file resolver → MHD loader.
Run from the project root after installing dependencies:
    pip install -e .
    python test_layers.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from maskview.par.parser import Individual, parse_par
from maskview.files.resolver import FILE_TYPE_LABELS, FILE_TYPE_ORDER, display_max, resolve_file
from maskview.files.loader import compute_display_range, load_volume

PAR_PATH = Path("reference/example_par_file.par")


def test_par_parser() -> Individual | None:
    print("TEST 1 · PAR Parser")
    print("-" * 60)

    active = parse_par(PAR_PATH)
    print(f"Active individuals (no # prefix): {len(active)}")

    lines = [l for l in PAR_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    headers = [h.lstrip("$") for h in lines[0].split("\t")]
    commented = [l.split("\t") for l in lines[1:] if l.split("\t")[0].startswith("#")]

    print(f"Commented-out rows (skipped):     {len(commented)}")
    print(f"Header columns detected:          {len(headers)}")

    if not commented:
        print("No rows available for resolver inspection.")
        return None

    fields = commented[0]
    row = dict(zip(headers, fields))

    print(f"\nFirst commented row (parsed for inspection only — not loaded as active):")
    print(f"  oldname  : {fields[0]}")
    print(f"  name     : {row.get('name', '?')}")
    print(f"  res      : {row.get('res', '?')} mm/voxel")
    print(f"  dims     : {row.get('dim1')} × {row.get('dim2')} × {row.get('dim3')} voxels")
    print(f"  kc / kpoint / kout / kin : {row.get('kc')} / {row.get('kpoint')} / {row.get('kout')} / {row.get('kin')}")
    print(f"  path     : {row.get('path', '?')}")

    def norm(s: str) -> str:
        try:
            f = float(s)
            return str(int(f)) if f == int(f) else s
        except ValueError:
            return s

    return Individual(
        oldname=fields[0].lstrip("#"),
        name=row.get("name", ""),
        res=float(row.get("res", 0)),
        dim1=int(row.get("dim1", 0)),
        dim2=int(row.get("dim2", 0)),
        dim3=int(row.get("dim3", 0)),
        kc=norm(row.get("kc", "0")),
        kpoint=norm(row.get("kpoint", "0")),
        kout=norm(row.get("kout", "0")),
        kin=norm(row.get("kin", "0")),
        path=row.get("path", ""),
        species=row.get("species", ""),
        population=row.get("population", ""),
        specimen=row.get("specimen", ""),
        bone=row.get("bone", ""),
        portion=row.get("portion", ""),
        raw_fields=row,
    )


def test_resolver(ind: Individual) -> None:
    print("\nTEST 2 · File Resolver")
    print("-" * 60)
    print(f"Individual : {ind.oldname}")
    print(f"Base path  : {ind.base_path}")
    print()

    for ft in FILE_TYPE_ORDER:
        path = resolve_file(ind, ft)
        dmax = display_max(ft)
        scale = "auto B&C" if dmax is None else f"0–{dmax} fixed"
        label = FILE_TYPE_LABELS[ft]
        name = path.name if path else "—"
        if path is None:
            status = "no pattern match"
        elif path.exists():
            status = "FOUND ON DISK"
        else:
            status = "path built, not on disk"
        print(f"  {label:<20}  {scale:<14}  {name:<48}  {status}")


def test_loader() -> None:
    print("\nTEST 3 · MHD Loader (synthetic data — no real files needed)")
    print("-" * 60)

    # Write a tiny known volume, load it back, confirm round-trip is exact.
    shape = (10, 8, 6)  # z=10, y=8, x=6  →  DimSize = 6 8 10 in MHD convention
    original = np.arange(shape[0] * shape[1] * shape[2], dtype=np.uint8).reshape(shape)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        original.tofile(tmp / "vol.raw")
        (tmp / "vol.mhd").write_text(
            "ObjectType = Image\n"
            "NDims = 3\n"
            f"DimSize = {shape[2]} {shape[1]} {shape[0]}\n"
            "ElementType = MET_UCHAR\n"
            "ElementDataFile = vol.raw\n",
            encoding="utf-8",
        )

        data, meta = load_volume(tmp / "vol.mhd")
        print(f"Written shape   : {original.shape}  (z, y, x)")
        print(f"Loaded shape    : {data.shape}")
        print(f"Round-trip      : {'PASS' if np.array_equal(data, original) else 'FAIL ← mismatch'}")

        data_mm, _ = load_volume(tmp / "vol.mhd", use_memmap=True)
        print(f"Memmap mode     : {'PASS' if np.array_equal(data_mm, original) else 'FAIL ← mismatch'}")

        lo, hi = compute_display_range(data, sample_slices=5)
        print(f"\nAuto B&C range  : {lo:.1f} – {hi:.1f}")
        print(f"  (actual data min/max: {int(data.min())} – {int(data.max())}, clipping 0.35% each end)")


if __name__ == "__main__":
    ind = test_par_parser()
    if ind:
        test_resolver(ind)
    test_loader()
    print("\nAll tests complete.")
