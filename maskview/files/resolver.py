from pathlib import Path
from ..par.parser import Individual


FILE_TYPE_LABELS: dict[str, str] = {
    'original':   'Original CT',
    'seg':        'Segmentation',
    'rdn_seg':    'RDN Segmentation',
    'close':      'Close',
    'outer':      'Outer Mask',
    'inner':      'Inner Mask',
    'thick':      'Thick Mask',
    'trab':       'Trabecular Mask',
    'masksegin':  'MaskSeg In',
    'masksegout': 'MaskSeg Out',
    'maskseg':    'MaskSeg',
}

FILE_TYPE_ORDER: list[str] = [
    'original', 'seg', 'rdn_seg', 'close', 'outer', 'inner', 'thick', 'trab',
    'masksegin', 'masksegout', 'maskseg',
]

# (subfolder, filename_patterns, declared_display_max)
# display_max=None → auto percentile B&C (original CT only)
_FILE_SPECS: dict[str, tuple[str, list[str], int | None]] = {
    'original':   ('00_Original', ['{oldname}.mhd'],                                                           None),
    'seg':        ('01_Seg',      ['{name}_seg.mhd'],                                                          1),
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
