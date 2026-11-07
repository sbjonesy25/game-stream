"""Small bounded ISO-BMFF metadata reader; no media decoding or file access."""
import struct


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    count = 0
    while start < end:
        if end - start < 8 or count >= 4096:
            raise ValueError('Invalid MP4 box')
        size, name = struct.unpack_from('>I4s', data, start)
        header = 8
        if size == 1:
            if end - start < 16:
                raise ValueError('Invalid MP4 extended box')
            size = struct.unpack_from('>Q', data, start + 8)[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or size > end - start:
            raise ValueError('Incomplete MP4 box')
        yield name, start + header, start + size
        start += size
        count += 1


def number(data, offset, width=4):
    if offset < 0 or offset + width > len(data):
        raise ValueError('Truncated MP4 field')
    return int.from_bytes(data[offset:offset + width], 'big')


def init_tracks(data):
    tracks, defaults = {}, {}
    if len(data) > 1024 * 1024 or any(tag in data for tag in (b'pssh', b'encv', b'enca')):
        raise ValueError('Unsupported MP4 initialization')
    for kind, begin, end in boxes(data):
        if kind != b'moov':
            continue
        for name, a, b in boxes(data, begin, end):
            if name == b'mvex':
                for tag, c, d in boxes(data, a, b):
                    if tag == b'trex':
                        payload = data[c:d]
                        defaults[number(payload, 4)] = number(payload, 12)
            if name != b'trak':
                continue
            track_id = scale = None
            handler = None
            for tag, c, d in boxes(data, a, b):
                payload = data[c:d]
                if tag == b'tkhd':
                    track_id = number(payload, 20 if number(payload, 0, 1) else 12)
                if tag == b'mdia':
                    for tag2, e, f in boxes(data, c, d):
                        payload2 = data[e:f]
                        if tag2 == b'mdhd':
                            scale = number(payload2, 20 if number(payload2, 0, 1) else 12)
                        elif tag2 == b'hdlr':
                            handler = payload2[8:12]
            if track_id and scale and handler in (b'vide', b'soun'):
                tracks[track_id] = dict(scale=scale, kind='video' if handler == b'vide' else 'audio')
    if not tracks or len(tracks) > 8:
        raise ValueError('MP4 has no supported media tracks')
    for track_id, track in tracks.items():
        track['default_duration'] = defaults.get(track_id, 0)
    return tracks


def fragment_timing(data, tracks):
    timings = []
    has_media = False
    for kind, begin, end in boxes(data):
        if kind == b'mdat':
            has_media = end > begin
        if kind != b'moof':
            continue
        for name, a, b in boxes(data, begin, end):
            if name != b'traf':
                continue
            track_id = None
            duration = None
            timestamp = None
            runs = []
            for tag, c, d in boxes(data, a, b):
                payload = data[c:d]
                if tag == b'tfhd':
                    flags = number(payload, 1, 3)
                    track_id = number(payload, 4)
                    offset = 8 + (8 if flags & 1 else 0) + (4 if flags & 2 else 0)
                    if flags & 8:
                        duration = number(payload, offset)
                elif tag == b'tfdt':
                    timestamp = number(payload, 4, 8 if number(payload, 0, 1) else 4)
                elif tag == b'trun':
                    runs.append(payload)
            track = tracks.get(track_id)
            if not track or timestamp is None:
                continue
            default = track['default_duration'] if duration is None else duration
            total = 0
            for run in runs:
                flags = number(run, 1, 3)
                count = number(run, 4)
                offset = 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
                stride = 4 * sum(bool(flags & flag) for flag in (0x100, 0x200, 0x400, 0x800))
                if count > 1000000 or offset + count * stride > len(run):
                    raise ValueError('Invalid MP4 sample run')
                if flags & 0x100:
                    total += sum(number(run, offset + i * stride) for i in range(count))
                else:
                    total += count * default
            if total:
                timings.append((track['kind'] == 'video', timestamp / track['scale'], total / track['scale']))
    if not has_media or not timings:
        raise ValueError('MP4 fragment lacks media timing')
    _, start, duration = max(timings)  # Use video timing for multiplexed audio/video.
    if not 0 < duration <= 60:
        raise ValueError('Invalid MP4 fragment duration')
    return start, duration
