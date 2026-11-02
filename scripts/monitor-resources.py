#!/usr/bin/env python3
"""Sample Game Stream + browser/decoder descendants without logging stream URLs."""
import argparse
from pathlib import Path
import sys
from time import monotonic, sleep
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game_stream.config import write_json
from game_stream.resources import memory_snapshot, diagnostic_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True, help='Game Stream Python process ID')
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--output', type=Path, default=Path('game-stream-memory.json'))
    args = parser.parse_args()
    if args.pid <= 0 or args.seconds <= 0 or args.interval <= 0:
        parser.error('PID and durations must be positive')
    samples = []
    metadata = diagnostic_metadata()
    initial_start = None
    start = monotonic()
    try:
        while True:
            sample = memory_snapshot(args.pid)
            if initial_start is None:
                initial_start = sample['root_start_ticks']
            sample['root_process_changed'] = (initial_start is not None and sample['root_start_ticks'] is not None
                                               and sample['root_start_ticks'] != initial_start)
            sample['elapsed_seconds'] = round(monotonic() - start, 1)
            samples.append(sample)
            write_json(args.output, {'metadata': metadata, 'samples': samples})
            print(f'{sample["elapsed_seconds"]}s: {sample["pss_mib"]} MiB PSS, '
                  f'{sample["swap_mib"]} MiB swap, {sample["processes"]} processes', flush=True)
            if not sample['running'] or sample['root_process_changed'] or monotonic() - start >= args.seconds:
                break
            sleep(min(args.interval, max(0, args.seconds - (monotonic() - start))))
    except KeyboardInterrupt:
        pass
    print('Saved private memory report: ' + str(args.output))


if __name__ == '__main__':
    main()
