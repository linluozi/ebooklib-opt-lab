#!/usr/bin/env python3
"""Differential test: pristine ebooklib vs patched ebooklib, byte for byte.

Both implementations run the same 2200 scenarios (random API call sequences plus
edge cases) through ``tests/_probe.py``; the two JSONL outputs must be identical
byte for byte, including item order, resolved objects and raised exceptions.

Usage:
    python3 tests/test_diff.py --base /tmp/eb-base --opt /tmp/eb-opt
"""
import argparse
import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "_probe.py")
N_SCENARIOS = 2200
SEED = 20261101
UIDS = [None, "", "chapter_1", "chapter_2", "dup", "dup", "üñïçødé", "a" * 300,
        "static_0", "image_3", "with space", "line\nbreak"]


def make_op(rng, allow_lookup=True):
    kind = rng.choice(["html", "html", "image", "item"])
    uid = rng.choice(UIDS) if rng.random() < 0.45 else None
    op = {"kind": kind, "uid": uid, "file": f"f{rng.randrange(50)}.xhtml",
          "title": rng.choice(["", "T", "章 一", "x" * 40])}
    return op


def scenario(i, rng):
    ops = []
    length = 0
    for _ in range(rng.randrange(1, 26)):
        roll = rng.random()
        if roll < 0.55:
            op = make_op(rng)
            ops.append({"op": "add", **op})
            length += 1
        elif roll < 0.68:
            ops.append({"op": "lookup", "uid": rng.choice(UIDS)})
        elif roll < 0.73:
            ops.append({"op": "items_append", **make_op(rng)})
            length += 1
        elif roll < 0.77 and length:
            ops.append({"op": "items_insert", "pos": rng.randrange(length + 1), **make_op(rng)})
            length += 1
        elif roll < 0.81 and length:
            ops.append({"op": "items_setitem", "pos": rng.randrange(length), **make_op(rng)})
        elif roll < 0.85 and length:
            ops.append({"op": "items_delitem", "pos": rng.randrange(length)})
            length -= 1
        elif roll < 0.88 and length:
            ops.append({"op": "items_pop", "pos": rng.randrange(length)})
            length -= 1
        elif roll < 0.90 and length:
            ops.append({"op": "items_sort", "reverse": rng.random() < 0.5})
        elif roll < 0.92 and length:
            ops.append({"op": "items_reverse"})
        elif roll < 0.94:
            extra = [make_op(rng) for _ in range(rng.randrange(1, 4))]
            ops.append({"op": "items_iadd", "items": extra})
            length += len(extra)
        elif roll < 0.95:
            ops.append({"op": "items_clear"})
            length = 0
        elif roll < 0.955:
            ops.append({"op": "items_reassign"})
        elif roll < 0.975:
            ops.append({"op": "pickle_roundtrip", "protocol": rng.choice([0, 2, 4, 5])})
        elif roll < 0.98:
            ops.append({"op": "deepcopy"})
        else:
            ops.append({"op": "lookup", "uid": rng.choice(UIDS)})
    ops.append({"op": "lookup", "uid": rng.choice(UIDS)})
    return {"id": i, "ops": ops}


def fixed_scenarios():
    return [
        {"id": "empty", "ops": [{"op": "lookup", "uid": "nope"}]},
        {"id": "dup-first-wins", "ops": [
            {"op": "add", "kind": "html", "uid": "dup", "file": "a.xhtml"},
            {"op": "add", "kind": "item", "uid": "dup", "file": "b.bin"},
            {"op": "lookup", "uid": "dup"}]},
        {"id": "dup-then-remove-first", "ops": [
            {"op": "add", "kind": "html", "uid": "dup", "file": "a.xhtml"},
            {"op": "add", "kind": "item", "uid": "dup", "file": "b.bin"},
            {"op": "items_delitem", "pos": 0},
            {"op": "lookup", "uid": "dup"}]},
        {"id": "none-uid-lookup", "ops": [
            {"op": "add", "kind": "html", "uid": None, "file": "x.xhtml"},
            {"op": "lookup", "uid": None},
            {"op": "lookup", "uid": ""}]},
        {"id": "unicode-and-long", "ops": [
            {"op": "add", "kind": "html", "uid": "üñïçødé", "file": "u.xhtml"},
            {"op": "add", "kind": "html", "uid": "a" * 300, "file": "l.xhtml"},
            {"op": "lookup", "uid": "üñïçødé"},
            {"op": "lookup", "uid": "a" * 300}]},
        {"id": "plain-list-reassign", "ops": [
            {"op": "add", "kind": "html", "uid": "x1", "file": "1.xhtml"},
            {"op": "items_reassign"},
            {"op": "lookup", "uid": "x1"},
            {"op": "add", "kind": "html", "uid": "x2", "file": "2.xhtml"},
            {"op": "lookup", "uid": "x2"}]},
    ]


def build_payload():
    rng = random.Random(SEED)
    scenarios = fixed_scenarios() + [scenario(i, rng) for i in range(N_SCENARIOS)]
    return "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in scenarios)


def run(impl_path, payload):
    env = dict(os.environ)
    env["PYTHONPATH"] = impl_path
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, PROBE], input=payload.encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode()[-3000:])
        raise SystemExit(f"probe failed for {impl_path} (exit {proc.returncode})")
    return proc.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--opt", required=True)
    args = ap.parse_args()
    payload = build_payload()
    base_out = run(args.base, payload)
    opt_out = run(args.opt, payload)
    base_lines = base_out.splitlines()
    opt_lines = opt_out.splitlines()
    assert len(base_lines) == len(opt_lines), (len(base_lines), len(opt_lines))
    mismatches = [i for i, (a, b) in enumerate(zip(base_lines, opt_lines)) if a != b]
    print(f"scenarios: {len(base_lines)}  mismatches: {len(mismatches)}")
    if mismatches:
        i = mismatches[0]
        print("first mismatch at scenario", i)
        print("base:", base_lines[i][:600].decode(errors="replace"))
        print("opt :", opt_lines[i][:600].decode(errors="replace"))
        raise SystemExit(1)
    print("byte-identical: yes")


if __name__ == "__main__":
    main()
