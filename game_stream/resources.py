"""Linux process-tree memory totals; no command lines or source URLs are read."""
from datetime import datetime, timezone
from pathlib import Path


def memory_snapshot(pid, proc=Path('/proc')):
    processes = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            processes[int(entry.name)] = (int(fields[1]), entry)
        except (OSError, ValueError, IndexError):
            continue
    included = {pid}
    while True:
        children = {number for number, (parent, _) in processes.items() if parent in included}
        if children <= included:
            break
        included |= children
    pss = swap = unavailable = 0
    for number in included:
        try:
            for line in (processes[number][1] / 'smaps_rollup').read_text().splitlines():
                if line.startswith('Pss:'):
                    pss += int(line.split()[1])
                elif line.startswith('SwapPss:'):
                    swap += int(line.split()[1])
        except (OSError, ValueError, KeyError):
            unavailable += 1
    return dict(timestamp=datetime.now(timezone.utc).isoformat(), pid=pid,
                running=pid in processes, processes=len(included & processes.keys()),
                pss_mib=round(pss / 1024, 2), swap_mib=round(swap / 1024, 2),
                unreadable_processes=unavailable)
