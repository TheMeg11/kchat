"""Process/cgroup samples, NOT Railway billing or fabricated historical metrics."""
import os
import time
from collections import deque
from pathlib import Path

_samples = deque(maxlen=288)
_previous = None


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def sample():
    global _previous
    now, cpu = time.monotonic(), time.process_time()
    percent = round(100 * (cpu-_previous[1])/(now-_previous[0]), 2) if _previous and now > _previous[0] else None
    _previous = now, cpu
    rss = next((int(line.split()[1])/1024 for line in _read('/proc/self/status').splitlines() if line.startswith('VmRSS:')), None)
    limit = _read('/sys/fs/cgroup/memory.max')
    memory_mb = int(limit)/1024**2 if limit.isdigit() and int(limit) < 2**60 else None
    cpu_max = _read('/sys/fs/cgroup/cpu.max').split()
    cores = int(cpu_max[0])/int(cpu_max[1]) if len(cpu_max) == 2 and cpu_max[0].isdigit() else None
    point = {"time":time.strftime('%H:%M:%S',time.gmtime()), "cpu":percent,
        "ramPct":round(rss/memory_mb*100, 2) if rss is not None and memory_mb else None}
    if not _samples or now-_samples[-1][0] >= 30:
        _samples.append((now,point))
    return {"ramUsageMb":round(rss,2) if rss else None, "ramLimitMb":memory_mb,
        "cpuUsagePct":percent,"cpuLimitCores":cores,"telemetrySeries":[p for _,p in _samples]}
