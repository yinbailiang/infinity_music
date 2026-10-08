"""
Infinity Music: a minimal FastAPI app for distributing music and media.

No module-level state: main() takes the .infd config path from the command
line, compiles it, then builds the dependency graph (config -> ResourceManager
-> FastAPI routes) and injects it explicitly before serving.
"""

import argparse
import mimetypes
import os
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePath
from typing import Any, List, Tuple, cast
from urllib.parse import quote

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from infinity_data import SandboxConfig, Schema, load

from infinity_music.meta_manager import MetaManager
from infinity_music.resource_manager import ResourceManager
from infinity_music.templates import PageRenderer, load_page_templates

# ---------------------------------------------------------------------------
# Configuration model（.infd 数据 + 包内 schemas.inft 结构校验）
# ---------------------------------------------------------------------------
SCHEMA_FILE = Path(__file__).resolve().parent / 'assets' / 'schemas.inft'
CONFIG_SCHEMA = Schema(template='AppConfig', from_file=str(SCHEMA_FILE), mode='strict')
# dir/file 文件系统约束与外部模板导入需要访问本机文件系统
CONFIG_SANDBOX = SandboxConfig.full_access()


@dataclass(frozen=True)
class AppConfig:
    """Typed application config compiled from an .infd file."""

    host: str
    port: int
    reload: bool
    res_root: Path
    static_root: Path
    templates_dir: Path
    meta_cache: Path | None = None


def _resolve_path(base: Path, value: PurePath) -> Path:
    """Resolve a path value; relative ones are anchored to the config dir."""
    if value.is_absolute():
        return Path(value)
    return (base / value).resolve()


def load_config(path: Path) -> AppConfig:
    """Compile the .infd config at *path*; raise RuntimeError on failure."""
    result = load(path, schema=CONFIG_SCHEMA, sandbox=CONFIG_SANDBOX)
    if result.has_errors:
        details = '\n'.join(f'  {d.code}: {d.message}' for d in result.diagnostics)
        raise RuntimeError(f'配置 {path} 编译失败:\n{details}')

    raw = result.value
    server: dict[str, Any] = cast('dict[str, Any]', raw['server'])
    base = path.resolve().parent
    raw_meta_cache = raw.get('meta_cache')
    return AppConfig(
        host=cast(str, server['host']),
        port=cast(int, server['port']),
        reload=cast(bool, server['reload']),
        res_root=_resolve_path(base, raw['media_dir']),
        static_root=_resolve_path(base, raw['static_dir']),
        templates_dir=_resolve_path(base, raw['templates_dir']),
        meta_cache=_resolve_path(base, raw_meta_cache) if isinstance(raw_meta_cache, PurePath) else None,
    )


# ---------------------------------------------------------------------------
# HTML helpers（纯函数，无全局状态）
# ---------------------------------------------------------------------------
APP_NAME = 'Infinity Music'

# 分层缓存策略：
# - MEDIA：/media/{id} 的 id 是文件内容 SHA-256（内容寻址），URL 永不失效 → immutable 长缓存
# - STATIC：/static/{path} 按路径寻址，作者可能覆盖同名文件 → 短缓存，过期后靠
#   FileResponse 自带的 ETag/Last-Modified 做条件复验
# - HOME：/ 是聚合列表，加删资源即变且 URL 不变 → 不缓存
# - DETAIL：/detail/{id} 由内容哈希寻址，页面只取决于媒体元数据（随文件哈希固定）
#   与站点皮肤（模板/CSS/JS，仅部署时变更）→ 可给中长缓存。max-age 让浏览器较快
#   拿到改版后的皮肤，s-maxage 让 CDN 驻留更久、分担回源
CACHE_MEDIA = 'public, max-age=31536000, immutable'
CACHE_STATIC = 'public, max-age=86400'
CACHE_HOME = 'no-cache'
CACHE_DETAIL = 'public, max-age=3600, s-maxage=86400'


def _render_file_item(
    path: Path,
    file_id: str,
    renderer: PageRenderer,
    meta_manager: MetaManager,
) -> str:
    """Render one media file as a home/browse list entry (icon + title + artist)."""
    mime_type, _ = mimetypes.guess_type(path.name)
    file_name = escape(path.name)

    if mime_type and mime_type.startswith('audio/'):
        meta = meta_manager.get_or_build(path, file_id)
        title = meta.get('title') or path.stem
        artist = meta.get('artist') or ''
        if meta.get('artwork_b64'):
            # 有内嵌封面：展示缩略图（经 /cover 接口，immutable 缓存）
            icon = renderer.render('home_item_thumb', file_id=file_id)
        else:
            icon = renderer.render('icon_audio')
    elif mime_type and mime_type.startswith('video/'):
        title = path.stem
        artist = ''
        icon = renderer.render('icon_video')
    else:
        title = path.stem
        artist = ''
        icon = renderer.render('icon_file')

    return renderer.render(
        'home_item',
        file_id=file_id,
        file_name=file_name,
        title=escape(title),
        artist=escape(artist),
        icon=icon,
    )


