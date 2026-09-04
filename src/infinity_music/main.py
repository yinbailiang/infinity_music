"""
Infinity Music: a minimal FastAPI app for distributing music and media.

No module-level state: main() takes the .infd config path from the command
line, compiles it, then builds the dependency graph (config -> ResourceManager
-> FastAPI routes) and injects it explicitly before serving.
"""

import argparse
import base64
import mimetypes
import os
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePath
from typing import Any, List, Tuple, cast

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from infinity_data import Schema, load

from infinity_music.music_meta import get_audio_metadata
from infinity_music.resource_manager import ResourceManager

# ---------------------------------------------------------------------------
# Configuration model（.infd，InfinityData 语言）
# ---------------------------------------------------------------------------
CONFIG_SCHEMA = Schema(template='AppConfig', mode='strict')


@dataclass(frozen=True)
class AppConfig:
    """Typed application config compiled from an .infd file."""

    host: str
    port: int
    reload: bool
    res_root: Path
    static_root: Path
    custom_css: Path
    custom_js: Path


def _resolve_path(base: Path, value: PurePath) -> Path:
    """Resolve a path value; relative ones are anchored to the config dir."""
    if value.is_absolute():
        return Path(value)
    return (base / value).resolve()


def load_config(path: Path) -> AppConfig:
    """Compile the .infd config at *path*; raise RuntimeError on failure."""
    result = load(path, schema=CONFIG_SCHEMA)
    if result.has_errors:
        details = '\n'.join(f'  {d.code}: {d.message}' for d in result.diagnostics)
        raise RuntimeError(f'配置 {path} 编译失败:\n{details}')

    raw = result.value
    server: dict[str, Any] = cast('dict[str, Any]', raw['server'])
    base = path.resolve().parent
    return AppConfig(
        host=cast(str, server['host']),
        port=cast(int, server['port']),
        reload=cast(bool, server['reload']),
        res_root=_resolve_path(base, raw['media_dir']),
        static_root=_resolve_path(base, raw['static_dir']),
        custom_css=_resolve_path(base, raw['ui_css']),
        custom_js=_resolve_path(base, raw['ui_js']),
    )


# ---------------------------------------------------------------------------
# HTML helpers（纯函数，无全局状态）
# ---------------------------------------------------------------------------
def _read_optional(path: Path) -> str:
    """Read a file's text, returning '' if it does not exist."""
    return path.read_text() if path.exists() else ''


def _render_home(resources: List[Tuple[Path, str]], config: AppConfig) -> str:
    """Render the home page listing every resource."""
    if not resources:
        body = '<p class="home-res">Nothing</p>'
    else:
        body = ''.join(
            f'<p class="home-res"><a href="/detail/{sha}">{escape(path.name)}</a></p>'
            for path, sha in resources
        )

    return f"""<!doctype html>
<html lang="zh-Hans">
<head>
    <meta charset="utf-8" />
    <title>Infinity Music</title>
    <style>{_read_optional(config.custom_css)}</style>
</head>
<script>{_read_optional(config.custom_js)}</script>
<body class="home-body">
    <main class="home-main">
        <h1 class="home-title">Infinity Music</h1>
        {body}
    </main>
</body>
</html>
"""


