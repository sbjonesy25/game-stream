// Runs in each frame before the page's player starts. The page continues to
// fetch/unwrap its own media; only complete clear MPEG-TS fragments leave it.
(() => {
    const patched = new WeakSet();
    const ids = new WeakMap();
    let serial = 0, pending = 0;
    function forward(hls, event, data) {
        if (event !== 'hlsFragLoaded' || !data?.frag || !data.payload) return;
        const frag = data.frag, media = hls.media;
        if (frag.type !== 'main' || !Number.isFinite(frag.sn) ||
            !media || media.mediaKeys || pending >= 3) return;
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
        Promise.resolve(globalThis.__streamwall_fragment({
            player: ids.get(hls), sequence: frag.sn, cc: frag.cc || 0,
            duration: frag.duration, bytes: btoa(binary)
        })).catch(() => {}).finally(() => pending--);
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
    setInterval(() => {
        try { patch(globalThis.Hls); } catch (_) {}
        try { if (typeof Hls !== 'undefined') patch(Hls); } catch (_) {}
        try { patch(globalThis.Clappr?.HLS?.HLSJS); } catch (_) {}
        try {
            // Clappr pages can use a global lexical `let player`, not window.player.
            const p = typeof player !== 'undefined' ? player : globalThis.player;
            patch(p?.core?.getCurrentPlayback?.()?._hls?.constructor);
        } catch (_) {}
        for (const video of document.querySelectorAll('video')) video.muted = true;
    }, 100);
})();