def _render_children(
    rel_dir: str,
    manager: ResourceManager,
    renderer: PageRenderer,
    meta_manager: MetaManager,
) -> str:
    """Render one directory level: nested folder rows + media file entries.

    文件夹行是 <details> + htmx（click once），其内容（更深层）由浏览器
    首次展开时再向 /browse/{rel} 惰性请求，因此支持任意深度嵌套。
    """
    subdirs, files = manager.list_dir(rel_dir)
    parts: List[str] = []

    for dir_name in subdirs:
        child_rel = dir_name if not rel_dir else f'{rel_dir}/{dir_name}'
        parts.append(
            renderer.render(
                'dir_item',
                dir_rel=quote(child_rel, safe='/'),
                dir_name=escape(dir_name),
                dir_count=str(manager.count_children(child_rel)),
            )
        )

    for path, sha in files:
        parts.append(_render_file_item(path, sha, renderer, meta_manager))

    return ''.join(parts) if parts else renderer.render('home_empty')


def _render_home(
    renderer: PageRenderer,
    manager: ResourceManager,
    meta_manager: MetaManager,
) -> str:
    """Render the home page: virtual Search folder + top-level folders/files."""
    items = renderer.render('search_row') + _render_children('', manager, renderer, meta_manager)

    return renderer.render(
        'home_page',
        app_name=APP_NAME,
        items=items,
    )


def _search_matches(
    query: str,
    manager: ResourceManager,
    meta_manager: MetaManager,
) -> List[Tuple[Path, str]]:
    """Advanced search: filename/path + audio tags (title/artist/album).

    音频元数据经 MetaManager 惰性构建并缓存；首次搜索会一次性补全全库缓存，
    之后读取磁盘 JSON 快速匹配。多个关键词需同时命中。
    """
    tokens = query.split()
    results: List[Tuple[Path, str]] = []
    for path, sha in manager.resources:
        fields = [path.stem, path.relative_to(manager.root).as_posix()]
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type and mime_type.startswith('audio/'):
            meta = meta_manager.get_or_build(path, sha)
            fields.extend(
                [
                    str(meta.get('title') or ''),
                    str(meta.get('artist') or ''),
                    str(meta.get('album') or ''),
                ]
            )
        blob = ' '.join(fields).casefold()
        if all(tok in blob for tok in tokens):
            results.append((path, sha))
    return results


def _render_detail(
    path: Path,
    file_id: str,
    renderer: PageRenderer,
) -> str:
    """Render the detail-page shell; real content is lazy-loaded via htmx.

    The media meta/player fragment lives behind ``/fragment/{file_id}`` and is
    pulled in by htmx on load, so audio metadata parsing happens only when a
    resource is actually viewed (and is cached by MetaManager afterwards).
    """
    file_name = escape(path.name)
    content = renderer.render('detail_loading', file_id=file_id)

    return renderer.render(
        'detail_page',
        app_name=APP_NAME,
        file_name=file_name,
        file_id=file_id,
        content=content,
    )


def _render_fragment(
    path: Path,
    file_id: str,
    renderer: PageRenderer,
    meta_manager: MetaManager,
) -> str:
    """Render the htmx content fragment for one media file.

    - video -> native ``<video>`` player fragment
    - audio -> metadata (lazily computed & cached by MetaManager) + player
    - other -> plain title fragment
    """
    file_name = escape(path.name)
    mime_type, _ = mimetypes.guess_type(path.name)

    if mime_type and mime_type.startswith('video/'):
        return renderer.render('detail_video', file_name=file_name, file_id=file_id)

    if mime_type and mime_type.startswith('audio/'):
        meta = meta_manager.get_or_build(path, file_id)
        meta_title = escape(meta.get('title') or path.stem)
        meta_artist = escape(meta.get('artist') or 'Unknown Artist')
        meta_album = escape(meta.get('album') or '')

        cover = ''
        if meta.get('artwork_b64'):
            cover = renderer.render('detail_cover', file_id=file_id)
        return renderer.render(
            'detail_audio',
            cover=cover,
            meta_title=meta_title,
            meta_artist=meta_artist,
            meta_album=meta_album,
            file_id=file_id,
        )

    return renderer.render('detail_other', file_name=file_name)


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


def get_renderer(request: Request) -> PageRenderer:
    """FastAPI dependency: the PageRenderer stored on app.state."""
    renderer = request.app.state.page_renderer
    assert isinstance(renderer, PageRenderer)
    return renderer


def get_meta_manager(request: Request) -> MetaManager:
    """FastAPI dependency: the MetaManager stored on app.state."""
    meta_manager = request.app.state.meta_manager
    assert isinstance(meta_manager, MetaManager)
    return meta_manager


