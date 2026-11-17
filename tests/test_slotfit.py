import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from slotfit.analyze import (
    ALREADY,
    REVIEW,
    SAFE,
    UNSAFE,
    analyze_paths,
    analyze_source,
)
from slotfit.measure import measure_layout, measure_materialised

FIXTURES = {
    "plain": '''
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
''',
    "loop_assign": '''
class Bag:
    def __init__(self, items):
        self.items = []
        for item in items:
            self.items.append(item)
''',
    "method_assign": '''
class Lazy:
    def __init__(self):
        self.value = None
    def load(self, v):
        self.loaded = v
''',
    "setattr": '''
class Dyn:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
''',
    "dict_access": '''
class Meta:
    def __init__(self):
        self.a = 1
    def dump(self):
        return dict(self.__dict__)
''',
    "weakref": '''
class Node:
    __weakref__ = None
    def __init__(self):
        self.next = None
''',
    "getattr": '''
class Proxy:
    def __init__(self):
        self.target = None
    def __getattr__(self, name):
        return getattr(self.target, name)
''',
    "dataclass": '''
from dataclasses import dataclass
@dataclass
class Conf:
    a: int = 0
    b: int = 0
''',
    "inherits": '''
class Child(Base):
    def __init__(self):
        self.a = 1
''',
    "slotted": '''
class Tight:
    __slots__ = ("a", "b")
    def __init__(self):
        self.a = 1
        self.b = 2
''',
    "no_init": '''
class Const:
    X = 1
''',
    "plain_init_vararg": '''
class Weird:
    def __init__(self, *args):
        self.args = args
''',
}


def one(name):
    return analyze_source(FIXTURES[name], f"{name}.py")


class AnalyzeTests(unittest.TestCase):
    def test_plain_class_is_safe(self):
        (report,) = one("plain")
        self.assertEqual(report.status, SAFE)
        self.assertEqual(report.attrs, ["x", "y"])
        self.assertEqual(report.reasons, [])

    def test_assignment_in_loop_is_still_fixed(self):
        (report,) = one("loop_assign")
        self.assertEqual(report.status, SAFE)
        self.assertEqual(report.attrs, ["items"])

    def test_assignment_outside_init_is_unsafe(self):
        (report,) = one("method_assign")
        self.assertEqual(report.status, UNSAFE)
        self.assertTrue(any("load()" in r for r in report.reasons))

    def test_setattr_is_unsafe(self):
        (report,) = one("setattr")
        self.assertEqual(report.status, UNSAFE)

    def test_dict_access_is_unsafe(self):
        (report,) = one("dict_access")
        self.assertEqual(report.status, UNSAFE)
        self.assertTrue(any("__dict__" in r for r in report.reasons))

    def test_weakref_declaration_is_unsafe(self):
        (report,) = one("weakref")
        self.assertEqual(report.status, UNSAFE)

    def test_dynamic_lookup_is_unsafe(self):
        (report,) = one("getattr")
        self.assertEqual(report.status, UNSAFE)

    def test_dataclass_is_review(self):
        (report,) = one("dataclass")
        self.assertEqual(report.status, REVIEW)

    def test_inheritance_is_review(self):
        (report,) = one("inherits")
        self.assertEqual(report.status, REVIEW)

    def test_already_slotted_is_reported_separately(self):
        (report,) = one("slotted")
        self.assertEqual(report.status, ALREADY)

    def test_no_init_is_review(self):
        (report,) = one("no_init")
        self.assertEqual(report.status, REVIEW)

    def test_vararg_is_unsafe(self):
        (report,) = one("plain_init_vararg")
        self.assertEqual(report.status, UNSAFE)

    def test_syntax_error_is_ignored(self):
        self.assertEqual(analyze_source("def broken(:\n", "bad.py"), [])


class MeasureTests(unittest.TestCase):
    def test_slotted_layout_is_smaller(self):
        layout = measure_layout(["x", "y", "z"])
        self.assertGreater(layout.dict_instance_bytes, layout.slotted_instance_bytes)
        self.assertGreater(layout.saved_bytes, 0)
        self.assertIn("saved_pct", layout.as_dict())

    def test_measurement_is_stable(self):
        first = measure_layout(["x", "y"], count=2000)
        second = measure_layout(["x", "y"], count=2000)
        self.assertLessEqual(abs(first.saved_bytes - second.saved_bytes), 1.0)

    @unittest.skipUnless(sys.version_info[:2] == (3, 12),
                         "managed-dict threshold is CPython 3.12 specific")
    def test_managed_dict_threshold_at_two_attributes(self):
        one = measure_layout(["a"], count=2000)
        two = measure_layout(["a", "b"], count=2000)
        self.assertGreater(two.dict_instance_bytes, one.dict_instance_bytes)

    def test_duplicate_attributes_are_deduped(self):
        layout = measure_layout(["x", "x", "y"])
        self.assertEqual(layout.attrs, ["x", "y"])

    def test_materialised_peak_tracks_count(self):
        result = measure_materialised(["a", "b"], 2000)
        self.assertEqual(result["count"], 2000)
        self.assertGreater(result["dict"], result["slotted"])
        self.assertGreater(result["saved_bytes"], 0)


class CliTests(unittest.TestCase):
    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "mod.py"
            src.write_text(FIXTURES["plain"] + FIXTURES["setattr"], encoding="utf-8")
            out = Path(tmp) / "report.json"
            proc = subprocess.run(
                [sys.executable, "-m", "slotfit.cli", tmp, "--json", str(out), "--top", "5"],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["tool"], "slotfit")
            self.assertEqual(payload["counts"][SAFE], 1)
            self.assertEqual(payload["counts"][UNSAFE], 1)
            safe = [c for c in payload["classes"] if c["status"] == SAFE][0]
            self.assertEqual(safe["layout"]["attrs"], ["x", "y"])

    def test_analyze_paths_skips_virtualenvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.py").write_text(FIXTURES["plain"], encoding="utf-8")
            hidden = root / ".venv"
            hidden.mkdir()
            (hidden / "skip.py").write_text(FIXTURES["plain"], encoding="utf-8")
            reports = analyze_paths([tmp])
            self.assertEqual(len(reports), 1)
            self.assertTrue(reports[0].path.endswith("keep.py"))


if __name__ == "__main__":
    unittest.main()
