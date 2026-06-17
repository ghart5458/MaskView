import csv
from pathlib import Path

# Maps sidebar button text to the value stored in the CSV, and back.
BTN_TO_VALUE = {"Pass": "Pass", "Rev": "Review", "Fail": "Fail"}
VALUE_TO_BTN = {v: k for k, v in BTN_TO_VALUE.items()}


class AnnotationManager:
    """Per-individual, per-file-type annotations held in memory.

    Annotations are loaded from a CSV on PAR open (if one exists) and written
    only when the user explicitly calls export().  This means the CSV is never
    silently modified during a session.

    CSV layout: one row per individual, columns are 'oldname', one column per
    file type (e.g. 'original', 'maskseg'), then 'notes'.
    Values for file-type columns: 'Pass', 'Review', 'Fail', or blank.
    """

    def __init__(self):
        self._path: Path | None = None
        self._columns: list[str] = []
        self._data: dict[str, dict[str, str]] = {}  # oldname → {ft: value}
        self._notes: dict[str, str] = {}             # oldname → note text
        self._all_entries: list[tuple[str, bool]] = []  # (oldname, is_active) in PAR order

    def load(self, par_path: Path, oldnames: list[str], file_types: list[str],
             all_entries: list[tuple[str, bool]] | None = None) -> None:
        """Load or initialise annotations from the default sidecar CSV.

        all_entries is the full (oldname, is_active) list from parse_file_entries,
        including commented-out individuals.  When omitted only active rows are tracked.
        """
        self._path = par_path.parent / (par_path.stem + "_annotations.csv")
        self._columns = list(file_types)
        self._all_entries = all_entries if all_entries is not None else [
            (name, True) for name in oldnames
        ]
        if self._path.exists():
            self._read(oldnames)
        else:
            self._data  = {name: {} for name in oldnames}
            self._notes = {name: "" for name in oldnames}

    def default_export_path(self) -> Path | None:
        return self._path

    def set(self, oldname: str, file_type: str, value: str) -> None:
        """Record an annotation in memory. Pass '' to clear."""
        if oldname not in self._data:
            self._data[oldname] = {}
        if value:
            self._data[oldname][file_type] = value
        else:
            self._data[oldname].pop(file_type, None)

    def set_note(self, oldname: str, text: str) -> None:
        """Store a free-text note for one individual in memory."""
        self._notes[oldname] = text

    def get_row(self, oldname: str) -> dict[str, str]:
        """Return all annotations for one individual as {file_type: value}."""
        return dict(self._data.get(oldname, {}))

    def get_note(self, oldname: str) -> str:
        """Return the saved note text for one individual (empty string if none)."""
        return self._notes.get(oldname, "")

    def clear_file_types(self, file_types: list[str], clear_notes: bool = False) -> None:
        """Clear selected file-type annotations (and optionally notes) for all individuals."""
        for annots in self._data.values():
            for ft in file_types:
                annots.pop(ft, None)
        if clear_notes:
            for oldname in self._notes:
                self._notes[oldname] = ""

    def export(self, path: Path) -> None:
        """Write all annotations to *path* in PAR order.  Raises on I/O error.

        Active individuals are written with their annotations.  Commented-out
        individuals are written with '#oldname' and blank annotation columns,
        mirroring the structure of the source PAR file.
        """
        fieldnames = ["oldname"] + self._columns + ["notes"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for oldname, is_active in self._all_entries:
                if is_active:
                    row = {"oldname": oldname, "notes": self._notes.get(oldname, "")}
                    row.update({ft: self._data.get(oldname, {}).get(ft, "")
                                for ft in self._columns})
                else:
                    row = {"oldname": f"#{oldname}", "notes": ""}
                    row.update({ft: "" for ft in self._columns})
                writer.writerow(row)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read(self, oldnames: list[str]) -> None:
        self._data  = {}
        self._notes = {}
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
                        self._notes[name] = row.get("notes", "")
        except Exception:
            self._data  = {}
            self._notes = {}
        for name in oldnames:
            if name not in self._data:
                self._data[name]  = {}
            if name not in self._notes:
                self._notes[name] = ""
