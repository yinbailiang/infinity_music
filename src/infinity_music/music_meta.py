import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Tuple

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

logger = logging.getLogger(__name__)


def _first_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ''
    return str(value) if value is not None else ''


def _extract_cover_from_flac_pictures(audio: FLAC) -> Optional[Tuple[bytes, str]]:
    """直接从 FLAC 对象的 pictures 属性提取封面（最可靠）"""
    try:
        pictures = audio.pictures
        if pictures:
            pic = pictures[0]  # 取第一张图片
            return pic.data, pic.mime
    except Exception as e:
        logger.warning(f'读取 FLAC pictures 失败: {e}')
    return None


def _extract_cover_from_id3(tags) -> Optional[Tuple[bytes, str]]:
    if not hasattr(tags, 'getall'):
        return None
    try:
        apic_list = tags.getall('APIC')
        if apic_list:
            apic = apic_list[0]
            return apic.data, apic.mime
    except Exception:
        pass
    return None


def _extract_cover_from_mp4(tags) -> Optional[Tuple[bytes, str]]:
    if 'covr' not in tags:
        return None
    try:
        covr = tags['covr']
        if covr:
            data = bytes(covr[0])
            return data, 'image/jpeg'  # 大部分 MP4 封面是 JPEG
    except Exception:
        pass
    return None


def _find_external_cover(file_path: Path) -> Optional[Tuple[bytes, str]]:
    """仅在需要时作为回退方案，查找同目录下的常见封面文件"""
    cover_names = [
        'cover.jpg',
        'cover.jpeg',
        'cover.png',
        'folder.jpg',
        'folder.jpeg',
        'folder.png',
        'front.jpg',
        'front.jpeg',
        'front.png',
    ]
    for name in cover_names:
        cover_path = file_path.parent / name
        if cover_path.exists():
            mime = 'image/jpeg' if cover_path.suffix.lower() in ['.jpg', '.jpeg'] else 'image/png'
            try:
                return cover_path.read_bytes(), mime
            except OSError:
                continue
    return None


@lru_cache
def get_audio_metadata(path: Path) -> dict[str, Any]:
    try:
        audio = MutagenFile(path)
        if audio is None:
            return {}

        # 处理无标签或标签为空的情况
        tags = getattr(audio, 'tags', None)
        title = path.stem
        artist = 'Unknown Artist'
        album = ''

        if tags is not None:
            title = _first_value(tags.get('TIT2') or tags.get('title') or path.stem)
            artist = _first_value(tags.get('TPE1') or tags.get('artist') or 'Unknown Artist')
            album = _first_value(tags.get('TALB') or tags.get('album') or '')

        artwork_data: Optional[bytes] = None
        artwork_mime: Optional[str] = None

        # 根据文件类型提取内嵌封面
        if isinstance(audio, FLAC):
            # 优先使用 audio.pictures（最可靠）
            cover = _extract_cover_from_flac_pictures(audio)
            if cover:
                artwork_data, artwork_mime = cover
            else:
                # 如果 pictures 为空，再尝试从 tags 中找（可能某些版本仍存在）
                if tags is not None:
                    cover = _extract_cover_from_flac(tags)  # 注意这个函数需要存在，我们稍后定义
                    if cover:
                        artwork_data, artwork_mime = cover
        elif hasattr(tags, 'getall'):  # ID3 (MP3)
            cover = _extract_cover_from_id3(tags)
            if cover:
                artwork_data, artwork_mime = cover
        elif isinstance(audio, MP4):
            cover = _extract_cover_from_mp4(tags)
            if cover:
                artwork_data, artwork_mime = cover

        # 如果内嵌封面仍然没有，才尝试外部封面
        if artwork_data is None:
            external = _find_external_cover(path)
            if external:
                artwork_data, artwork_mime = external

        return {
            'title': title,
            'artist': artist,
            'album': album,
            'artwork_mime': artwork_mime,
            'artwork_data': artwork_data,
        }
    except Exception as e:
        logger.warning(f'解析元数据失败 {path}: {e}')
        # 异常时也尝试外部封面
        external = _find_external_cover(path)
        if external:
            artwork_data, artwork_mime = external
            return {
                'title': path.stem,
                'artist': 'Unknown Artist',
                'album': '',
                'artwork_mime': artwork_mime,
                'artwork_data': artwork_data,
            }
        return {}
