# slotfit

`slotfit` answers one question with a number: **if this class used `__slots__`, how
many bytes per instance would actually be saved, and is the change safe?**

It reads a Python package, classifies every class as `safe` / `review` / `unsafe` /
`already-slotted` from the source, and for the safe ones measures the real layout by
building throw-away twin classes under `tracemalloc` — one with a normal instance
dict, one with `__slots__` for the same attribute names.

Upstream: none (original project; no upstream code is copied or patched).

## Why `sys.getsizeof` is not enough

The usual estimate is "an instance dict costs 184 B, so `__slots__` saves that".
On CPython 3.12 that is wrong in both directions: `getsizeof(obj)` stays 48 B no
matter how many attributes the object has, and `getsizeof(obj.__dict__)` reports
296 B for zero attributes as well as for one. Measured with `tracemalloc` on
20,000 live instances (median of 3 runs, list spine subtracted):

| attributes | dict layout B/instance | slotted B/instance | saved B | saved % |
|---|---|---|---|---|
| 0 | 65.09 | 32.37 | 32.72 | 50.27% |
| 1 | 65.09 | 40.37 | 24.72 | 37.98% |
| 2 | 320.37 | 48.37 | **272.00** | **84.90%** |
| 3 | 320.37 | 56.37 | 264.00 | 82.40% |
| 5 | 320.37 | 72.37 | 248.00 | 77.41% |

There is a threshold at two attributes: the managed dict materialises and the cost
jumps from 65 B to 320 B. The classic `56 + 184 = 240 B` estimate underestimates
the two-attribute layout by 80 B (33%) and overestimates the one-attribute saving
by 159 B.

## Real corpus

Nine real libraries (three third-party, six of this team's published repos), 359
classes, no files modified — read-only clones, commits in `corpus/manifest.json`.
Total scan time 0.47 s.

| status | classes |
|---|---|
| safe | 50 |
| review (inheritance / decorator / no `__init__`) | 195 |
| unsafe (dynamic attributes, `__dict__`, `__weakref__`) | 92 |
| already slotted | 22 |

Best single class in the corpus: `GCToggler` (boltons) with 2 attributes — 272 B
saved per instance, i.e. **259.40 MiB per 1,000,000 live instances**. Full
per-class rows: `corpus/report.json`.

## Install and run

```bash
python3 -m pip install -e .
slotfit path/to/package --instances 1000000 --top 20 --json report.json
```

Without installing:

```bash
PYTHONPATH=. python3 -m slotfit.cli /tmp/corpus --json corpus/report.json
```

## Verification

```bash
python3 -m unittest discover -s tests -v     # 20 tests, 1.4 s, all pass
python3 -m slotfit.cli tests --only-safe      # every fixture is classified as expected
sh corpus/run_corpus.sh                       # rebuild the corpus table above
```

The 20 tests cover the safety classifier (assignment outside `__init__`, `setattr`,
`__dict__`, `__weakref__`, `__getattr__`, `*args`, dataclass, inheritance, already
slotted, syntax errors) and the measurement itself (stability across runs, the
3.12 two-attribute threshold, duplicate-attribute dedup, CLI JSON schema).

## Safety rules

`safe` requires all of: no non-`object` bases, no decorator other than
`final`/`property`/`staticmethod`/`classmethod`, no `cached_property`, no
`__getattr__`/`__getattribute__`/`__setattr__`/`__delattr__`, no `__dict__` access,
no `__weakref__`, no `setattr`/`vars`, no `**kwargs`/`*args` in `__init__`, no
attribute assigned outside `__init__`, and at least one attribute assigned in
`__init__`. Anything else is `review` or `unsafe` with the reason printed.

## Limitations (read before applying)

- The measurement uses **twin classes** with the same attribute names; it never
  imports or rewrites the class under test. It is a layout estimate, not a patch.
- Runtime-only attribute creation (e.g. `obj.x = 1` done by callers, `copy.copy`,
  unpickling) is invisible to static analysis and will raise `AttributeError`
  after a naive `__slots__` change. Classes that are pickled need
  `__getstate__`/`__setstate__` — this tool does not detect that.
- Inheritance is always `review`: a slotted subclass of a dict-carrying base still
  pays for the base dict.
- C-extension types, `__slots__` with `__dict__`, and classes created by
  `type()` at runtime are out of scope.

## 本轮创新

此前台账里的内存条目为 0，`__slots__` 也从未被测量过；所有优化都按时间计价。
本轮首次用 `tracemalloc` 实测 CPython 3.12 的 managed-dict 阈值：0–1 个属性
65.09 B/实例，2 个属性跳到 320.37 B/实例，经典 `56 + 184` 公式在 2 属性时低估
80 B；并把「该不该加 `__slots__`」从经验法则变成按真实属性集测量 + 可判定安全
分类（359 个真实类中仅 50 个 safe、92 个 unsafe），给出逐类 witness 与复现命令。

## Environment

- AMD EPYC 7K62, 4 vCPU, 3723 MB RAM
- CPython 3.12.3 (system `/usr/bin/python3.12`); no 3.10/3.11 on this machine, so no
  cross-interpreter numbers are claimed
- measurement: `count=20000`, `REPEATS=3`, median, list spine subtracted
- no randomness, no seed; timings not used in any claim

## License

MIT, original work. No upstream source is included.
