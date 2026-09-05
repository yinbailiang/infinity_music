// Infinity Music my_profile 前端脚本
// - Media Session + 播放位置保存（localStorage）
// - 自绘极简音频控制条（播放/暂停 + 进度 + 时间）
// - 兼容 htmx：内容片段动态注入后，通过 htmx:load 重新扫描并幂等绑定
(function () {
    'use strict';

    // ------------------------------------------------------------- 工具
    function fmtTime(sec) {
        if (!isFinite(sec) || sec < 0) return '0:00';
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return `${m}:${String(s).padStart(2, '0')}`;
    }

    // 播放位置保存 / 恢复（媒体通用）
    function bindProgress(media) {
        if (media.dataset.infProgress === '1') return;
        media.dataset.infProgress = '1';

        const storageKey = `media-progress:${media.src}`;

        function saveProgress() {
            if (!media.currentTime || (media.duration && media.currentTime === media.duration)) return;
            localStorage.setItem(storageKey, media.currentTime);
        }

        function restoreProgress() {
            const saved = localStorage.getItem(storageKey);
            if (saved) {
                const time = parseFloat(saved);
                if (!isNaN(time) && time > 0) {
                    media.currentTime = time;
                }
            }
        }

        let lastSave = 0;
        function throttledSave() {
            const now = Date.now();
            if (now - lastSave > 3000) {
                saveProgress();
                lastSave = now;
            }
        }

        media.addEventListener('loadedmetadata', restoreProgress);
        media.addEventListener('timeupdate', throttledSave);
        media.addEventListener('play', throttledSave);   // 开始播放时也记录
        media.addEventListener('pause', saveProgress);  // 暂停时立即保存
        media.addEventListener('ended', saveProgress);  // 播放结束时保存
    }

    // 视频：Media Session（原生 controls），封面取视频帧
    function bindVideoMediaSession(video) {
        if (video.dataset.infVideo === '1') return;
        video.dataset.infVideo = '1';

        function setMediaMetadata() {
            if (!video.videoWidth || !video.videoHeight) return; // 等待拿到尺寸

            const canvas = document.createElement('canvas');
            const scale = Math.min(1, 512 / video.videoWidth);
            canvas.width = Math.floor(video.videoWidth * scale);
            canvas.height = Math.floor(video.videoHeight * scale);
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

            let artworkUrl;
            try {
                artworkUrl = canvas.toDataURL('image/jpeg', 0.7);
            } catch (e) {
                console.warn('无法截取视频帧（可能跨源限制）', e);
                return;
            }

            if ('mediaSession' in navigator) {
                const title = document.querySelector('.detail-title')?.textContent || '视频播放';
                navigator.mediaSession.metadata = new MediaMetadata({
                    title: title,
                    artist: 'Infinity Music',
                    artwork: [
                        { src: artworkUrl, sizes: `${canvas.width}x${canvas.height}`, type: 'image/jpeg' }
                    ]
                });
            }
        }

        video.addEventListener('canplay', setMediaMetadata);
        if (video.readyState >= 2) {
            setMediaMetadata();
        }
    }

    // 音频：Media Session，元数据取自详情页 DOM
    function bindAudioMediaSession(audio) {
        if (audio.dataset.infAudio === '1') return;
        audio.dataset.infAudio = '1';

        function setAudioMetadata() {
            if (!('mediaSession' in navigator)) return;

            const titleEl = document.querySelector('.detail-meta-title') || document.querySelector('.detail-title');
            const title = titleEl ? titleEl.textContent.trim() : 'Infinity Music';

            const artistEl = document.querySelector('.detail-artist');
            const artist = artistEl ? artistEl.textContent.trim() : 'Infinity Music';

            const albumEl = document.querySelector('.detail-album');
            const album = albumEl ? albumEl.textContent.trim() : '';

            let artwork = [];
            const coverImg = document.querySelector('.detail-cover');
            if (coverImg && coverImg.src) {
                let sizes = '512x512';
                if (coverImg.naturalWidth && coverImg.naturalHeight) {
                    sizes = `${coverImg.naturalWidth}x${coverImg.naturalHeight}`;
                }
                let type = 'image/jpeg';
                const m = coverImg.src.match(/^data:([^;]+)/);
                if (m) {
                    type = m[1];
                } else if (coverImg.src.endsWith('.png')) {
                    type = 'image/png';
                }
                artwork = [{ src: coverImg.src, sizes: sizes, type: type }];
            }

            navigator.mediaSession.metadata = new MediaMetadata({
                title: title,
                artist: artist,
                album: album,
                artwork: artwork
            });
        }

        audio.addEventListener('canplay', setAudioMetadata);
        if (audio.readyState >= 1) {
            setAudioMetadata();
        }
    }

    // 自绘极简控制条：仅作用于 .player 内的音频
    function bindPlayer(player) {
        if (player.dataset.infPlayer === '1') return;
        player.dataset.infPlayer = '1';

        const audio = player.closest('.player-wrap')?.querySelector('audio');
        if (!audio) return;

        const toggle = player.querySelector('.player-toggle');
        const track = player.querySelector('.player-track');
        const fill = player.querySelector('.player-fill');
        const time = player.querySelector('.player-time');

        function syncState() {
            const playing = !audio.paused && !audio.ended;
            player.classList.toggle('is-playing', playing);
            const label = playing ? '暂停' : '播放';
            toggle.setAttribute('aria-label', label);
            toggle.setAttribute('title', label);
        }

        function syncTime() {
            const dur = audio.duration;
            const cur = audio.currentTime;
            fill.style.width = dur ? `${(cur / dur) * 100}%` : '0%';
            time.textContent = `${fmtTime(cur)} / ${fmtTime(dur)}`;
        }

        toggle.addEventListener('click', () => {
            if (audio.paused) {
                audio.play().catch(() => {});
            } else {
                audio.pause();
            }
        });

        // 拖动 / 点击进度条：按下即跳转，按住拖动实时跟手，松开落点
        let dragging = false;

        function seekFromEvent(e) {
            const rect = track.getBoundingClientRect();
            const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
            const dur = audio.duration;
            if (!dur) return;
            fill.style.width = `${ratio * 100}%`;
            time.textContent = `${fmtTime(ratio * dur)} / ${fmtTime(dur)}`;
            audio.currentTime = ratio * dur;
        }

        track.addEventListener('pointerdown', (e) => {
            dragging = true;
            track.setPointerCapture(e.pointerId);
            seekFromEvent(e);
        });

        track.addEventListener('pointermove', (e) => {
            if (dragging) seekFromEvent(e);
        });

        function endDrag(e) {
            if (!dragging) return;
            dragging = false;
            try {
                track.releasePointerCapture(e.pointerId);
            } catch (_) {
                /* 指针可能已丢失，忽略 */
            }
        }

        track.addEventListener('pointerup', endDrag);
        track.addEventListener('pointercancel', endDrag);

        audio.addEventListener('play', syncState);
        audio.addEventListener('pause', syncState);
        audio.addEventListener('ended', syncState);
        audio.addEventListener('timeupdate', syncTime);
        audio.addEventListener('loadedmetadata', () => { syncTime(); syncState(); });

        syncTime();
        syncState();
    }

    // 扫描（部分）文档并绑定；所有绑定幂等，htmx 重复注入不会重复挂载
    function init(scope) {
        const root = (scope && scope.nodeType === 1) ? scope : document;
        root.querySelectorAll('video').forEach((v) => {
            bindProgress(v);
            bindVideoMediaSession(v);
        });
        root.querySelectorAll('audio').forEach((a) => {
            bindProgress(a);
            bindAudioMediaSession(a);
        });
        root.querySelectorAll('.player').forEach((p) => bindPlayer(p));
    }

    // 页面初次加载
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init(document));
    } else {
        init(document);
    }

    // htmx 动态注入的内容
    document.addEventListener('htmx:load', (e) => {
        const elt = e.detail && e.detail.elt ? e.detail.elt : document;
        init(elt);
    });

    // 页面离开前保存所有媒体进度
    window.addEventListener('beforeunload', () => {
        document.querySelectorAll('audio, video').forEach((media) => {
            if (media.currentTime && !(media.duration && media.currentTime === media.duration)) {
                localStorage.setItem(`media-progress:${media.src}`, media.currentTime);
            }
        });
    });
})();
