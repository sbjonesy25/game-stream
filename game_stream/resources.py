"""Linux diagnostics using whitelisted counters; no command lines or stream data."""
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


def _counters(path):
    values = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                values[parts[0].rstrip(':')] = int(parts[1])
    except OSError:
        pass
    return values


def _role(pid, root_pid, directory):
    if pid == root_pid:
        return 'app_vlc'
    if pid == os.getpid():
        return 'diagnostic_monitor'
    try:
        name = (directory / 'comm').read_text().strip().lower()
    except OSError:
        return 'other'
    if 'crashpad' in name:
        return 'browser_helper'
    if name in ('chromium', 'chromium-browse', 'chrome', 'brave'):
        return 'browser'
    if name in ('node', 'mainthread'):
        return 'automation'
    if name == 'ffmpeg':
        return 'decoder'
    return 'other'


def system_memory(proc=Path('/proc')):
    values = _counters(proc / 'meminfo')
    keys = {'MemTotal': 'total_mib', 'MemAvailable': 'available_mib', 'Cached': 'cached_mib',
            'SReclaimable': 'reclaimable_mib', 'Shmem': 'shared_mib',
            'SwapTotal': 'swap_total_mib', 'SwapFree': 'swap_free_mib'}
    result = {name: round(values[key] / 1024, 2) for key, name in keys.items() if key in values}
    if 'SwapTotal' in values and 'SwapFree' in values:
        result['swap_used_mib'] = round((values['SwapTotal'] - values['SwapFree']) / 1024, 2)
    vm = _counters(proc / 'vmstat')
    result['swap_io_pages'] = {key: vm[key] for key in ('pswpin', 'pswpout') if key in vm}
    pressure = {}
    try:
        for line in (proc / 'pressure/memory').read_text().splitlines():
            parts = line.split()
            if parts and parts[0] in ('some', 'full'):
                counters = {}
                for item in parts[1:]:
                    key, value = item.split('=', 1)
                    if key in ('avg10', 'avg60', 'avg300', 'total'):
                        counters[key] = float(value)
                pressure[parts[0]] = counters
    except (OSError, ValueError):
        pass
    result['pressure'] = pressure
    return result


def memory_snapshot(pid, proc=Path('/proc')):
    processes = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            processes[int(entry.name)] = (int(fields[1]), entry, fields)
        except (OSError, ValueError, IndexError):
            continue
    included = {pid}
    while True:
        children = {number for number, (parent, _, _) in processes.items() if parent in included}
        if children <= included:
            break
        included |= children
    pss = swap = unavailable = 0
    rows, roles = [], {}
    ticks = os.sysconf('SC_CLK_TCK')
    for number in sorted(included):
        if number not in processes:
            continue
        parent, directory, fields = processes[number]
        memory = _counters(directory / 'smaps_rollup')
        readable = 'Pss' in memory
        unavailable += not readable
        pss += memory.get('Pss', 0)
        swap += memory.get('SwapPss', 0)
        row = dict(pid=number, parent_pid=parent, role=_role(number, pid, directory),
                   state=fields[0], start_ticks=int(fields[19]) if len(fields) > 19 else None,
                   cpu_seconds=round((int(fields[11]) + int(fields[12])) / ticks, 3) if len(fields) > 12 else None,
                   threads=int(fields[17]) if len(fields) > 17 else None,
                   major_faults=int(fields[9]) if len(fields) > 9 else None, memory_readable=readable)
        for source, target in [('Pss', 'pss_mib'), ('Rss', 'rss_mib'), ('SwapPss', 'swap_mib')]:
            row[target] = round(memory[source] / 1024, 3) if source in memory else None
        row['private_mib'] = round(sum(memory.get(k, 0) for k in
            ('Private_Clean', 'Private_Dirty', 'Private_Hugetlb')) / 1024, 3) if readable else None
        try:
            row['file_descriptors'] = sum(1 for _ in (directory / 'fd').iterdir())
        except OSError:
            row['file_descriptors'] = None
        rows.append(row)
        group = roles.setdefault(row['role'], dict(processes=0, pss_mib=0.0, swap_mib=0.0))
        group['processes'] += 1
        group['pss_mib'] = round(group['pss_mib'] + (row['pss_mib'] or 0), 3)
        group['swap_mib'] = round(group['swap_mib'] + (row['swap_mib'] or 0), 3)
    root = processes.get(pid)
    return dict(timestamp=datetime.now(timezone.utc).isoformat(), pid=pid,
                running=root is not None, processes=len(rows),
                root_start_ticks=int(root[2][19]) if root and len(root[2]) > 19 else None,
                pss_mib=round(pss / 1024, 2), swap_mib=round(swap / 1024, 2),
                unreadable_processes=unavailable, by_role=roles, process_details=rows,
                system_memory=system_memory(proc))


def diagnostic_metadata(config_path=None):
    from .config import ConfigStore
    settings = None
    try:
        settings = ConfigStore(config_path).load()  # Unknown keys, including legacy root URLs, are dropped.
    except (OSError, ValueError):
        pass
    version = None
    if shutil.which('dpkg-query'):
        try:
            result = subprocess.run(['dpkg-query', '-W', '-f=${Version}', 'game-stream'],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and re.fullmatch(r'\d+\.\d+\.\d+', result.stdout.strip()):
                version = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    session = os.environ.get('XDG_SESSION_TYPE', '')
    return dict(schema=2, installed_app_version=version,
                os=platform.system(), kernel=platform.release(), architecture=platform.machine(),
                logical_cpus=os.cpu_count(), page_size_bytes=os.sysconf('SC_PAGE_SIZE'),
                desktop_session=session if session in ('x11', 'wayland') else 'unknown', settings=settings,
                settings_source='Validated on-disk preferences with defaults for missing keys; runtime session choices can differ.',
                privacy='No command lines, environment dumps, URLs, cookies, passwords, memory dumps or file paths are captured.')
