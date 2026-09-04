"""
Content-addressed media resource manager.

Scans a media directory tree, assigns each file a SHA-256 content-hash ID,
and persists a JSON cache (size + mtime -> hash) so full-file hashing is not
recomputed on every startup.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CACHE_FILE_NAME = '.hash_cache.json'


class ResourceManager:
    """Scans a media directory, maintains content-hash IDs, and serves lookups."""

    def __init__(self, root: Path):
        self.root = root
        self.cache_path = root / CACHE_FILE_NAME
        self.resources: List[Tuple[Path, str]] = []  # (absolute_path, sha256)
        self._id_to_path: Dict[str, Path] = {}
        self._path_to_id: Dict[Path, str] = {}
        self._scan()

    # ------------------------------------------------------------------ scan
    def _scan(self) -> None:
        """Scan the media directory, using cached hashes when possible."""
        if not self.root.exists():
            return

        cache = self._load_cache()
        new_cache: Dict[str, Dict[str, str]] = {}
        resources: List[Tuple[Path, str]] = []

        for current_root, dirs, files in os.walk(self.root):
            # Skip hidden directories (optional)
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file_name in files:
                if file_name == CACHE_FILE_NAME:
                    continue

                file_path = Path(current_root, file_name)
                rel_path = file_path.relative_to(self.root).as_posix()
                stat = file_path.stat()
                meta_key = f'{stat.st_size}:{stat.st_mtime_ns}'

                cached_entry = cache.get(rel_path)
                if cached_entry and cached_entry.get('meta') == meta_key:
                    # File unchanged: reuse cached hash
                    sha = cached_entry['hash']
                else:
                    # Compute new hash (chunked reading to handle large files)
                    sha = self._file_sha256(file_path)

                new_cache[rel_path] = {'hash': sha, 'meta': meta_key}
                resources.append((file_path, sha))

        # Update instance state
        self.resources = sorted(resources, key=lambda x: x[0])
        self._id_to_path = {sha: path for path, sha in self.resources}
        self._path_to_id = {path: sha for path, sha in self.resources}

        # Persist updated cache
        self._save_cache(new_cache)

    # ------------------------------------------------------------- utilities
    @staticmethod
    def _file_sha256(path: Path) -> str:
        """Compute SHA-256 of a file in chunks (memory efficient)."""
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()

    def _load_cache(self) -> Dict[str, Dict[str, str]]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # Corrupted cache -> ignore, will rebuild
        return {}

    def _save_cache(self, cache: Dict[str, Dict[str, str]]) -> None:
        """Atomically write cache to disk."""
        tmp_path = self.cache_path.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(cache, indent=2))
        tmp_path.replace(self.cache_path)

    # --------------------------------------------------------------- queries
    def get_by_id(self, file_id: str) -> Optional[Path]:
        """Return the Path for a given file ID, or None if not found."""
        return self._id_to_path.get(file_id)

    def get_id(self, path: Path) -> Optional[str]:
        """Return the ID for a given Path, or None if not found."""
        return self._path_to_id.get(path)
