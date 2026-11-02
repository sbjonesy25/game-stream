// Runs in each frame before the page's player starts. The page continues to
// fetch/unwrap its own media; only complete clear MPEG-TS fragments leave it.
(() => {
    const patched = new WeakSet();
    const ids = new WeakMap();
    let serial = 0, pending = 0, connected = false, scanTicks = 0;
    const rejected = new WeakSet();
    const configured = new WeakSet();
    function boundPlayer(hls) {
        if (!hls?.config) return;
        // Hls.js otherwise permits an infinite retained back buffer. Apply before
        // fragLoaded is delivered to the player's buffering controllers.
        hls.config.backBufferLength = 10;
        hls.config.liveBackBufferLength = 10;
        hls.config.maxBufferLength = 12;
        hls.config.maxMaxBufferLength = 24;
        hls.config.maxBufferSize = 16 * 1024 * 1024;
        const cap = globalThis.__game_stream_max_height || 0;
        if (cap && hls.levels?.length && !configured.has(hls)) {
            const allowed = hls.levels.map((level, index) => ({height: level.height, index}))
                .filter(level => level.height > 0 && level.height <= cap);
            // Providers without a lower rendition keep their lowest available level.
            hls.autoLevelCapping = allowed.length ? allowed[allowed.length - 1].index : 0;
            configured.add(hls);
        }
    }
    function forward(hls, event, data) {
        boundPlayer(hls);
        if (event !== 'hlsFragLoaded' || !data?.frag || !data.payload) return;
        const frag = data.frag, media = hls.media;
        if (frag.type !== 'main' || !Number.isFinite(frag.sn) ||
            !media || media.mediaKeys || rejected.has(hls) || pending >= 2) return;
        if (frag.decryptdata?.method && frag.decryptdata.method !== 'NONE') return;
        const bytes = new Uint8Array(data.payload.buffer || data.payload,
            data.payload.byteOffset || 0, data.payload.byteLength);
        if (bytes.length < 940 || bytes.length > 16 * 1024 * 1024 || bytes.length % 188) return;
        for (let i = 0; i < 5; i++) if (bytes[i * 188] !== 0x47) return;
        if (!ids.has(hls)) ids.set(hls, ++serial);
        media.muted = true; // VLC owns audible output, even while this page stays alive.
        let binary = '';
        for (let i = 0; i < bytes.length; i += 32768)
            binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
        pending++;
        Promise.resolve(globalThis.__game_stream_fragment({
            player: ids.get(hls), sequence: frag.sn, cc: frag.cc || 0,
            duration: frag.duration, bytes: btoa(binary)
        })).then(accepted => {
            if (accepted === false) rejected.add(hls);
            else if (accepted === true) connected = true;
        })
            .catch(() => {}).finally(() => pending--);
    }
    function patch(Hls) {
        const proto = Hls?.prototype;
        if (!proto || typeof proto.trigger !== 'function' || patched.has(proto)) return;
        patched.add(proto);
        const trigger = proto.trigger;
        proto.trigger = function(event, data, ...rest) {
            // Copy before a player transfers the buffer to its transmux worker.
            try { forward(this, event, data); } catch (_) {}
            return trigger.call(this, event, data, ...rest);
        };
    }
    function scan() {
        if (connected && ++scanTicks % 10) return;
        try { patch(globalThis.Hls); } catch (_) {}
        try { if (typeof Hls !== 'undefined') patch(Hls); } catch (_) {}
        try { patch(globalThis.Clappr?.HLS?.HLSJS); } catch (_) {}
        try {
            // Clappr pages can use a global lexical `let player`, not window.player.
            const p = typeof player !== 'undefined' ? player : globalThis.player;
            patch(p?.core?.getCurrentPlayback?.()?._hls?.constructor);
        } catch (_) {}
        for (const video of document.querySelectorAll('video')) video.muted = true;
    }
    globalThis.__game_stream_scan = scan;
    scan();
    setInterval(scan, 100);
})();
