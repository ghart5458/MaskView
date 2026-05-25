import csv
from pathlib import Path

# Maps sidebar button text to the value stored in the CSV, and back.
BTN_TO_VALUE = {"Pass": "Pass", "Rev": "Review", "Fail": "Fail"}
VALUE_TO_BTN = {v: k for k, v in BTN_TO_VALUE.items()}


class AnnotationManager:
    """Per-individual, per-file-type annotations persisted to a CSV alongside the PAR file.

    CSV layout: one row per individual, columns are 'oldname' then one column
    per file type (e.g. 'original', 'maskseg'). Values: 'Pass', 'Review',
    'Fail', or blank. Written immediately on every change.
    """

    def __init__(self):
        self._path: Path | None = None
        self._columns: list[str] = []
        self._data: dict[str, dict[str, str]] = {}  # oldname → {ft: value}

    def load(self, par_path: Path, oldnames: list[str], file_types: list[str]) -> None:
        """Load or create the annotations CSV. Called when a PAR file is opened."""
        self._path = par_path.parent / (par_path.stem + "_annotations.csv")
        self._columns = list(file_types)
        if self._path.exists():
            self._read(oldnames)
        else:
            self._data = {name: {} for name in oldnames}

    def set(self, oldname: str, file_type: str, value: str) -> None:
        """Record an annotation and persist to disk. Pass '' to clear."""
        if oldname not in self._data:
            self._data[oldname] = {}
        if value:
            self._data[oldname][file_type] = value
        else:
            self._data[oldname].pop(file_type, None)
        self._write()

    def get_row(self, oldname: str) -> dict[str, str]:
        """Return all annotations for one individual as {file_type: value}."""
        return dict(self._data.get(oldname, {}))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read(self, oldnames: list[str]) -> None:
        self._data = {}
        try:
            with open(self._path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = row.get("oldname", "")
                    if name:
                        self._data[name] = {
                            ft: row[ft]
                            for ft in self._columns
                            if ft in row and row[ft]
                        }
        except Exception:
            self._data = {}
        # Ensure every individual from the PAR file has a row
        for name in oldnames:
            if name not in self._data:
                self._data[name] = {}

    def _write(self) -> None:
        if self._path is None:
            return
        try:
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["oldname"] + self._columns, extrasaction="ignore"
                )
                writer.writeheader()
                for oldname, annots in self._data.items():
                    row = {"oldname": oldname}
                    row.update({ft: annots.get(ft, "") for ft in self._columns})
                    writer.writerow(row)
        except Exception:
            pass
