#!/usr/bin/env python3
"""Regenerate docs/patches/ for the GitHub Pages patcher from releases.json.

    python3 tools/build_index.py

releases.json maps each official input file (name + MD5) to exactly ONE current patch
(patches/<name>.json). Entries with "status": "active" are published; "pending" entries
are validated only if their patch file already exists and are never emitted. The script
fails if any input MD5 has more than one entry, writes docs/patches/index.json and copies
only the patch JSONs of active releases into docs/patches/ (anything else there is removed).
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "patches"


def fail(msg):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)


def main():
    rel = json.loads((ROOT / "releases.json").read_text())
    inputs, entries, keep = {}, [], set()
    for r in rel["releases"]:
        inp = r["input"]
        md5 = inp["md5"].lower()
        if md5 in inputs:
            fail("more than one release for input MD5 %s (%s)" % (md5, inp["file"]))
        inputs[md5] = inp["file"]
        status = r.get("status", "active")
        if status not in ("active", "pending"):
            fail("%s: unknown status %r" % (inp["file"], status))
        if status == "active" and not r.get("patch"):
            fail("%s: active release without a patch" % inp["file"])
        entry = {"input": {k: inp[k] for k in ("file", "md5", "radio", "region", "version")}, "release": None}
        if r.get("patch"):
            src = ROOT / "patches" / r["patch"]
            if not src.is_file():
                if status == "pending":
                    print("pending (patch not built yet): %s -> %s" % (inp["file"], r["patch"]))
                    continue
                fail("patch file not found: %s" % src)
            p = json.loads(src.read_text())
            if p["input"]["md5"].lower() != md5 or p["input"]["file"] != inp["file"]:
                fail("%s is for %s %s, not %s %s" % (r["patch"], p["input"]["file"], p["input"]["md5"], inp["file"], md5))
            for key in ("name", "ops", "output"):
                if key not in p:
                    fail("%s: missing '%s'" % (r["patch"], key))
            entry["release"] = {
                "name": p["name"],
                "patch_file": r["patch"],
                "patch_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "label": r.get("release", ""),
                "date": r.get("date", ""),
                "title": r.get("title") or p.get("description", p["name"]),
                "description": p.get("description", ""),
                "features": r.get("features", []),
                "output_md5": p["output"]["md5"],
                "output_main_md5": p["output"]["main_md5"],
                "bootloader": "bootstrap" in p,
                "tx_outside_amateur_bands": bool(r.get("tx_outside_amateur_bands", False)),
            }
            if status == "pending":
                print("pending (not published): %s -> %s" % (inp["file"], r["patch"]))
                continue
            keep.add(r["patch"])
        entries.append(entry)

    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        if f.name != "index.json" and f.name not in keep:
            f.unlink()
    for name in sorted(keep):
        shutil.copyfile(ROOT / "patches" / name, OUT / name)
    index = {"generated_from": "releases.json", "releases": entries}
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print("wrote %s: %d active release(s): %s" % (OUT / "index.json", len(entries), ", ".join(sorted(keep)) or "-"))


if __name__ == "__main__":
    main()
