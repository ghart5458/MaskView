from pathlib import Path
import numpy as np


class TurboVolume:
    """Three orientation-specific strided views of one volume.

    xy  — data[::step, :, :] — used when viewing XY planes (stride along Z)
    xz  — data[:, ::step, :] — used when viewing XZ planes (stride along Y)
    yz  — data[:, :, ::step] — used when viewing YZ planes (stride along X)

    Each view has full in-plane resolution; only the scroll axis is compressed.
    """
    __slots__ = ("xy", "xz", "yz")

    def __init__(self, xy: np.ndarray, xz: np.ndarray, yz: np.ndarray):
        self.xy, self.xz, self.yz = xy, xz, yz


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


def load_volume(mhd_path: Path, use_memmap: bool = False,
                turbo_step: int = 1) -> tuple[np.ndarray, dict[str, str]]:
    """Load an MHD/RAW volume. Returns (array with shape [z, y, x], mhd_metadata).

    turbo_step > 1: for external .raw files, uses memmap so only every Nth Z-slice
    is read from disk (~4x faster over slow network drives).  XY in-plane resolution
    is preserved; XZ/YZ cross-sections will have Z compressed.

    use_memmap=True (non-turbo): maps the file without reading it; slices are paged
    in on demand.
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
        if turbo_step > 1:
            data = np.ascontiguousarray(
                data[::turbo_step, ::turbo_step, ::turbo_step])
        return data, meta

    raw_path = mhd_path.parent / raw_name
    if turbo_step > 1:
        # memmap the file then copy every Nth voxel in all three dimensions.
        # Z-stride is the main I/O saving (contiguous slice blocks); Y/X strides
        # reduce array size in RAM and keep all three view orientations proportional.
        mm = np.memmap(raw_path, dtype=dtype, mode='r', shape=shape)
        return np.ascontiguousarray(
            mm[::turbo_step, ::turbo_step, ::turbo_step]), meta
    if use_memmap:
        return np.memmap(raw_path, dtype=dtype, mode='r', shape=shape), meta
    return np.fromfile(raw_path, dtype=dtype).reshape(shape), meta


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
