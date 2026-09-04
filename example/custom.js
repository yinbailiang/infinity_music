document.addEventListener('DOMContentLoaded', function () {
    // ---------- 视频 Media Session + 播放位置保存 ----------
    document.querySelectorAll('video').forEach(video => {
        // 播放位置保存相关
        const storageKey = `media-progress:${video.src}`;

        function saveProgress() {
            if (!video.currentTime || video.duration && video.currentTime === video.duration) return;
            localStorage.setItem(storageKey, video.currentTime);
        }

        // 恢复播放位置
        function restoreProgress() {
            const saved = localStorage.getItem(storageKey);
            if (saved) {
                const time = parseFloat(saved);
                if (!isNaN(time) && time > 0) {
                    video.currentTime = time;
                }
            }
        }

        // 节流保存（每 3 秒保存一次）
        let lastSave = 0;
        function throttledSave() {
            const now = Date.now();
            if (now - lastSave > 3000) {
                saveProgress();
                lastSave = now;
            }
        }

        video.addEventListener('loadedmetadata', restoreProgress);
        video.addEventListener('timeupdate', throttledSave);
        video.addEventListener('play', throttledSave); // 开始播放时也记录
        video.addEventListener('pause', saveProgress); // 暂停时立即保存
        video.addEventListener('ended', saveProgress); // 播放结束时保存

        // --- 原有视频 Media Session 逻辑 ---
        function setMediaMetadata() {
            if (!video.videoWidth || !video.videoHeight) return; // 如果视频尺寸未知，等待

            const canvas = document.createElement('canvas');
            const scale = Math.min(1, 512 / video.videoWidth); // 限制最大宽度512px
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
                console.log('Media Session 元数据已设置');
            } else {
                console.log('浏览器不支持 Media Session API');
            }
        }

        video.addEventListener('canplay', setMediaMetadata);
        if (video.readyState >= 2) {
            setMediaMetadata();
        }
    });

    // ---------- 音频 Media Session + 播放位置保存 ----------
    document.querySelectorAll('audio').forEach(audio => {
        // 播放位置保存相关
        const storageKey = `media-progress:${audio.src}`;

        function saveProgress() {
            if (!audio.currentTime || audio.duration && audio.currentTime === audio.duration) return;
            localStorage.setItem(storageKey, audio.currentTime);
        }

        function restoreProgress() {
            const saved = localStorage.getItem(storageKey);
            if (saved) {
                const time = parseFloat(saved);
                if (!isNaN(time) && time > 0) {
                    audio.currentTime = time;
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

        audio.addEventListener('loadedmetadata', restoreProgress);
        audio.addEventListener('timeupdate', throttledSave);
        audio.addEventListener('play', throttledSave);
        audio.addEventListener('pause', saveProgress);
        audio.addEventListener('ended', saveProgress);

        // --- 原有音频 Media Session 逻辑 ---
        function setAudioMetadata() {
            if (!('mediaSession' in navigator)) {
                console.log('浏览器不支持 Media Session API');
                return;
            }

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
                if (coverImg.src.startsWith('data:')) {
                    const match = coverImg.src.match(/^data:([^;]+)/);
                    if (match) type = match[1];
                } else if (coverImg.src.endsWith('.png')) {
                    type = 'image/png';
                }
                artwork = [{
                    src: coverImg.src,
                    sizes: sizes,
                    type: type
                }];
            }

            navigator.mediaSession.metadata = new MediaMetadata({
                title: title,
                artist: artist,
                album: album,
                artwork: artwork
            });
            console.log('音频 Media Session 元数据已设置', { title, artist, album, artwork });
        }

        audio.addEventListener('canplay', setAudioMetadata);
        if (audio.readyState >= 1) {
            setAudioMetadata();
        }
    });

    // ---------- 页面离开前保存所有媒体进度 ----------
    window.addEventListener('beforeunload', () => {
        document.querySelectorAll('audio, video').forEach(media => {
            if (media.currentTime && !(media.duration && media.currentTime === media.duration)) {
                localStorage.setItem(`media-progress:${media.src}`, media.currentTime);
            }
        });
    });
});