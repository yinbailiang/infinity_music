document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('video').forEach(video => {

        function setMediaMetadata() {
            if (!video.videoWidth || !video.videoHeight) return; // 如果视频尺寸未知，等待

            // 截取当前帧
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
        // 如果视频已经加载完成（例如从缓存）
        if (video.readyState >= 2) {
            setMediaMetadata();
        }
    });

    // ---------- 音频 Media Session ----------
    document.querySelectorAll('audio').forEach(audio => {
        function setAudioMetadata() {
            if (!('mediaSession' in navigator)) {
                console.log('浏览器不支持 Media Session API');
                return;
            }

            // 标题：优先从音频详情页的 .detail-meta-title 获取，否则尝试 .detail-title
            const titleEl = document.querySelector('.detail-meta-title') || document.querySelector('.detail-title');
            const title = titleEl ? titleEl.textContent.trim() : 'Infinity Music';

            // 艺术家
            const artistEl = document.querySelector('.detail-artist');
            const artist = artistEl ? artistEl.textContent.trim() : 'Infinity Music';

            // 专辑
            const albumEl = document.querySelector('.detail-album');
            const album = albumEl ? albumEl.textContent.trim() : '';

            // 封面
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
});
