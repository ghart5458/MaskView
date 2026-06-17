import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Individual:
    oldname: str
    name: str
    res: float
    dim1: int
    dim2: int
    dim3: int
    kc: str
    kpoint: str
    kout: str
    kin: str
    path: str
    species: str
    population: str
    specimen: str
    bone: str
    portion: str
    raw_fields: dict = field(default_factory=dict)

    @property
    def base_path(self) -> Path:
        return Path(self.path) / self.oldname


def _normalize_param(s: str) -> str:
    # "3.0" → "3" so integer params match filenames exactly
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else s
    except ValueError:
        return s


def _make_individual(row: dict) -> Individual:
    return Individual(
        oldname=row['oldname'],
        name=row.get('name', ''),
        res=float(row.get('res', 0)),
        dim1=int(row.get('dim1', 0)),
        dim2=int(row.get('dim2', 0)),
        dim3=int(row.get('dim3', 0)),
        kc=_normalize_param(row.get('kc', '0')),
        kpoint=_normalize_param(row.get('kpoint', '0')),
        kout=_normalize_param(row.get('kout', '0')),
        kin=_normalize_param(row.get('kin', '0')),
        path=row.get('path', ''),
        species=row.get('species', ''),
        population=row.get('population', ''),
        specimen=row.get('specimen', ''),
        bone=row.get('bone', ''),
        portion=row.get('portion', ''),
        raw_fields=row,
    )


def _detect_delim(header: str) -> str:
    tc, sc, cc = header.count('\t'), header.count(';'), header.count(',')
    if tc >= sc and tc >= cc:
        return '\t'
    return ';' if sc >= cc else ','


def _parse_rows(text: str) -> list[Individual]:
    """Parse delimiter-separated tabular text into a list of Individuals.

    Uses csv.reader so quoted fields containing delimiters are handled correctly.
    Leading $ is always stripped from column headers (harmless for plain CSVs).
    """
    lines = text.splitlines()
    header_line = next((l for l in lines if l.strip()), None)
    if header_line is None:
        return []

    delim = _detect_delim(header_line)
    reader = csv.reader(lines, delimiter=delim)

    raw_headers = next(reader, None)
    if not raw_headers:
        return []
    headers = [h.lstrip('$').strip() for h in raw_headers]

    individuals = []
    for row_fields in reader:
        if not row_fields or not any(f.strip() for f in row_fields):
            continue
        if row_fields[0].lstrip().startswith('#'):
            continue
        row = dict(zip(headers, [f.strip() for f in row_fields]))
        try:
            individuals.append(_make_individual(row))
        except (ValueError, KeyError):
            continue

    return individuals


def parse_par(path: str | Path) -> list[Individual]:
    return _parse_rows(Path(path).read_text(encoding='utf-8-sig'))


def parse_csv(path: str | Path) -> list[Individual]:
    return _parse_rows(Path(path).read_text(encoding='utf-8-sig'))


def parse_file(path: str | Path) -> list[Individual]:
    path = Path(path)
    if path.suffix.lower() == '.csv':
        return parse_csv(path)
    return parse_par(path)


def parse_file_entries(path: str | Path) -> list[tuple[str, bool]]:
    """Return (oldname, is_active) for every data row in PAR order.

    Commented-out rows (first field starts with #) are included with
    is_active=False so the export CSV can mirror the PAR exactly.
    """
    text = Path(path).read_text(encoding='utf-8-sig')
    lines = text.splitlines()
    header_line = next((l for l in lines if l.strip()), None)
    if header_line is None:
        return []
    delim = _detect_delim(header_line)
    reader = csv.reader(lines, delimiter=delim)
    raw_headers = next(reader, None)
    if not raw_headers:
        return []
    headers = [h.lstrip('$').strip() for h in raw_headers]
    try:
        oldname_col = headers.index('oldname')
    except ValueError:
        return []

    entries: list[tuple[str, bool]] = []
    for row_fields in reader:
        if not row_fields or not any(f.strip() for f in row_fields):
            continue
        first = row_fields[0].lstrip()
        is_commented = first.startswith('#')
        row_fields = [f.strip() for f in row_fields]
        if is_commented:
            row_fields[0] = first.lstrip('#').strip()
        if oldname_col < len(row_fields):
            oldname = row_fields[oldname_col]
            if oldname:
                entries.append((oldname, not is_commented))
    return entries
