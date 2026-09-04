"""
Minimal FastAPI music app with elegant resource management.

Uses content-addressed SHA-256 IDs and a JSON cache to avoid recomputing
hashes on every startup.
"""

import base64
import hashlib
import json
import mimetypes
import os
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from infinity_music.music_meta import get_audio_metadata

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RES_ROOT = PROJECT_ROOT / 'res'
CACHE_FILE_NAME = '.hash_cache.json'
CUSTOM_CSS = PROJECT_ROOT / 'custom.css'
CUSTOM_JS = PROJECT_ROOT / 'custom.js'


def load_custom_css() -> str:
    return CUSTOM_CSS.read_text() if CUSTOM_CSS.exists() else ''


def load_custom_js() -> str:
    return CUSTOM_JS.read_text() if CUSTOM_JS.exists() else ''


# ---------------------------------------------------------------------------
# Resource Manager
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------
@lru_cache
def get_resource_manager() -> ResourceManager:
    """Return a singleton ResourceManager for the application."""
    return ResourceManager(RES_ROOT)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title='Infinity Music', version='0.1.0')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get('/', response_class=HTMLResponse)
async def home(manager: ResourceManager = Depends(get_resource_manager)) -> str:
    def render_list_html(resources: List[Tuple[Path, str]]) -> str:
        """Return an HTML fragment listing all resources."""
        if not resources:
            return '<p class="home-res">Nothing</p>'
        items: list[str] = []
        for path, sha in resources:
            items.append(f'<p class="home-res"><a href="/detail/{sha}">{escape(path.name)}</a></p>')
        return ''.join(items)

    return f"""
<!doctype html>
<html lang="zh-Hans">
<head>
    <meta charset="utf-8" />
    <title>Infinity Music</title>
    <style>{load_custom_css()}</style>
</head>
<script>{load_custom_js()}</script>
<body class="home-body">
    <main class="home-main">
        <h1 class="home-title">Infinity Music</h1>
        {render_list_html(manager.resources)}
    </main>
</body>
</html>
"""


@app.get('/detail/{file_id}', response_class=HTMLResponse)
async def resource_detail(
    file_id: str,
    manager: ResourceManager = Depends(get_resource_manager),
) -> str:
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)

    sha = manager.get_id(file_path)
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if mime_type and mime_type.startswith('video/'):
        title = f'<h1 class="detail-title">{escape(file_path.name)}</h1>'
        player = f'<video class="detail-media" src="/media/{sha}" controls preload="metadata" loop autoplay></video>'
    elif mime_type and mime_type.startswith('audio/'):
        # 音频文件：解析元数据并生成 HTML
        meta = get_audio_metadata(file_path)
        meta_title = meta.get('title') or file_path.stem
        meta_artist = meta.get('artist') or 'Unknown Artist'
        meta_album = meta.get('album') or ''

        # 处理封面图
        artwork_html = ''
        if meta.get('artwork_data'):
            artwork_b64 = base64.b64encode(meta['artwork_data']).decode()
            artwork_mime = meta.get('artwork_mime') or 'image/jpeg'
            artwork_html = f'<img class="detail-cover" src="data:{artwork_mime};base64,{artwork_b64}" alt="Cover" />'

        title = ""
        player = f"""
<div class="detail-meta">
    {artwork_html}
    <div class="detail-meta-text">
        <p class="detail-meta-title">{escape(meta_title)}</p>
        <p class="detail-artist">{escape(meta_artist)}</p>
        <p class="detail-album">{escape(meta_album)}</p>
    </div>
</div>
<audio class="detail-media" src="/media/{sha}" controls preload="metadata" loop autoplay></audio>
        """
    else:
        title = f'<h1 class="detail-title">{escape(file_path.name)}</h1>'
        player = ''

    return f"""
<!doctype html>
<html lang="zh-Hans">
<head>
    <meta charset="utf-8" />
    <title>{escape(file_path.name)} - Infinity Music</title>
    <style>{load_custom_css()}</style>
</head>
<script>{load_custom_js()}</script>
<body class="detail-body">
    <main class="detail-main">
        {title}
        {player}
        <a class="detail-choice" href="/media/{sha}">Download</a>
        <a class="detail-choice" href="/">Back</a>
    </main>
</body>
</html>
"""


@app.get('/media/{file_id}')
async def media(
    file_id: str,
    manager: ResourceManager = Depends(get_resource_manager),
) -> FileResponse:
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)

    return FileResponse(path=file_path, filename=file_path.name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    uvicorn.run('infinity_music.main:app', host='0.0.0.0', port=8000, reload=True)
