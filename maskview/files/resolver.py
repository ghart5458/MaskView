from pathlib import Path
from ..par.parser import Individual


FILE_TYPE_LABELS: dict[str, str] = {
    'original':   'Original CT',
    'seg':        'Binary Seg',
    'rdn_seg':    'RDN Seg',
    'close':      'Close',
    'outer':      'OuterMask',
    'inner':      'InnerMask',
    'thick':      'ThickMask',
    'trab':       'Trab',
    'masksegin':  'MaskSegIn',
    'masksegout': 'MaskSegOut',
    'maskseg':    'MaskSeg',
}

FILE_TYPE_ORDER: list[str] = [
    'original', 'seg', 'rdn_seg', 'close', 'outer', 'inner', 'thick', 'trab',
    'masksegin', 'masksegout', 'maskseg',
]

# (subfolder, filename_patterns, declared_display_max)
# display_max=None → auto percentile B&C (original CT only)
_FILE_SPECS: dict[str, tuple[str, list[str], int | None]] = {
    'original':   ('00_Original', ['{oldname}_reoriented_cropped.mhd',
                                   '{oldname}_cropped.mhd',
                                   '{oldname}_reoriented.mhd',
                                   '{oldname}.mhd'],                                                           None),
    'seg':        ('01_Seg',      ['{name}_seg_cropped_capped.mhd',
                                   '{name}_seg_capped.mhd',
                                   '{name}_seg_cropped.mhd',
                                   '{name}_seg.mhd'],                                                          1),
    'rdn_seg':    ('01_Seg',      ['{oldname}_RDN_seg.mhd'],                                                   1),
    'close':      ('02_Close',    ['{name}_Close_kc{kc}_{kpoint}.mhd'],                                        1),
    'outer':      ('03_OuterMask',['{name}_OuterMask_kc{kc}_{kpoint}_kout{kout}.mhd',
                                   '{name}_OuterMask.mhd'],                                                     1),
    'inner':      ('04_InnerMask',['{name}_InnerMask_kc{kc}_{kpoint}_kin{kin}.mhd',
                                   '{name}_InnerMask.mhd'],                                                     1),
    'thick':      ('05_ThickMask',['{name}_ThickMask_kc{kc}_{kpoint}_kout{kout}_kin{kin}.mhd',
                                   '{name}_ThickMask.mhd'],                                                     1),
    'trab':       ('06_Trab',     ['{name}_Trab_kc{kc}_{kpoint}_kout{kout}_kin{kin}.mhd',
                                   '{name}_Trab.mhd'],                                                          1),
    'masksegin':  ('07_MaskSeg',  ['{name}_MaskSegIn.mhd'],                                                    2),
    'masksegout': ('07_MaskSeg',  ['{name}_MaskSegOut.mhd'],                                                   2),
    'maskseg':    ('07_MaskSeg',  ['{name}_MaskSeg.mhd'],                                                      3),
}


def resolve_file(ind: Individual, file_type: str) -> Path | None:
    if file_type not in _FILE_SPECS:
        return None

    subfolder, patterns, _ = _FILE_SPECS[file_type]
    base = ind.base_path / subfolder
    fmt = dict(
        oldname=ind.oldname,
        name=ind.name,
        kc=ind.kc,
        kpoint=ind.kpoint,
        kout=ind.kout,
        kin=ind.kin,
    )

    for pattern in patterns:
        candidate = base / pattern.format(**fmt)
        if candidate.exists():
            return candidate

    return None


def display_max(file_type: str) -> int | None:
    """Declared display maximum for scaling. None means auto (percentile B&C)."""
    spec = _FILE_SPECS.get(file_type)
    return spec[2] if spec else None


_SUBFOLDER_TO_TYPE: dict[str, str] = {
    '00_Original': 'original',
    '02_Close':    'close',
    '03_OuterMask':'outer',
    '04_InnerMask':'inner',
    '05_ThickMask':'thick',
    '06_Trab':     'trab',
}


def infer_file_type_from_path(path: Path) -> str | None:
    """Infer the file_type of an MHD from its subfolder name and filename suffix."""
    subfolder = path.parent.name
    if subfolder in _SUBFOLDER_TO_TYPE:
        return _SUBFOLDER_TO_TYPE[subfolder]
    if subfolder == '01_Seg':
        return 'rdn_seg' if '_RDN_' in path.stem else 'seg'
    if subfolder == '07_MaskSeg':
        if path.stem.endswith('_MaskSegIn'):
            return 'masksegin'
        if path.stem.endswith('_MaskSegOut'):
            return 'masksegout'
        if path.stem.endswith('_MaskSeg'):
            return 'maskseg'
    return None


_SCAN_SUBFOLDER: dict[str, str] = {
    'original':   '00_Original',
    'seg':        '01_Seg',
    'rdn_seg':    '01_Seg',
    'close':      '02_Close',
    'outer':      '03_OuterMask',
    'inner':      '04_InnerMask',
    'thick':      '05_ThickMask',
    'trab':       '06_Trab',
    'masksegin':  '07_MaskSeg',
    'masksegout': '07_MaskSeg',
    'maskseg':    '07_MaskSeg',
}


def resolve_file_from_scan(base_path: Path, file_type: str) -> Path | None:
    """Find a file by globbing the expected subfolder — no Individual name fields needed.

    Used when loading a single scan selected by the user rather than via a PAR.
    base_path is the individual's root folder (parent of the XX_SubFolder directories).
    """
    subfolder = _SCAN_SUBFOLDER.get(file_type)
    if subfolder is None:
        return None
    folder = base_path / subfolder
    if not folder.exists():
        return None

    candidates = sorted(folder.glob('*.mhd'))

    if file_type == 'original':
        for suffix in ('_reoriented_cropped.mhd', '_cropped.mhd', '_reoriented.mhd'):
            match = next((p for p in candidates if p.name.endswith(suffix)), None)
            if match:
                return match
        return candidates[0] if candidates else None
    elif file_type == 'seg':
        candidates = [p for p in candidates if '_RDN_' not in p.stem]
        for suffix in ('_seg_cropped_capped.mhd', '_seg_capped.mhd', '_seg_cropped.mhd', '_seg.mhd'):
            match = next((p for p in candidates if p.name.endswith(suffix)), None)
            if match:
                return match
        return candidates[0] if candidates else None
    elif file_type == 'rdn_seg':
        candidates = [p for p in candidates if '_RDN_' in p.stem]
    elif file_type == 'masksegin':
        candidates = [p for p in candidates if p.stem.endswith('_MaskSegIn')]
    elif file_type == 'masksegout':
        candidates = [p for p in candidates if p.stem.endswith('_MaskSegOut')]
    elif file_type == 'maskseg':
        candidates = [p for p in candidates
                      if p.stem.endswith('_MaskSeg')
                      and not p.stem.endswith(('_MaskSegIn', '_MaskSegOut'))]

    return candidates[0] if candidates else None
