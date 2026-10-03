"""Memory and CPU awareness for constrained hosting (PaaS / containers).

Paid hosting tiers are small: Render's free plan gives 512 MB of RAM, and a
1M-agent simulation needs ~1.3 GB. This module reads the cgroup memory/CPU
limits of the container so the app can *refuse* an oversized request with a
clear message instead of being OOM-killed by the platform.

Measured on the base app (python:3.11-slim, Docker):

    baseline after imports ........ ~210 MB
    per 1 000 agents (peak) ....... ~1.2 MB

Limits can be overridden for testing with the ``MEMORY_LIMIT_BYTES`` and
``CPU_QUOTA`` environment variables.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Cost model (see module docstring) ---------------------------------------
BASELINE_BYTES = 210 * 1024 * 1024            # imports + Flask + interpreter
BYTES_PER_AGENT = 1.2 * 1024                  # ~1.2 KB per agent at peak
SAFETY_RESERVE_BYTES = 80 * 1024 * 1024       # allocator/socket/stack headroom

# Values above this are treated as "no limit" (cgroup v1 reports a huge number)
_UNLIMITED = 1 << 60

_MEMORY_LIMIT_PATHS = (
    '/sys/fs/cgroup/memory.max',                # cgroup v2
    '/sys/fs/cgroup/memory/memory.limit_in_bytes',  # cgroup v1
)


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    txt = _read_text(path)
    if txt is None or txt in ('max', ''):
        return None
    try:
        value = int(txt)
    except ValueError:
        return None
    if value <= 0 or value >= _UNLIMITED:
        return None
    return value


def available_memory_bytes() -> Optional[int]:
    """Container memory limit in bytes, or ``None`` if it cannot be detected.

    ``None`` means "unknown" (e.g. local Windows development), in which case no
    clamping is applied.
    """
    env = os.environ.get('MEMORY_LIMIT_BYTES')
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except ValueError:
            logger.warning("Ignoring invalid MEMORY_LIMIT_BYTES=%r", env)

    for path in _MEMORY_LIMIT_PATHS:
        value = _read_int(path)
        if value is not None:
            return value
    return None


def effective_cpu_count() -> int:
    """Number of CPUs this container may actually use (cgroup-aware).

    ``os.cpu_count()`` reports the *host* core count inside a container, which
    on a PaaS can be dozens of cores even on a "less than 1 CPU" plan — using it
    to size a process pool would blow the memory budget.
    """
    env = os.environ.get('CPU_QUOTA')
    if env:
        try:
            value = float(env)
            if value > 0:
                return max(1, int(value))
        except ValueError:
            logger.warning("Ignoring invalid CPU_QUOTA=%r", env)

    # cgroup v2: "<quota> <period>" or "max <period>"
    txt = _read_text('/sys/fs/cgroup/cpu.max')
    if txt:
        parts = txt.split()
        if len(parts) == 2 and parts[0] != 'max':
            try:
                return max(1, int(int(parts[0]) / int(parts[1])))
            except (ValueError, ZeroDivisionError):
                pass

    # cgroup v1
    quota = _read_int('/sys/fs/cgroup/cpu/cpu.cfs_quota_us')
    period = _read_int('/sys/fs/cgroup/cpu/cpu.cfs_period_us')
    if quota and period:
        return max(1, int(quota / period))

    return os.cpu_count() or 1


def memory_budget_bytes() -> Optional[int]:
    """Bytes available for agent data (limit − baseline − reserve)."""
    limit = available_memory_bytes()
    if limit is None:
        return None
    return max(0, limit - BASELINE_BYTES - SAFETY_RESERVE_BYTES)


def max_safe_population() -> Optional[int]:
    """Largest population that fits the container, or ``None`` if unknown."""
    budget = memory_budget_bytes()
    if budget is None:
        return None
    return max(1_000, int(budget / BYTES_PER_AGENT))


def max_parallel_workers(population: int) -> int:
    """How many worker processes a sensitivity run of this size may use.

    Each worker is a full Python process (~``BASELINE_BYTES``) plus its own
    copy of the population, so parallelism is bounded by memory as well as by
    the CPU quota.
    """
    cpus = effective_cpu_count()
    limit = available_memory_bytes()
    if limit is None:
        return cpus
    per_worker = BASELINE_BYTES + int(population) * BYTES_PER_AGENT
    usable = max(0, limit - SAFETY_RESERVE_BYTES)
    by_memory = int(usable // max(per_worker, 1))
    return max(1, min(cpus, by_memory))


def describe_limits() -> str:
    """Human-readable summary for logs and error messages."""
    limit = available_memory_bytes()
    if limit is None:
        return (f"memory limit: unknown (no cgroup); cpus={effective_cpu_count()}; "
                f"no population cap applied")
    return (f"memory limit: {limit / 1024 / 1024:.0f} MB; "
            f"cpus={effective_cpu_count()}; "
            f"max population: {max_safe_population():,}")


def check_population(requested: int) -> Optional[str]:
    """Return an error message if ``requested`` does not fit, else ``None``."""
    limit = max_safe_population()
    if limit is None or requested <= limit:
        return None
    return (f"Requested population {int(requested):,} exceeds this instance's "
            f"safe limit of {limit:,} agents "
            f"(available memory {available_memory_bytes() / 1024 / 1024:.0f} MB). "
            f"Use a smaller population or a larger plan.")
