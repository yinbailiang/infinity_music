"""HTML 页面模板 — 声明式片段 + $var 渲染

页面片段以 .infd 命名片段声明，且**只从外部 profile 提供**（不再内置默认模板）：
片段可能引用 profile 专属静态资源（如 /static/htmx.min.js），因此整组模板
必须随 profile 一起维护。渲染使用 string.Template 的 $var 风格。
"""

from pathlib import Path
from string import Template

from infinity_data import safe_load
from pydantic import BaseModel, Field

TEMPLATE_FILE = 'page_templates.infd'


class PageTemplates(BaseModel):
    """页面模板模型 — 字段对应 page_templates.infd 顶层的命名片段"""

    home_page: str = Field(description='主页整页骨架（$app_name/$css/$js/$items）')
    home_empty: str = Field(description='无资源时的占位片段')
    home_item: str = Field(description='主页单个资源链接项（$file_id/$name）')
    dir_item: str = Field(
        description='文件夹行（htmx 惰性展开，$dir_rel/$dir_name/$dir_count）'
    )
    search_row: str = Field(description='默认置顶的虚拟搜索文件夹（含搜索框与结果容器）')
    detail_page: str = Field(
        description='详情页整页骨架（$app_name/$file_name/$css/$js/$content/$file_id）'
    )
    detail_loading: str = Field(description='详情页骨架占位，htmx 惰性加载容器（$file_id）')
    detail_video: str = Field(description='视频详情内容片段（$file_name/$file_id）')
    detail_audio: str = Field(
        description='音频详情内容片段（$cover/$meta_title/$meta_artist/$meta_album/$file_id）'
    )
    detail_cover: str = Field(description='音频封面 img 片段，指向 /cover 接口（$file_id）')
    detail_other: str = Field(description='其他文件详情内容片段（$file_name）')


def _load_fragments(path: Path) -> dict[str, str]:
    """编译一个 .infd 模板文件，仅保留值为字符串的顶层字段。"""
    if not path.is_file():
        raise RuntimeError(f'模板文件不存在: {path}')
    result = safe_load(path)
    if result.has_errors:
        details = '\n'.join(f'  {d.code}: {d.message}' for d in result.diagnostics)
        raise RuntimeError(f'模板 {path} 编译失败:\n{details}')

    value = result.value
    fragments: dict[str, str] = {}
    for name, text in value.items():
        if isinstance(text, str):
            fragments[name] = text
    return fragments


def load_page_templates(templates_dir: Path) -> PageTemplates:
    """从外部 profile 的模板目录加载**完整**页面片段集。

    No built-in fallback: fragments reference profile-specific static assets, so
    all PageTemplates fields must be declared by the profile's page_templates.infd.

    Args:
        templates_dir: profile 模板目录（必选），其下 page_templates.infd 声明全部片段。

    Returns:
        组装后的 PageTemplates

    Raises:
        RuntimeError: 文件缺失、编译失败或字段缺失/类型错误。
    """
    path = templates_dir / TEMPLATE_FILE
    fragments = _load_fragments(path)
    try:
        return PageTemplates(**fragments)
    except Exception as e:  # pydantic.ValidationError
        missing = ', '.join(sorted(set(PageTemplates.model_fields) - set(fragments)))
        hint = f'（缺失字段: {missing}）' if missing else ''
        raise RuntimeError(f'页面模板字段缺失或类型错误（{path}）{hint}: {e}') from e


class PageRenderer:
    """页面模板渲染器 — $var 风格替换（string.Template.safe_substitute）"""

    def __init__(self, templates: PageTemplates) -> None:
        self._templates = templates

    def render(self, name: str, **kwargs: str) -> str:
        """渲染命名片段；未提供的 $var 占位保留原样。

        Args:
            name: 片段名（PageTemplates 字段名）
            **kwargs: 模板变量

        Returns:
            渲染后的 HTML 文本
        """
        raw: str = getattr(self._templates, name)
        return Template(raw).safe_substitute(**kwargs)

    @property
    def raw(self) -> PageTemplates:
        """原始模板对象，供调试或直接访问"""
        return self._templates
