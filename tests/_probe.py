#!/usr/bin/env python3
"""Execute differential scenarios and print one canonical JSON line per scenario.

Reads JSONL on stdin, writes JSONL on stdout. Run once against the pristine
checkout and once against the patched one; the two outputs must be byte-equal.

Scenario ops (all deterministic):
  {"op": "add", "kind": "html|image|item", "uid": str|null, "file": str, "title": str}
  {"op": "lookup", "uid": any}
  {"op": "items_append", ...same fields as add, but mutates book.items directly}
  {"op": "items_insert", "pos": int, ...}
  {"op": "items_setitem", "pos": int, ...}
  {"op": "items_delitem", "pos": int}
  {"op": "items_pop", "pos": int}
  {"op": "items_sort", "reverse": bool}
  {"op": "items_reverse"}
  {"op": "items_iadd", "items": [...]}
  {"op": "items_clear"}
  {"op": "items_reassign"}            # book.items = plain list
"""
import copy
import json
import pickle
import sys

from ebooklib import epub

KINDS = {"html": epub.EpubHtml, "image": epub.EpubImage, "item": epub.EpubItem}


def make(op):
    cls = KINDS[op.get("kind", "item")]
    kwargs = {"file_name": op.get("file", "")}
    if op.get("uid") is not None:
        kwargs["uid"] = op["uid"]
    if op.get("title"):
        kwargs["title"] = op["title"]
    return cls(**kwargs)


def describe(result):
    if result is None:
        return None
    return {
        "id": result.get_id(),
        "name": result.get_name(),
        "type": type(result).__name__,
        "title": getattr(result, "title", None),
    }


def run(scenario):
    book = epub.EpubBook()
    book.set_identifier(scenario.get("identifier", "probe"))
    out = {"lookups": [], "order": [], "pages": 0, "n": 0}
    out["errors"] = []
    for op in scenario["ops"]:
      try:
        name = op["op"]
        if name == "add":
            book.add_item(make(op))
        elif name == "lookup":
            out["lookups"].append(describe(book.get_item_with_id(op["uid"])))
        elif name == "items_append":
            book.items.append(make(op))
        elif name == "items_insert":
            book.items.insert(op["pos"], make(op))
        elif name == "items_setitem":
            book.items[op["pos"]] = make(op)
        elif name == "items_delitem":
            del book.items[op["pos"]]
        elif name == "items_pop":
            book.items.pop(op["pos"])
        elif name == "items_sort":
            book.items.sort(key=lambda it: it.get_id() or "", reverse=op.get("reverse", False))
        elif name == "items_reverse":
            book.items.reverse()
        elif name == "items_iadd":
            book.items += [make(o) for o in op["items"]]
        elif name == "items_clear":
            book.items.clear()
        elif name == "items_reassign":
            book.items = list(book.items)
        elif name == "pickle_roundtrip":
            book = pickle.loads(pickle.dumps(book, op.get("protocol", 2)))
        elif name == "deepcopy":
            book = copy.deepcopy(book)
        else:
            raise SystemExit(f"unknown op {name}")
      except SystemExit:
        raise
      except Exception as exc:  # noqa: BLE001 - the exception itself is compared
        out["errors"].append([name, type(exc).__name__, str(exc)])
    out["order"] = [it.get_id() for it in book.get_items()]
    out["names"] = [it.get_name() for it in book.get_items()]
    out["pages"] = len(book.pages)
    out["n"] = len(book.items)
    return out


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        scenario = json.loads(line)
        result = run(scenario)
        result["scenario"] = scenario["id"]
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
