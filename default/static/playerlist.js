// Infinity Music 播放列表（profile 自定义脚本，随 default/static 一起搬运）
// - 仅由详情页模板加载：所有入口都在详情页（底部操作栏 + 右侧抽屉）
// - 抽屉打开时压入一条历史：返回键 / 移动端侧滑优先收起抽屉，而非离开页面
// - 数据（曲目快照 + 队列）存 IndexedDB，以内容哈希 id 为主键：文件改名/移动不断链
// - 详情页底部操作栏（复用 .detail-icon 默认风格）提供全部入口：
//   上一首 / 下一首 / 加入 / 打开列表；抽屉可拖拽排序 / 删除 / 清空 / 点击跳播
// - 详情页播放结束（或媒体键"上一首/下一首"）→ location.assign 跳转到对应详情页：
//   页面与 URL 一一对应，刷新/后退/分享始终正确，续播交给新页面的 autoplay
// - 无队列（或当前曲不在队列）时行为与原先一致：单曲 loop 保留、无跳转
(function () {
    'use strict';

    const DB_NAME = 'infinity-music';
    const DB_VERSION = 1;
    const QUEUE_KEY = 'current';
    // 详情页 URL：/detail/{sha256}
    const ID_RE = /\/detail\/([0-9a-f]{64})/;

    // ---------------------------------------------------------------- 图标
    const SVG_QUEUE =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="8" y1="6" x2="21" y2="6"/>' +
        '<line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>' +
        '<circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>';
    const SVG_NOTE =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 18V5l12-2v13"/>' +
        '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
    const SVG_PREV =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="15 18 9 12 15 6"/></svg>';
    const SVG_NEXT =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="9 18 15 12 9 6"/></svg>';
    const SVG_PLUS =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
        'stroke-linejoin="round" aria-hidden="true" focusable="false"><line x1="12" y1="5" x2="12" y2="19"/>' +
        '<line x1="5" y1="12" x2="19" y2="12"/></svg>';

    // ----------------------------------------------------------- IndexedDB
    // stores:
    //   tracks: { id, title, artist, cover }  —— 收集时的元数据快照
    //   queue:  { key, ids: [...] }           —— 队列顺序（单条记录）
    let dbPromise = null;

    function openDB() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains('tracks')) {
                    db.createObjectStore('tracks', { keyPath: 'id' });
                }
                if (!db.objectStoreNames.contains('queue')) {
                    db.createObjectStore('queue', { keyPath: 'key' });
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    function db() {
        if (!dbPromise) {
            dbPromise = openDB();
        }
        return dbPromise;
    }

    function reqP(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    function txDone(tx) {
        return new Promise((resolve, reject) => {
            tx.oncomplete = () => resolve(undefined);
            tx.onerror = () => reject(tx.error);
            tx.onabort = () => reject(tx.error);
        });
    }

    async function idbGet(storeName, key) {
        const d = await db();
        const tx = d.transaction(storeName, 'readonly');
        return reqP(tx.objectStore(storeName).get(key));
    }

    async function idbPut(storeName, value) {
        const d = await db();
        const tx = d.transaction(storeName, 'readwrite');
        tx.objectStore(storeName).put(value);
        return txDone(tx);
    }

    async function idbGetAll(storeName) {
        const d = await db();
        const tx = d.transaction(storeName, 'readonly');
        return reqP(tx.objectStore(storeName).getAll());
    }

    async function getQueueIds() {
        const rec = await idbGet('queue', QUEUE_KEY);
        return rec && Array.isArray(rec.ids) ? rec.ids : [];
    }

    async function setQueueIds(ids) {
        await idbPut('queue', { key: QUEUE_KEY, ids });
    }

    async function addTrack(track) {
        await idbPut('tracks', track);
        const ids = await getQueueIds();
        if (!ids.includes(track.id)) {
            ids.push(track.id);
            await setQueueIds(ids);
        }
    }

    async function getTracks(ids) {
        const all = await idbGetAll('tracks');
        const map = new Map(all.map((t) => [t.id, t]));
        return ids.map((id) => map.get(id) || { id, title: id.slice(0, 10) + '…', artist: '' });
    }

    // --------------------------------------------------------------- 工具
    function currentDetailId() {
        const m = ID_RE.exec(location.pathname);
        return m ? m[1] : null;
    }

    function trackFromDetail() {
        const id = currentDetailId();
        if (!id) {
            return null;
        }
        const pick = (sel) => {
            const el = document.querySelector(sel);
            return el ? (el.textContent || '').trim() : '';
        };
        const img = document.querySelector('img.detail-cover');
        const title = pick('.detail-meta-title') || pick('.detail-title') || document.title.split(' - ')[0];
        return { id, title, artist: pick('.detail-artist'), cover: img ? img.src : '' };
    }

    function flash(btn) {
        btn.classList.add('pl-ok');
        setTimeout(() => btn.classList.remove('pl-ok'), 600);
    }

    // ----------------------------------------------------------- 样式注入
    const CSS = `
button.detail-icon { background: none; border: 0; cursor: pointer; }
button.detail-icon:disabled { opacity: 0.28; cursor: default; }
button.detail-icon.pl-ok { color: #34a853; opacity: 1; }

.pl-drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    width: 21rem;
    background: var(--bg-color);
    transform: translateX(103%);
    transition: transform 0.22s ease;
}
.pl-drawer.open { transform: translateX(0); }

.pl-head {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.7rem 0.9rem 0.5rem;
}
.pl-head-title { font-weight: 600; }
.pl-count { opacity: 0.55; font-size: 0.85em; }
.pl-clear, .pl-close {
    border: 0;
    background: none;
    color: inherit;
    cursor: pointer;
    font: inherit;
    opacity: 0.6;
    padding: 0.1em 0.35em;
    border-radius: 999px;
    transition: color 0.2s ease, opacity 0.2s ease;
}
.pl-clear:hover, .pl-close:hover { opacity: 1; color: var(--link-color); }
.pl-clear { margin-left: auto; font-size: 0.85em; }
.pl-close { font-size: 1.15em; line-height: 1; }

.pl-list {
    list-style: none;
    margin: 0;
    padding: 0 0.45rem 1rem;
    overflow-y: auto;
    flex: 1;
}
.pl-item {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.3rem 0.5rem;
    border-radius: 0.55rem;
    cursor: pointer;
}
.pl-item:hover { background: color-mix(in srgb, var(--text-color) 10%, transparent); }
.pl-item.is-current { background: color-mix(in srgb, var(--text-color) 18%, transparent); }
.pl-item.dragging { opacity: 0.45; }
.pl-index { opacity: 0.45; font-size: 0.75em; min-width: 1.5em; text-align: right; }
.pl-thumb { width: 2rem; height: 2rem; border-radius: 8px; object-fit: cover; flex: 0 0 auto; }
.pl-thumb-ico {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--link-color);
    opacity: 0.5;
}
.pl-thumb-ico svg { width: 1.1rem; height: 1.1rem; }
.pl-info { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.pl-name { font-size: 0.92em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-artist { font-size: 0.78em; opacity: 0.6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-del {
    border: 0;
    background: none;
    color: inherit;
    cursor: pointer;
    font-size: 1.05rem;
    line-height: 1;
    opacity: 0.45;
    padding: 0 0.25em;
    transition: color 0.2s ease, opacity 0.2s ease;
}
.pl-del:hover { opacity: 1; color: var(--link-color); }
.pl-empty { text-align: center; opacity: 0.5; font-size: 0.9em; margin: 2rem 1.2rem; }

@media (max-width: 768px), (pointer: coarse) {
    /* 6 个图标在窄屏下收紧间距，维持可点面积 */
    .detail-actions.pl-nav { gap: 0.9rem; }
    .detail-actions.pl-nav .detail-icon { padding: 0.4rem; }
    /* 抽屉在触屏上收窄到 80vw */
    .pl-drawer { width: 80vw; }
    .pl-item { padding: 0.45rem 0.5rem; }
    .pl-del { font-size: 1.25rem; }
}
`;

    function injectStyle() {
        if (document.getElementById('pl-style')) {
            return;
        }
        const style = document.createElement('style');
        style.id = 'pl-style';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // --------------------------------------------------------------- 抽屉
    let dragId = null;

    function ensureDrawer() {
        if (document.getElementById('pl-drawer')) {
            return;
        }
        const drawer = document.createElement('aside');
        drawer.id = 'pl-drawer';
        drawer.className = 'pl-drawer';
        drawer.innerHTML =
            '<header class="pl-head">' +
            '<span class="pl-head-title">播放列表</span><span class="pl-count"></span>' +
            '<button type="button" class="pl-clear">清空</button>' +
            '<button type="button" class="pl-close" aria-label="关闭">×</button>' +
            '</header>' +
            '<ol class="pl-list"></ol>' +
            '<p class="pl-empty">列表为空 —— 在详情页点击 + 添加</p>';

        drawer.querySelector('.pl-close').addEventListener('click', () => closeDrawer());
        drawer.querySelector('.pl-clear').addEventListener('click', async () => {
            if (window.confirm('清空播放列表？')) {
                await setQueueIds([]);
                renderDrawer();
            }
        });

        // 点击抽屉外部任意位置 → 自动收起（操作栏的"列表"按钮除外，开合由它负责）
        document.addEventListener('click', (ev) => {
            if (!drawer.classList.contains('open')) {
                return;
            }
            if (drawer.contains(ev.target) || (listBtn && listBtn.contains(ev.target))) {
                return;
            }
            closeDrawer();
        });

        // 返回键 / 移动端侧滑返回 → 优先收起已打开的抽屉（而非离开页面）
        window.addEventListener('popstate', () => {
            if (drawer.classList.contains('open')) {
                drawer.classList.remove('open');
            }
        });

        const list = drawer.querySelector('.pl-list');
        list.addEventListener('dragover', (ev) => {
            if (!dragId) {
                return;
            }
            ev.preventDefault();
            const dragging = list.querySelector('.pl-item.dragging');
            if (!dragging) {
                return;
            }
            const after = itemAfter(list, ev.clientY);
            if (after == null) {
                list.appendChild(dragging);
            } else if (after !== dragging) {
                list.insertBefore(dragging, after);
            }
        });
        list.addEventListener('drop', async (ev) => {
            ev.preventDefault();
            const order = [...list.querySelectorAll('.pl-item')].map((li) => li.dataset.id);
            dragId = null;
            await setQueueIds(order);
            renderDrawer();
        });

        document.body.appendChild(drawer);
    }

    // 打开/收起抽屉；打开时压入一条历史，使返回键/侧滑优先收起抽屉
    function openDrawer() {
        const drawer = document.getElementById('pl-drawer');
        if (!drawer || drawer.classList.contains('open')) {
            return;
        }
        renderDrawer().finally(() => {
            drawer.classList.add('open');
            if (!(history.state && history.state.plDrawer)) {
                history.pushState({ plDrawer: true }, '');
            }
        });
    }

    function closeDrawer() {
        const drawer = document.getElementById('pl-drawer');
        if (!drawer || !drawer.classList.contains('open')) {
            return;
        }
        drawer.classList.remove('open');
        if (history.state && history.state.plDrawer) {
            history.back(); // 清掉为抽屉压入的历史条目
        }
    }

    function itemAfter(container, y) {
        const items = [...container.querySelectorAll('.pl-item:not(.dragging)')];
        return (
            items.find((el) => {
                const rect = el.getBoundingClientRect();
                return y < rect.top + rect.height / 2;
            }) || null
        );
    }

    function liFor(track, index, curId) {
        const li = document.createElement('li');
        li.className = 'pl-item' + (track.id === curId ? ' is-current' : '');
        li.dataset.id = track.id;
        li.draggable = true;
        li.title = track.title || '';

        const num = document.createElement('span');
        num.className = 'pl-index';
        num.textContent = String(index + 1);

        let cover;
        if (track.cover) {
            cover = document.createElement('img');
            cover.className = 'pl-thumb';
            cover.src = track.cover;
            cover.alt = '';
            cover.loading = 'lazy';
        } else {
            cover = document.createElement('span');
            cover.className = 'pl-thumb pl-thumb-ico';
            cover.innerHTML = SVG_NOTE;
        }

        const info = document.createElement('span');
        info.className = 'pl-info';
        const name = document.createElement('span');
        name.className = 'pl-name';
        name.textContent = track.title || '未命名';
        const artist = document.createElement('span');
        artist.className = 'pl-artist';
        artist.textContent = track.artist || '';
        info.append(name, artist);

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'pl-del';
        del.title = '移除';
        del.setAttribute('aria-label', '移除');
        del.textContent = '×';
        del.addEventListener('click', async (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            const ids = await getQueueIds();
            await setQueueIds(ids.filter((x) => x !== track.id));
            renderDrawer();
        });

        li.append(num, cover, info, del);

        li.addEventListener('click', (ev) => {
            if (ev.target.closest('.pl-del')) {
                return;
            }
            if (track.id !== curId) {
                location.assign('/detail/' + track.id);
            }
        });
        li.addEventListener('dragstart', (ev) => {
            dragId = track.id;
            li.classList.add('dragging');
            ev.dataTransfer.effectAllowed = 'move';
            ev.dataTransfer.setData('text/plain', track.id);
        });
        li.addEventListener('dragend', () => {
            dragId = null;
            li.classList.remove('dragging');
        });

        return li;
    }

    async function renderDrawer() {
        const drawer = document.getElementById('pl-drawer');
        if (!drawer) {
            return;
        }
        const list = drawer.querySelector('.pl-list');
        const ids = await getQueueIds();
        const tracks = await getTracks(ids);
        const curId = currentDetailId();

        list.textContent = '';
        tracks.forEach((track, i) => list.appendChild(liFor(track, i, curId)));
        drawer.querySelector('.pl-count').textContent = ids.length > 0 ? '共 ' + ids.length + ' 首' : '';
        drawer.querySelector('.pl-empty').style.display = ids.length > 0 ? 'none' : '';
    }

    // ------------------------------------------------------- 详情页增强
    let prevBtn = null;
    let nextBtn = null;
    let listBtn = null;
    let navBusy = false;

    function makeNavButton(cls, label, svg, onClick) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'detail-icon ' + cls;
        btn.title = label;
        btn.setAttribute('aria-label', label);
        btn.innerHTML = svg;
        btn.addEventListener('click', onClick);
        return btn;
    }

    function ensureDetailNav() {
        const nav = document.querySelector('.detail-actions');
        if (!nav || nav.dataset.plNav === '1') {
            return;
        }
        nav.dataset.plNav = '1';

        const addBtn = makeNavButton('pl-add-detail', '加入播放列表', SVG_PLUS, async () => {
            const track = trackFromDetail();
            if (!track) {
                return;
            }
            await addTrack(track);
            flash(addBtn);
            renderDrawer();
            refreshNav();
            syncPlayback();
        });
        prevBtn = makeNavButton('pl-prev', '上一首', SVG_PREV, () => jumpBy(-1));
        nextBtn = makeNavButton('pl-next', '下一首', SVG_NEXT, () => jumpBy(1));
        listBtn = makeNavButton('pl-list-btn', '打开播放列表', SVG_QUEUE, () => {
            const drawer = document.getElementById('pl-drawer');
            if (drawer.classList.contains('open')) {
                closeDrawer();
            } else {
                openDrawer();
            }
        });

        // 插到 nav 开头，与原有下载/返回同排：[prev][next][add][list][download][back]
        nav.classList.add('pl-nav');
        nav.prepend(listBtn);
        nav.prepend(addBtn);
        nav.prepend(nextBtn);
        nav.prepend(prevBtn);
        refreshNav();
    }

    async function refreshNav() {
        if (!prevBtn || !nextBtn) {
            return;
        }
        const id = currentDetailId();
        const ids = await getQueueIds();
        const i = id ? ids.indexOf(id) : -1;
        prevBtn.disabled = !(i > 0);
        nextBtn.disabled = !(i >= 0 && i < ids.length - 1);
    }

    async function jumpBy(delta) {
        const id = currentDetailId();
        if (!id || navBusy) {
            return;
        }
        const ids = await getQueueIds();
        const i = ids.indexOf(id);
        if (i < 0) {
            return;
        }
        const j = i + delta;
        if (j < 0 || j >= ids.length) {
            return;
        }
        navBusy = true;
        location.assign('/detail/' + ids[j]);
    }

    // 详情页媒体：当前曲在队列中则取消单曲 loop（播完触发 ended 交给队列跳转）
    // 可重复调用：中途加入队列后立即进入"队列模式"（loop=false），无需刷新
    async function syncPlayback() {
        const id = currentDetailId();
        if (!id) {
            return;
        }
        const ids = await getQueueIds();
        const inQueue = ids.includes(id);
        document.querySelectorAll('.detail-media').forEach((media) => {
            if (media.dataset.plHook !== '1') {
                media.dataset.plHook = '1';
                media.addEventListener('ended', () => jumpBy(1));
            }
            if (inQueue) {
                media.loop = false;
            }
        });
    }

    function setupMediaSession() {
        if (!('mediaSession' in navigator)) {
            return;
        }
        navigator.mediaSession.setActionHandler('nexttrack', () => jumpBy(1));
        navigator.mediaSession.setActionHandler('previoustrack', () => jumpBy(-1));
    }

    // ---------------------------------------------------------------- 初始化
    function init() {
        injectStyle();
        ensureDrawer();
        if (currentDetailId()) {
            ensureDetailNav();
            syncPlayback();
            setupMediaSession();
        }
        renderDrawer();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init());
    } else {
        init();
    }

    document.addEventListener('htmx:load', () => init());
})();
