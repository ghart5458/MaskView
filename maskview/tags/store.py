import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Tag:
    id: str
    x: int
    y: int
    z: int
    note: str = ""
    color: str = "#ffaa00"


class TagStore:
    """Loads and saves tags for a single volume file to a JSON sidecar."""

    def __init__(self, volume_path: Path):
        self._json_path = volume_path.with_name(volume_path.stem + "_MV_tags.json")
        self._tags: list[Tag] = []
        self._load()

    @property
    def tags(self) -> list[Tag]:
        return list(self._tags)

    def add(self, x: int, y: int, z: int, note: str = "", color: str = "#ffaa00") -> Tag:
        tag = Tag(id=str(uuid.uuid4()), x=x, y=y, z=z, note=note, color=color)
        self._tags.append(tag)
        self._save()
        return tag

    def update(self, tag_id: str, note: str, color: str) -> bool:
        for tag in self._tags:
            if tag.id == tag_id:
                tag.note = note
                tag.color = color
                self._save()
                return True
        return False

    def remove(self, tag_id: str) -> bool:
        before = len(self._tags)
        self._tags = [t for t in self._tags if t.id != tag_id]
        if len(self._tags) < before:
            self._save()
            return True
        return False

    def clear(self) -> None:
        self._tags.clear()
        self._save()

    def _load(self):
        if self._json_path.exists():
            try:
                raw = json.loads(self._json_path.read_text(encoding="utf-8"))
                self._tags = [Tag(**t) for t in raw]
            except Exception:
                self._tags = []

    def _save(self):
        self._json_path.write_text(
            json.dumps([asdict(t) for t in self._tags], indent=2),
            encoding="utf-8",
        )