@router.get('/', response_class=HTMLResponse)
async def home(
    response: Response,
    manager: ResourceManager = Depends(get_manager),
    renderer: PageRenderer = Depends(get_renderer),
    meta_manager: MetaManager = Depends(get_meta_manager),
) -> str:
    response.headers['Cache-Control'] = CACHE_HOME
    return _render_home(renderer, manager, meta_manager)


@router.get('/browse/{dir_path:path}', response_class=HTMLResponse)
async def browse_dir(
    response: Response,
    dir_path: str,
    manager: ResourceManager = Depends(get_manager),
    renderer: PageRenderer = Depends(get_renderer),
    meta_manager: MetaManager = Depends(get_meta_manager),
) -> str:
    """htmx 文件夹惰性加载片段：返回该目录下的子文件夹 + 媒体条目。"""
    response.headers['Cache-Control'] = CACHE_HOME
    root = manager.root.resolve()
    target = (root / dir_path).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise HTTPException(status_code=404)
    rel = target.relative_to(root).as_posix()
    return _render_children(rel, manager, renderer, meta_manager)


@router.get('/search', response_class=HTMLResponse)
async def search_media(
    response: Response,
    q: str = '',
    manager: ResourceManager = Depends(get_manager),
    renderer: PageRenderer = Depends(get_renderer),
    meta_manager: MetaManager = Depends(get_meta_manager),
) -> str:
    """htmx 实时搜索片段：匹配文件名/路径 + 音频曲名/作者/专辑。"""
    response.headers['Cache-Control'] = CACHE_HOME
    query = q.strip().casefold()
    if not query:
        return ''

    matched = _search_matches(query, manager, meta_manager)
    if not matched:
        return renderer.render('home_empty')
    return ''.join(_render_file_item(path, sha, renderer, meta_manager) for path, sha in matched)


@router.get('/detail/{file_id}', response_class=HTMLResponse)
async def resource_detail(
    response: Response,
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
    renderer: PageRenderer = Depends(get_renderer),
) -> str:
    response.headers['Cache-Control'] = CACHE_DETAIL
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    sha = manager.get_id(file_path)
    assert sha is not None  # get_by_id 命中则 id 必存在
    return _render_detail(file_path, sha, renderer)


@router.get('/fragment/{file_id}', response_class=HTMLResponse)
async def media_fragment(
    response: Response,
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
    renderer: PageRenderer = Depends(get_renderer),
    meta_manager: MetaManager = Depends(get_meta_manager),
) -> str:
    """htmx 惰性加载的内容片段（音频 meta / 视频 / 其他文件）。"""
    response.headers['Cache-Control'] = CACHE_DETAIL
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    return _render_fragment(file_path, file_id, renderer, meta_manager)


@router.get('/cover/{file_id}')
async def media_cover(
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
    meta_manager: MetaManager = Depends(get_meta_manager),
) -> Response:
    """Serve the embedded cover artwork bytes for an audio resource.

    封面数据来自 MetaManager 的 `<hash>-meta.json` 缓存；file_id 是内容哈希，
    URL 不可变，因此可以安全使用 immutable 长缓存。
    """
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    mime_type, _ = mimetypes.guess_type(file_path.name)
    if not (mime_type and mime_type.startswith('audio/')):
        raise HTTPException(status_code=404)
    meta = meta_manager.get_or_build(file_path, file_id)
    content = meta_manager.artwork_bytes(meta)
    if content is None:
        raise HTTPException(status_code=404)
    return Response(
        content=content,
        media_type=meta.get('artwork_mime') or 'image/jpeg',
        headers={'Cache-Control': CACHE_MEDIA},
    )


@router.get('/media/{file_id}')
async def media(
    file_id: str,
    manager: ResourceManager = Depends(get_manager),
) -> FileResponse:
    file_path = manager.get_by_id(file_id)
    if file_path is None:
        raise HTTPException(status_code=404)
    # 内容寻址：file_id 是内容哈希，URL 不可变，可安全 immutable 长缓存
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        headers={'Cache-Control': CACHE_MEDIA},
    )


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
    # 静态文件按路径寻址，可能被覆盖 → 短缓存；FileResponse 自带的
    # ETag/Last-Modified 在过期后触发条件请求做复验
    return FileResponse(path=target, headers={'Cache-Control': CACHE_STATIC})


# ---------------------------------------------------------------------------
# App factory（显式依赖注入，无全局状态）
# ---------------------------------------------------------------------------
def build_app(config: AppConfig) -> FastAPI:
    """Build a FastAPI app; deps (config/manager/renderer) injected via app.state."""
    app = FastAPI(title=APP_NAME, version='0.1.0')
    app.state.config = config
    app.state.resource_manager = ResourceManager(config.res_root)
    app.state.meta_manager = MetaManager(config.meta_cache)
    app.state.page_renderer = PageRenderer(load_page_templates(config.templates_dir))
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