def _render_detail(path: Path, file_id: str, config: AppConfig) -> str:
    """Render the detail page for one media file."""
    mime_type, _ = mimetypes.guess_type(path.name)

    if mime_type and mime_type.startswith('video/'):
        title = f'<h1 class="detail-title">{escape(path.name)}</h1>'
        player = (
            f'<video class="detail-media" src="/media/{file_id}" controls '
            'preload="metadata" loop autoplay></video>'
        )
    elif mime_type and mime_type.startswith('audio/'):
        # 音频：解析标签，内嵌封面图
        meta = get_audio_metadata(path)
        meta_title = meta.get('title') or path.stem
        meta_artist = meta.get('artist') or 'Unknown Artist'
        meta_album = meta.get('album') or ''

        artwork = ''
        if meta.get('artwork_data'):
            artwork_b64 = base64.b64encode(meta['artwork_data']).decode()
            artwork_mime = meta.get('artwork_mime') or 'image/jpeg'
            artwork = (
                f'<img class="detail-cover" src="data:{artwork_mime};base64,'
                f'{artwork_b64}" alt="Cover" />'
            )

        title = ''
        player = f"""<div class="detail-meta">
    {artwork}
    <div class="detail-meta-text">
        <p class="detail-meta-title">{escape(meta_title)}</p>
        <p class="detail-artist">{escape(meta_artist)}</p>
        <p class="detail-album">{escape(meta_album)}</p>
    </div>
</div>
<audio class="detail-media" src="/media/{file_id}" controls preload="metadata" loop autoplay></audio>
"""
    else:
        title = f'<h1 class="detail-title">{escape(path.name)}</h1>'
        player = ''

    return f"""<!doctype html>
<html lang="zh-Hans">
<head>
    <meta charset="utf-8" />
    <title>{escape(path.name)} - Infinity Music</title>
    <style>{_read_optional(config.custom_css)}</style>
</head>
<script>{_read_optional(config.custom_js)}</script>
<body class="detail-body">
    <main class="detail-main">
        {title}
        {player}
        <a class="detail-choice" href="/media/{file_id}">Download</a>
        <a class="detail-choice" href="/">Back</a>
    </main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes（依赖经 request.app.state 注入，无模块级状态）
# ---------------------------------------------------------------------------
router = APIRouter()


def get_config(request: Request) -> AppConfig:
    """FastAPI dependency: the AppConfig stored on app.state."""
    config = request.app.state.config
    assert isinstance(config, AppConfig)
    return config


def get_manager(request: Request) -> ResourceManager:
    """FastAPI dependency: the ResourceManager stored on app.state."""
    manager = request.app.state.resource_manager
    assert isinstance(manager, ResourceManager)
    return manager


@router.get('/', response_class=HTMLResponse)
async def home(
    manager: ResourceManager = Depends(get_manager),
    config: AppConfig = Depends(get_config),
) -> str:
    return _render_home(manager.resources, config)


@router.get('/detail/{file_id}', response_class=HTMLResponse)
async def resource_detail(
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
    config: AppConfig = Depends(get_config),
) -> str:
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    sha = manager.get_id(file_path)
    assert sha is not None  # get_by_id 命中则 id 必存在
    return _render_detail(file_path, sha, config)


@router.get('/media/{file_id}')
async def media(
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
) -> FileResponse:
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path=file_path, filename=file_path.name)


@router.get('/static/{file_path:path}')
async def static_resource(
    file_path: str,
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    """Serve a file from the static dir, guarding against path traversal."""
    root = config.static_root.resolve()
    target = (root / file_path).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=404)
    if not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path=target)


# ---------------------------------------------------------------------------
# App factory（显式依赖注入，无全局状态）
# ---------------------------------------------------------------------------
def build_app(config: AppConfig) -> FastAPI:
    """Build a FastAPI app; config & manager are injected via app.state."""
    app = FastAPI(title='Infinity Music', version='0.1.0')
    app.state.config = config
    app.state.resource_manager = ResourceManager(config.res_root)
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
CONFIG_ENV = 'INFINITY_MUSIC_CONFIG'


def create_app_from_env() -> FastAPI:
    """uvicorn factory 入口：从 CONFIG_ENV 环境变量构建 app。

    uvicorn 的 reload 模式要求 app 以可导入的字符串入口提供，而配置路径
    来自命令行，因此经环境变量传递、由本工厂重建依赖并注入。
    """
    return build_app(load_config(Path(os.environ[CONFIG_ENV])))


def main(argv: list[str] | None = None) -> None:
    """Parse CLI args, load the config, build the app, and serve it."""
    parser = argparse.ArgumentParser(description='Infinity Music 分发服务')
    parser.add_argument(
        '-c',
        '--config',
        type=Path,
        default=Path('config.infd'),
        help='配置文件路径（.infd，默认 ./config.infd）',
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if config.reload:
        # reload 需要可导入的字符串入口（uvicorn 不支持 app 对象 + reload）
        os.environ[CONFIG_ENV] = str(args.config.resolve())
        uvicorn.run(
            'infinity_music.main:create_app_from_env',
            factory=True,
            host=config.host,
            port=config.port,
            reload=True,
        )
    else:
        uvicorn.run(build_app(config), host=config.host, port=config.port)


if __name__ == '__main__':
    main()
