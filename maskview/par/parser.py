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


def parse_par(path: str | Path) -> list[Individual]:
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    lines = [l for l in lines if l.strip()]
    if not lines:
        return []

    headers = [h.lstrip('$') for h in lines[0].split('\t')]

    individuals = []
    for line in lines[1:]:
        fields = line.split('\t')
        if not fields or fields[0].startswith('#'):
            continue

        row = dict(zip(headers, fields))

        try:
            ind = Individual(
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
            individuals.append(ind)
        except (ValueError, KeyError):
            continue

    return individuals
