"""Probe .als internals: gzip? decompressed size/hash? identical-content pairs?"""
import os
import sys
import gzip
import hashlib
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def info(p):
    st = os.stat(p)
    with open(p, "rb") as f:
        data = f.read()
    gz = data[:2] == b"\x1f\x8b"
    out = {"name": Path(p).name, "size": len(data),
           "mtime": st.st_mtime, "gz": gz,
           "declen": None, "sha1": None, "head": ""}
    if gz:
        try:
            dec = gzip.decompress(data)
        except Exception as e:
            out["head"] = f"<decompress failed: {e}>"
            return out
        out["declen"] = len(dec)
        out["sha1"] = hashlib.sha1(dec).hexdigest()
        out["head"] = dec[:400].decode("utf-8", "replace")
    return out


def main():
    roots = sys.argv[1:] or [input("Root: ").strip('" ')]
    recs = []
    for root in roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn
                     if d.casefold() not in {"backup", "abletoolz_backup"}]
            for f in fn:
                if f.lower().endswith(".als") and not f.startswith("._"):
                    recs.append(os.path.join(dp, f))
    by_hash = defaultdict(list)
    seen_folders = set()
    for p in sorted(recs):
        i = info(p)
        folder = os.path.dirname(p)
        ts = datetime.fromtimestamp(i["mtime"]).strftime("%Y-%m-%d %H:%M")
        print(f"{ts}  {i['size']:>9}  gz={i['gz']}  "
              f"declen={i['declen']}  sha1={(i['sha1'] or '-')[:12]}  "
              f"{i['name']!r}")
        if i["sha1"]:
            by_hash[i["sha1"]].append(i["name"])
        if folder not in seen_folders:
            seen_folders.add(folder)
            print(f"      head: {i['head'][:220]!r}")
    print("\n--- identical decompressed content (same sha1) ---")
    any_dup = False
    for h, names in by_hash.items():
        if len(set(names)) > 1:
            any_dup = True
            print(f"  {h[:12]}: {names}")
    if not any_dup:
        print("  none")


if __name__ == "__main__":
    main()
