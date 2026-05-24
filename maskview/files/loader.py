from pathlib import Path
import numpy as np


_DTYPE_MAP: dict[str, type] = {
    'MET_UCHAR':  np.uint8,
    'MET_CHAR':   np.int8,
    'MET_USHORT': np.uint16,
    'MET_SHORT':  np.int16,
    'MET_UINT':   np.uint32,
    'MET_INT':    np.int32,
    'MET_FLOAT':  np.float32,
    'MET_DOUBLE': np.float64,
}


def parse_mhd(mhd_path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in Path(mhd_path).read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            meta[key.strip()] = value.strip()
    return meta


def load_volume(mhd_path: Path, use_memmap: bool = False) -> tuple[np.ndarray, dict[str, str]]:
    """Load an MHD/RAW volume. Returns (array with shape [z, y, x], mhd_metadata).

    use_memmap=True reads slices on demand instead of loading the full file into RAM.
    Useful on machines with limited memory; default is full load for speed.
    """
    mhd_path = Path(mhd_path)
    meta = parse_mhd(mhd_path)

    dims = [int(d) for d in meta['DimSize'].split()]
    dtype = _DTYPE_MAP.get(meta.get('ElementType', 'MET_UCHAR'), np.uint8)
    shape = (dims[2], dims[1], dims[0])  # z, y, x

    raw_name = meta.get('ElementDataFile', '')

    if raw_name.upper() == 'LOCAL':
        raw_bytes = mhd_path.read_bytes()
        for marker in (b'ElementDataFile = LOCAL', b'ElementDataFile=LOCAL'):
            idx = raw_bytes.find(marker)
            if idx >= 0:
                break
        offset = raw_bytes.find(b'\n', idx) + 1
        data = np.frombuffer(raw_bytes[offset:], dtype=dtype).reshape(shape).copy()
    else:
        raw_path = mhd_path.parent / raw_name
        if use_memmap:
            data = np.memmap(raw_path, dtype=dtype, mode='r', shape=shape)
        else:
            data = np.fromfile(raw_path, dtype=dtype).reshape(shape)

    return data, meta


def compute_display_range(
    data: np.ndarray,
    percentile: float = 0.35,
    sample_slices: int = 10,
) -> tuple[float, float]:
    """FIJI-style auto B&C: clip top/bottom percentile across sampled slices.

    Samples every Nth slice rather than scanning the full volume for speed.
    """
    n = data.shape[0]
    step = max(1, n // sample_slices)
    sample = data[::step].ravel()
    low = float(np.percentile(sample, percentile))
    high = float(np.percentile(sample, 100.0 - percentile))
    if high <= low:
        high = low + 1.0
    return low, high
