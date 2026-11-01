#!/usr/bin/env python3
"""Benchmark the public "assemble a book, then resolve its ids" workload.

One sample = build a fresh EpubBook by calling ``add_item`` for N items (a
doc-to-EPUB producer does exactly this), then resolve every resulting id once
through ``get_item_with_id``.

Usage:
    EB_IMPL=/path/to/ebooklib-checkout python3 benchmark/bench.py \
        --impl baseline --out benchmark/baseline.json
"""
import argparse
import json
import os
import random
import statistics
import sys
import time

IMPL = os.environ.get("EB_IMPL")
if IMPL:
    sys.path.insert(0, IMPL)

from ebooklib import epub  # noqa: E402

SCALES = {"small": 400, "medium": 1200, "large": 3600}
SEED = 20261101
WARMUP = 3
SAMPLES = 5


def build_items(n, seed=SEED):
    """Deterministic item recipe: 60% chapters, 20% images, 20% plain items.

    Every 9th item carries an explicit id of the form the generator would pick,
    so the auto-id probe loop really collides sometimes (upstream #388).
    """
    rng = random.Random(seed)
    items = []
    for i in range(n):
        explicit = f"chapter_{i}" if i % 9 == 0 else None
        kind = i % 10
        if kind < 6:
            items.append(epub.EpubHtml(uid=explicit, file_name=f"chap_{i}.xhtml", title=f"Chapter {i}"))
        elif kind < 8:
            items.append(epub.EpubImage(uid=explicit, file_name=f"img_{i}.png"))
        else:
            items.append(epub.EpubItem(uid=explicit, file_name=f"data_{i}.bin"))
    rng.shuffle(items)
    return items


def one_sample(items):
    book = epub.EpubBook()
    book.set_identifier("bench-0001")
    book.set_title("Bench")
    for item in items:
        book.add_item(item)
    resolved = []
    for item in items:
        got = book.get_item_with_id(item.get_id())
        resolved.append(None if got is None else got.get_name())
    order = [it.get_id() for it in book.get_items()]
    return resolved, order


def run_scale(name, n):
    items = build_items(n)
    for _ in range(WARMUP):
        one_sample(items)
    times, checksum = [], None
    for i in range(SAMPLES):
        t0 = time.perf_counter()
        resolved, order = one_sample(items)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        digest = hash((tuple(resolved), tuple(order)))
        checksum = digest if checksum is None else checksum
        assert digest == checksum, "non-deterministic workload"
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-dir", default=os.path.join(os.path.dirname(__file__), "raw"))
    args = ap.parse_args()
    os.makedirs(args.raw_dir, exist_ok=True)

    results = {
        "impl": args.impl,
        "python": sys.version.split()[0],
        "seed": SEED,
        "warmup": WARMUP,
        "samples": SAMPLES,
        "workload": "add_item x N then get_item_with_id for every id",
        "scales": {},
    }
    for name, n in SCALES.items():
        times = run_scale(name, n)
        for i, seconds in enumerate(times):
            with open(os.path.join(args.raw_dir, f"{args.impl}-{name}-{i}.json"), "w") as fh:
                json.dump({"impl": args.impl, "scale": name, "n": n, "sample": i,
                           "seconds": seconds, "seed": SEED}, fh)
        results["scales"][name] = {
            "n": n,
            "median_s": statistics.median(times),
            "p25_s": statistics.quantiles(times, n=4)[0],
            "p75_s": statistics.quantiles(times, n=4)[2],
            "rsd_pct": 100 * statistics.stdev(times) / statistics.mean(times),
            "samples_s": times,
        }
        s = results["scales"][name]
        print(f"{args.impl:9s} {name:6s} n={n:5d} median={s['median_s']*1000:9.2f}ms "
              f"p25={s['p25_s']*1000:8.2f} p75={s['p75_s']*1000:8.2f} rsd={s['rsd_pct']:.1f}%")
    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
