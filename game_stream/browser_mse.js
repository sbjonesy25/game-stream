// Observe the clear fragmented MP4 already submitted to MediaSource. Never read
// decoded video frames, export keys, or interfere with the page's append calls.
(() => {
    const sources = new WeakMap(), buffers = new WeakMap();
    let serial = 0, pending = 0;
    function sourceInfo(source) {
        if (!sources.has(source)) sources.set(source, {id: ++serial, url: '', rejected: false, audio: false});
        return sources.get(source);
    }
    const create = URL.createObjectURL;
    URL.createObjectURL = function(value, ...args) {
        const url = create.call(this, value, ...args);
        if (globalThis.MediaSource && value instanceof MediaSource) sourceInfo(value).url = url;
        return url;
    };
    if (!globalThis.MediaSource || !globalThis.SourceBuffer) return;
    const add = MediaSource.prototype.addSourceBuffer;
    MediaSource.prototype.addSourceBuffer = function(mime, ...args) {
        const buffer = add.call(this, mime, ...args);
        if (/^(video|audio)\/mp4\s*(?:;|$)/i.test(mime)) {
            const source = sourceInfo(this);
            if (/^audio\//i.test(mime)) source.audio = true;
            buffers.set(buffer, {source, id: ++serial, init: null, sent: false,
                                 ftyp: null, remainder: new Uint8Array(), parts: [], size: 0, moof: false});
        }
        return buffer;
    };
    function base64(bytes) {
        let binary = '';
        for (let i = 0; i < bytes.length; i += 32768)
            binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
        return btoa(binary);
    }
    function join(parts) {
        const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
        let offset = 0;
        for (const part of parts) {result.set(part, offset); offset += part.length;}
        return result;
    }
    function send(meta, bytes) {
        if (!meta.init || pending >= 2) return;
        const videos = [...document.querySelectorAll('video')];
        const video = videos.find(v => (v.currentSrc || v.src) === meta.source.url);
        if (!video || video.paused || video.ended || video.mediaKeys || video.getBoundingClientRect().width < 1) return;
        // Skip tiny/inactive ad videos when a larger playing video shares the frame.
        const area = v => {const r = v.getBoundingClientRect(); return r.width * r.height;};
        if (videos.some(v => v !== video && !v.paused && !v.ended && area(v) > area(video))) return;
        video.muted = true;
        const initialization = meta.sent ? null : meta.init;
        pending++;
        Promise.resolve(globalThis.__game_stream_mse({player: meta.source.id, track: meta.id,
            bytes: base64(bytes), init: initialization, audio: meta.source.audio})).then(accepted => {
                if (accepted === false) meta.source.rejected = true;
                if (accepted === true && initialization === meta.init) meta.sent = true;
            }).catch(() => {}).finally(() => pending--);
    }
    function inspect(meta, value) {
        if (typeof globalThis.__game_stream_mse !== 'function' || meta.source.rejected) return;
        const incoming = new Uint8Array(value.buffer || value, value.byteOffset || 0, value.byteLength);
        const limit = 16 * 1024 * 1024;
        if (incoming.length + meta.remainder.length > limit) {
            meta.remainder = new Uint8Array(); meta.parts = []; meta.size = 0; meta.moof = false;
            return;
        }
        const bytes = meta.remainder.length ? join([meta.remainder, incoming]) : incoming;
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        let offset = 0;
        while (offset + 8 <= bytes.length) {
            let size = view.getUint32(offset), header = 8;
            if (size === 1) {
                if (offset + 16 > bytes.length) break;
                size = Number(view.getBigUint64(offset + 8)); header = 16;
            }
            if (size < header || size > limit) {
                meta.remainder = new Uint8Array(); meta.parts = []; meta.size = 0; meta.moof = false;
                return;
            }
            if (offset + size > bytes.length) break;
            const name = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
            const box = bytes.subarray(offset, offset + size);
            offset += size;
            if (name === 'ftyp') {
                if (box.length <= 1024 * 1024) meta.ftyp = box.slice();
                continue;
            }
            if (name === 'moov') {
                if (box.length + (meta.ftyp?.length || 0) <= 1024 * 1024) {
                    meta.init = base64(meta.ftyp ? join([meta.ftyp, box]) : box);
                    meta.sent = false;
                }
                meta.parts = []; meta.size = 0; meta.moof = false;
                continue;
            }
            if (name === 'moof') {
                if (meta.moof) {meta.parts = []; meta.size = 0;}
                meta.moof = true;
            }
            if (meta.size + box.length > limit) {
                meta.parts = []; meta.size = 0; meta.moof = false;
                continue;
            }
            meta.parts.push(box.slice()); meta.size += box.length;
            if (name === 'mdat') {
                if (meta.moof) send(meta, join(meta.parts));
                meta.parts = []; meta.size = 0; meta.moof = false;
            }
        }
        meta.remainder = bytes.subarray(offset).slice();
    }
    const append = SourceBuffer.prototype.appendBuffer;
    SourceBuffer.prototype.appendBuffer = function(value, ...args) {
        try {const meta = buffers.get(this); if (meta) inspect(meta, value);} catch (_) {}
        return append.call(this, value, ...args);
    };
})();
