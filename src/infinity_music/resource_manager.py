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
            # 隐藏目录与隐藏文件（.gitkeep/.DS_Store/.hash_cache.json 等）不作为资源
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for file_name in files:
                if file_name.startswith('.'):
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
            except json.JSONDecodeError, OSError:
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

    def list_dir(self, rel_dir: str = '') -> Tuple[List[str], List[Tuple[Path, str]]]:
        """List one directory level under the media root.

        Args:
            rel_dir: 相对 media root 的目录（POSIX 风格，'' 表示根目录）。

        Returns:
            (subdir_names, file_items)，均按名称排序；隐藏条目已被过滤。
        """
        base = self.root if not rel_dir else self.root / rel_dir
        subdirs: List[str] = []
        files: List[Tuple[Path, str]] = []

        if not base.is_dir():
            return subdirs, files

        entries = list(os.scandir(base))
        entries.sort(key=lambda e: (not e.is_dir(), e.name.casefold()))
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            path = Path(entry.path)
            if entry.is_dir():
                subdirs.append(entry.name)
            elif entry.is_file():
                sha = self._path_to_id.get(path)
                if sha is None:
                    # 启动后新增的文件：实时计算并登记，保证 /detail、/media、/cover 可访问
                    sha = self._file_sha256(path)
                    self._path_to_id[path] = sha
                    self._id_to_path[sha] = path
                files.append((path, sha))
        return subdirs, files

    def count_children(self, rel_dir: str = '') -> int:
        """Count direct children (files + folders) of a media-root-relative dir."""
        base = self.root if not rel_dir else self.root / rel_dir
        if not base.is_dir():
            return 0
        return sum(1 for e in os.scandir(base) if not e.name.startswith('.'))
