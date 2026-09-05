"""
MetaManager: lazily compute per-resource audio metadata and cache it on disk.

Disk layout (mirrors content addressing of ResourceManager)::

    <meta_cache>/<file_id>-meta.json

JSON layout (``artwork_data`` 以 base64 文本保存，便于单文件整体搬运)::

    {
        "title":        str,
        "artist":       str,
        "album":        str,
        "artwork_mime": str | null,
        "artwork_b64":  str | null
    }

Lookup order: in-memory dict -> disk cache -> (compute + persist). Computing is
deferred to first request (lazy); cache files are written atomically.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Any, Optional

from infinity_music.music_meta import get_audio_metadata

logger = logging.getLogger(__name__)

SUFFIX = '-meta.json'


def _serialize(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize music_meta output into a JSON-serializable dict (base64 artwork)."""
    data: dict[str, Any] = {
        'title': raw.get('title') or '',
        'artist': raw.get('artist') or '',
        'album': raw.get('album') or '',
        'artwork_mime': raw.get('artwork_mime'),
        'artwork_b64': None,
    }
    artwork = raw.get('artwork_data')
    if artwork:
        data['artwork_mime'] = raw.get('artwork_mime') or 'image/jpeg'
        data['artwork_b64'] = base64.b64encode(artwork).decode('ascii')
    return data


class MetaManager:
    """Caches audio metadata under a content-hash-addressed JSON file per resource."""

    def __init__(self, root: Optional[Path]):
        self.root = root
        self._memory: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ lookups
    def cache_path_for(self, file_id: str) -> Optional[Path]:
        """The on-disk cache path for *file_id* (None when caching disabled)."""
        if self.root is None:
            return None
        return self.root / f'{file_id}{SUFFIX}'

    def get_or_build(self, path: Path, file_id: str) -> dict[str, Any]:
        """Return metadata for *path*, computing it lazily and caching as JSON.

        ``file_id`` is the SHA-256 content hash of the media file, so the cache
        file is invalidated automatically whenever the file content changes.
        """
        # 1) in-memory
        cached = self._memory.get(file_id)
        if cached is not None:
            return dict(cached)

        # 2) on-disk cache
        disk_path = self.cache_path_for(file_id)
        if disk_path is not None and disk_path.is_file():
            try:
                data = json.loads(disk_path.read_text())
                if isinstance(data, dict):
                    self._memory[file_id] = data
                    return dict(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning('读取 meta 缓存失败 %s: %s', disk_path, e)

        # 3) compute + persist
        try:
            data = _serialize(get_audio_metadata(path))
        except Exception as e:  # 防御：解析失败也缓存一个空 meta，避免重复解析
            logger.warning('惰性计算元数据失败 %s: %s', path, e)
            data = _serialize({})

        self._memory[file_id] = data
        if disk_path is not None:
            try:
                self.root.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
                tmp = disk_path.with_suffix('.tmp')
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                tmp.replace(disk_path)
            except OSError as e:
                logger.warning('写 meta 缓存失败 %s: %s', disk_path, e)
        return dict(data)

    def artwork_bytes(self, meta: dict[str, Any]) -> Optional[bytes]:
        """Decode the base64 artwork from a meta dict, if any."""
        b64 = meta.get('artwork_b64')
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            return None
