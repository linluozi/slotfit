"""Measure the real per-instance allocation cost of a fixed attribute set.

``sys.getsizeof`` is not enough on CPython 3.12+: instance attributes live in a
lazily materialised "managed" dict, so ``getsizeof(obj)`` stays 48 bytes and
``getsizeof(obj.__dict__)`` reports the same 296 bytes for one or for zero
attributes.  We therefore build real instances under ``tracemalloc`` and divide
the traced peak by the count, subtracting the list spine measured on its own.

Two throw-away classes with the same attribute names are compared: one with a
normal instance dict and one with ``__slots__``.  Both are measured natively, so
the number describes the layout the interpreter actually allocates.
"""

from __future__ import annotations

import gc
import statistics
import tracemalloc
from dataclasses import dataclass

DEFAULT_COUNT = 20_000
REPEATS = 3


@dataclass
class Layout:
    attrs: list[str]
    count: int
    dict_instance_bytes: float
    slotted_instance_bytes: float

    @property
    def saved_bytes(self) -> float:
        return self.dict_instance_bytes - self.slotted_instance_bytes

    def saved_mb(self, instances: int) -> float:
        return self.saved_bytes * instances / (1024 * 1024)

    def as_dict(self) -> dict:
        return {
            "attrs": self.attrs,
            "count": self.count,
            "dict_instance_bytes": round(self.dict_instance_bytes, 2),
            "slotted_instance_bytes": round(self.slotted_instance_bytes, 2),
            "saved_bytes": round(self.saved_bytes, 2),
            "saved_pct": round(
                100.0 * self.saved_bytes / self.dict_instance_bytes, 2
            ) if self.dict_instance_bytes else 0.0,
        }


def _list_peak(count: int) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        holder = [None] * count
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del holder
    return peak


def _peak(attrs: list[str], count: int, slotted: bool) -> int:
    namespace = {"__slots__": tuple(attrs)} if slotted else {}
    cls = type("_SlotfitProbe", (object,), namespace)
    gc.collect()
    tracemalloc.start()
    try:
        objs = [cls() for _ in range(count)]
        for obj in objs:
            for name in attrs:
                setattr(obj, name, None)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del objs
    return peak


def _per_instance(attrs: list[str], count: int, slotted: bool) -> float:
    spine = _list_peak(count)
    samples = [(_peak(attrs, count, slotted) - spine) / count for _ in range(REPEATS)]
    return statistics.median(samples)


def measure_layout(attrs: list[str], count: int = DEFAULT_COUNT) -> Layout:
    attrs = list(dict.fromkeys(attrs))
    plain = _per_instance(attrs, count, slotted=False)
    slotted = _per_instance(attrs, count, slotted=True)
    return Layout(attrs, count, plain, slotted)


def measure_materialised(attrs: list[str], count: int) -> dict:
    """Peak traced bytes for ``count`` simultaneously live instances."""
    attrs = list(dict.fromkeys(attrs))
    out = {}
    for label, slotted in (("dict", False), ("slotted", True)):
        namespace = {"__slots__": tuple(attrs)} if slotted else {}
        cls = type(f"_SlotfitPeak{label}", (object,), namespace)
        gc.collect()
        tracemalloc.start()
        try:
            objs = [cls() for _ in range(count)]
            for obj in objs:
                for name in attrs:
                    setattr(obj, name, None)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        out[label] = peak
        del objs
    out["count"] = count
    out["saved_bytes"] = out["dict"] - out["slotted"]
    out["saved_pct"] = round(
        100.0 * out["saved_bytes"] / out["dict"], 2
    ) if out["dict"] else 0.0
    return out


def enrich(reports, count: int = DEFAULT_COUNT) -> list[dict]:
    from .analyze import SAFE

    rows = []
    for report in reports:
        row = report.as_dict()
        if report.status == SAFE:
            row["layout"] = measure_layout(report.attrs, count=count).as_dict()
        rows.append(row)
    return rows
