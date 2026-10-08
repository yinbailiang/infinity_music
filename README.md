# Infinity Music

一个简单的、免费的、音乐分发服务：扫描本地媒体目录（音频 / 视频 / 任意文件），
用浏览器浏览、搜索、播放与下载。无数据库、无构建步骤、无转码——文件以原始
字节直出，播放和下载拿到的就是磁盘上的那一份数据。

## 快速开始

需要 Python ≥ 3.14，依赖用 [uv](https://docs.astral.sh/uv/) 管理：

```bash
uv sync
uv run infmusic -c default/config.infd
```

然后打开 <http://127.0.0.1:32523>。`default/` 是一个开箱即用的 profile，自带示例媒体目录。

## 配置（config.infd）

配置是 `.infd` 文件，启动时经 schema 严格校验（非法字段 / 类型 / 越界端口都会直接报错）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `server.host` | str | 监听地址，默认 `"0.0.0.0"` |
| `server.port` | int（1–65535） | 监听端口，默认 `32523` |
| `server.reload` | bool | 开发热重载，默认 `false` |
| `media_dir` | 目录 | 媒体根目录 |
| `static_dir` | 目录 | 静态资源目录（经 `/static/...` 提供） |
| `templates_dir` | 目录 | 模板目录（其中放 `page_templates.infd`） |
| `meta_cache` | 目录（可选） | 音频元数据 / 封面缓存；可省略，随时可删（会自动重建） |

相对路径以配置文件所在目录为基准解析。

## 自定义 profile（换风格不用改代码）

profile 是一个自包含目录：配置 + 模板 + 静态资源 + 媒体库，整包可搬运。

```
my_profile/
├── config.infd                 # 指向媒体 / 静态 / 模板目录
├── templates/
│   └── page_templates.infd     # 全部页面片段（必须完整声明，见下）
├── static/                     # css / js / 图标（模板中经 /static/... 引用）
├── res/                        # 媒体库（也可以改 config 指向已有目录）
└── meta_cache/                 # 首次访问音频后自动生成
```

制作步骤：

1. `cp -r default my_profile`（或从零新建目录）
2. 编辑 `my_profile/config.infd`，把 `media_dir` / `static_dir` / `templates_dir` 指到你的目录
3. 编辑 `my_profile/templates/page_templates.infd` 与 `my_profile/static/` 定制外观
4. `uv run infmusic -c my_profile/config.infd`

### 模板契约

- 片段是 `page_templates.infd` 中的字符串字段，用 `string.Template` 的 `$var` 语法渲染；
  需要字面量 `$` 时写 `$$`（例如内联 JS 模板字符串）。
- **没有内置默认模板**：所有片段必须由 profile 声明；缺一个都会在启动时报错并列出缺失字段。
- 变量名拼错（或漏传）会得到一条日志警告，渲染输出里会按原样保留 `$var`，据此快速定位。

| 片段 | 用途 | 可用变量 |
| --- | --- | --- |
| `home_page` | 首页整页骨架 | `$app_name` `$items` |
| `search_row` | 置顶的虚拟 Search 行（搜索框 + 结果容器） | — |
| `dir_item` | 文件夹行（htmx 点击惰性展开） | `$dir_rel` `$dir_name` `$dir_count` |
| `home_item` | 单个媒体条目（缩略图/图标 + 标题 + 作者） | `$file_id` `$file_name` `$title` `$artist` `$icon` |
| `home_item_thumb` | 条目封面缩略图（经 `/cover/{id}`，有内嵌封面时替代图标） | `$file_id` |
| `icon_audio` | 音频条目回退图标（无内嵌封面时） | — |
| `icon_video` | 视频条目图标 | — |
| `icon_file` | 其他文件条目图标 | — |
| `home_empty` | 列表空态占位 | — |
| `detail_page` | 详情页整页骨架 | `$app_name` `$file_name` `$file_id` `$content` |
| `detail_loading` | 详情内容加载占位（htmx 拉取 `/fragment/{id}`） | `$file_id` |
| `detail_audio` | 音频详情内容（元数据 + 播放器） | `$cover` `$meta_title` `$meta_artist` `$meta_album` `$file_id` |
| `detail_video` | 视频详情内容 | `$file_name` `$file_id` |
| `detail_other` | 其他文件详情内容 | `$file_name` |
| `detail_cover` | 音频封面 `<img>`（指向 `/cover/{id}`） | `$file_id` |

### 交互用的 HTTP 端点

页面结构全部由服务端渲染，模板只负责外壳与样式。想保留"文件夹展开 / 实时搜索 /
惰性详情"这些交互，请在模板中保留相应的 `hx-*` 属性（可参考 `default/` 的写法）：

| 端点 | 用途 | 缓存 |
| --- | --- | --- |
| `GET /browse/{目录}` | 某一层的子文件夹 + 媒体条目片段 | no-cache |
| `GET /search?q=` | 搜索结果片段（文件名/路径 + 曲名/作者/专辑） | no-cache |
| `GET /detail/{id}` | 详情页（骨架，内容惰性加载） | 1h |
| `GET /fragment/{id}` | 详情内容片段（音频元数据 / 播放器） | 1h |
| `GET /cover/{id}` | 内嵌封面图 | immutable |
| `GET /media/{id}` | 原始文件流（播放 / 下载，支持 Range） | immutable |
| `GET /static/{路径}` | 静态资源 | 1d |

`id` 是文件内容的 SHA-256：内容不变则 URL 永久有效，文件一改自动获得新 URL，
缓存不需要任何失效逻辑。

## 播放列表（default profile 内置）

`default/static/playerlist.js` 提供纯客户端的播放列表：数据存在浏览器 IndexedDB，不经过服务端。

- 详情页底部操作栏提供全部操作（上一首 / 下一首 / 加入 / 打开列表）；抽屉内可拖拽排序、删除、清空
- 抽屉内可**拖拽排序 / 删除 / 清空**，点击条目跳播
- 详情页播完自动跳转下一首（`location.assign`，页面与 URL 一一对应，刷新/后退/分享都正确）
- 无队列时行为与原先一致（单曲循环保留）

注意：数据只存在**当前浏览器**（清理站点数据会丢失、不跨设备）；复制到其他 profile 时记得在模板里保留
`<script src="/static/playerlist.js" defer></script>` 一行。

## 开发

```bash
uv sync --group dev      # ruff / pyright / pre-commit / pytest（含测试组）
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

测试组已经配置好，但仓库当前还没有测试用例。
