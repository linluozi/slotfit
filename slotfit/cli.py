"""slotfit command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import ALREADY, REVIEW, SAFE, UNSAFE, analyze_paths
from .measure import enrich


def _fmt_bytes(n: int) -> str:
    return f"{n} B"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slotfit",
        description="Find classes where __slots__ pays, with measured per-instance bytes.",
    )
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument(
        "--instances", type=int, default=1_000_000,
        help="instance count used for the projected saving (default: 1000000)",
    )
    parser.add_argument("--json", dest="json_path", help="write the full report as JSON")
    parser.add_argument("--top", type=int, default=15, help="rows to print (default: 15)")
    parser.add_argument(
        "--only-safe", action="store_true", help="print only classes classified safe"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reports = analyze_paths(args.paths)
    rows = enrich(reports)

    counts = {SAFE: 0, REVIEW: 0, UNSAFE: 0, ALREADY: 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    eligible = [r for r in rows if r["status"] == SAFE]
    eligible.sort(key=lambda r: r["layout"]["saved_bytes"], reverse=True)

    print(f"slotfit: scanned {len(rows)} classes in {len(args.paths)} path(s)")
    print(
        f"  safe={counts[SAFE]} review={counts[REVIEW]} "
        f"unsafe={counts[UNSAFE]} already-slotted={counts[ALREADY]}"
    )
    if eligible:
        best = eligible[0]
        print(
            f"  best single class: {best['name']} saves "
            f"{best['layout']['saved_bytes']:.1f} B/instance, "
            f"{best['layout']['saved_mb'] if False else best['layout']['saved_bytes'] * args.instances / (1024 * 1024):.2f} MiB "
            f"at {args.instances:,} live instances"
        )
    print()
    shown = [r for r in rows if not args.only_safe or r["status"] == SAFE]
    shown.sort(key=lambda r: (r["status"] != SAFE, -r.get("layout", {}).get("saved_bytes", 0)))
    print(f"{'class':<24} {'file':<24} {'status':<9} {'attrs':>5} {'dict B':>8} {'slot B':>8} {'saved B':>8}")
    for row in shown[: args.top]:
        layout = row.get("layout", {})
        print(
            f"{row['name'][:23]:<24} {Path(row['path']).name[:23]:<24} {row['status']:<9} "
            f"{len(row['attrs']):>5} {layout.get('dict_instance_bytes', 0):>8} "
            f"{layout.get('slotted_instance_bytes', 0):>8} "
            f"{layout.get('saved_bytes', 0):>8}"
        )
        for reason in row["reasons"][:2]:
            print(f"    - {reason}")

    if args.json_path:
        payload = {
            "tool": "slotfit",
            "python": sys.version.split()[0],
            "instances": args.instances,
            "counts": counts,
            "classes": rows,
        }
        Path(args.json_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
