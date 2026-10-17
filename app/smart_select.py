"""Smart Select part 1: regexes, tokeniser, merge rules."""
import difflib
import os
import re
from collections import defaultdict
from pathlib import Path

AL_TAG_RE = re.compile(r"\(?\bAL\d+(?:\.\d+)*(?:\s*BETA)?\b\)?", re.IGNORECASE)
TRAILING_ALS_BITS_RE = re.compile(r"(?:[\s.]+als\s*)+$", re.IGNORECASE)
TEMPO_MIN, TEMPO_MAX = 60, 250
FILLER_WORDS = frozenset({
    "further", "actually", "this", "time", "just", "again", "also",
    "more", "really", "very", "final", "backup", "copy",
})
INT_RE = re.compile(r"\d+")
BPM_TOKEN_RE = re.compile(r"^bpm(\d+)$")

# Month names (full + common abbreviations incl. "sept") act as date
# markers: "july 25" / "sept 26" are save dates = versions, not stages.
MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec",
})


def is_bpm_token(word):
    return BPM_TOKEN_RE.match(word) is not None


def bpm_value(word):
    m = BPM_TOKEN_RE.match(word)
    return int(m.group(1)) if m else None


def strip_als_artifact(raw_stem):
    if re.search(r"[A-Z]{3,}als(\b|(?=\d))", raw_stem):
        raw_stem = re.sub(r"([A-Z0-9]{3,})als\b", r"\1", raw_stem)
        raw_stem = re.sub(r"([A-Z0-9]{3,})als(?=\d)", r"\1", raw_stem)
    return raw_stem


def normalize_tokens(stem):
    raw = strip_als_artifact(stem)
    raw = AL_TAG_RE.sub(" ", raw)
    raw = TRAILING_ALS_BITS_RE.sub(" ", raw)
    low = raw.lower().replace("_", " ")
    low = re.sub(r"(?<=\d)(?=[a-z])", " ", low)
    low = re.sub(r"(?<=[a-z])(?=\d)", " ", low)
    low = re.sub(r"[^a-z0-9\s]", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    toks = low.split()
    if not toks:
        return ("", [], [], {"cleaned": ""})
    song = toks[0]
    rest = toks[1:]
    versions = [t for t in rest if re.fullmatch(r"\d+(?:\.\d+)?", t)]
    tempo_vals = set()
    for tok in rest:
        for num in INT_RE.findall(tok):
            try:
                val = int(num)
            except ValueError:
                continue
            if TEMPO_MIN <= val <= TEMPO_MAX:
                tempo_vals.add(val)
    stage = []
    idx = 0
    while idx < len(rest):
        tok = rest[idx]
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            try:
                val = int(float(tok))
            except ValueError:
                idx += 1
                continue
            if TEMPO_MIN <= val <= TEMPO_MAX:
                stage.append(f"bpm{val}")
            idx += 1
            continue
        if len(tok) == 1 and tok.isalpha():
            idx += 1
            continue
        if tok in MONTHS:
            # "july 25" / "sept 26": date stamp = version, not a stage.
            # Drop the month and a directly attached day/year number.
            nxt = rest[idx + 1] if idx + 1 < len(rest) else None
            if nxt is not None and re.fullmatch(r"\d+(?:\.\d+)?", nxt):
                try:
                    day = int(float(nxt))
                except ValueError:
                    day = None
                if day is not None and 1 <= day <= 31:
                    idx += 2
                    continue
            idx += 1
            continue
        stage.append(tok)
        idx += 1
    deduped = []
    for word in stage:
        if not (deduped and deduped[-1] == word):
            deduped.append(word)
    return (song, deduped, versions, {"cleaned": low, "tempo": tempo_vals})


def stage_key(words):
    return " ".join(words) if words else "<base>"

def demote_outliers(metas):
    cands = sorted({v for m in metas for v in m.get("tempo", set())})
    if len(cands) < 2:
        bad = set(cands)
    else:
        bad = {v for v in cands
               if not any(v != u and abs(v - u) <= 50 for u in cands)}
    if not bad:
        return
    for meta in metas:
        if meta.get("tempo", set()) & bad:
            meta["stage_words"] = [
                w for w in meta["stage_words"]
                if not (is_bpm_token(w) and bpm_value(w) in bad)]
            meta["tempo"] = meta.get("tempo", set()) - bad
            meta["key"] = stage_key(meta["stage_words"])


def version_run_ok(members):
    if len(members) < 3:
        return False
    nums = []
    for meta in members:
        toks = meta["cleaned"].split()
        lead = None
        for tok in toks[1:]:
            if re.fullmatch(r"\d+", tok):
                lead = int(tok)
                break
        if lead is None:
            return False
        nums.append(lead)
    if nums != sorted(nums):
        return False
    return len(set(nums)) == len(nums)


def merge_ok(sw, lw, gap=None):
    if not sw:
        return False
    ss, ls = set(sw), set(lw)
    if ss.issubset(ls):
        extra = ls - ss
        if extra <= FILLER_WORDS and len(extra) <= 2:
            return True
        if extra <= FILLER_WORDS and gap is not None and gap <= 300:
            return True
        if gap is not None and gap <= 90 and len(ls) - len(ss) <= 1:
            return True
    a, b = " ".join(sw), " ".join(lw)
    if len(sw) == len(lw):
        if difflib.SequenceMatcher(None, a, b).ratio() >= 0.90:
            return True
    return False


def _same_stem(word1, word2):
    """Check whether two words share a stem (consolidate/consolidating)."""
    if word1 == word2:
        return True
    short, long = (word1, word2) if len(word1) <= len(word2) else (word2, word1)
    if len(short) < 5:
        return False
    if not long.startswith(short):
        # handle e-drop: consolidate vs consolidating shares "consolidat"
        stem = short[:-1] if short.endswith("e") else short
        if len(stem) < 5 or not long.startswith(stem):
            return False
    # suffix must look like an inflection, not a new word
    tail = long[len(short):] if long.startswith(short) else long[len(short) - 1:]
    return tail in ("ing", "ed", "s", "es", "d")


def _notes_overlap(short_words, long_words):
    """Subset check that treats inflected forms as equal."""
    remaining = list(long_words)
    for word in short_words:
        for idx, other in enumerate(remaining):
            if _same_stem(word, other):
                remaining.pop(idx)
                break
        else:
            return None
    return remaining


def _fuzzy_same_count_ok(sw, lw):
    if len(sw) != len(lw):
        return False
    pairs = sum(1 for a, b in zip(sorted(sw), sorted(lw)) if _same_stem(a, b))
    if pairs == len(sw):
        return True
    a, b = " ".join(sw), " ".join(lw)
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.90


def merge_ok_stemmed(sw, lw, gap=None):
    """merge_ok with stemming: consolidate == consolidating.

    Key guard: the shorter side must share >=2 stemmed words with the
    longer side before ANY subset/fuzzy merge is allowed. This keeps
    'consolidate bits groups' and 'really consolidating drums into
    racks' split (only 'consolidat*' in common) while still merging
    'consolidate' -> 'consolidate further' (filler extra).
    """
    if not sw or not lw:
        return False
    if len(sw) > len(lw):
        sw, lw = lw, sw
    # count stemmed overlap; need 2+ shared core words to merge at all
    remaining = list(lw)
    shared = 0
    for word in sw:
        for idx, other in enumerate(remaining):
            if _same_stem(word, other):
                remaining.pop(idx)
                shared += 1
                break
    if shared < 2 and not (len(sw) == 1 and shared == 1):
        return False
    extra = remaining
    extra_set = set(extra)
    if extra_set <= FILLER_WORDS and len(extra_set) <= 2:
        return True
    if extra_set <= FILLER_WORDS and gap is not None and gap <= 300:
        return True
    if gap is not None and gap <= 90 and len(extra) <= 1:
        return True
    if _fuzzy_same_count_ok(sw, lw):
        return True
    return False


merge_ok_plain = merge_ok
merge_ok = merge_ok_stemmed
def _cluster_files(files, mtime_fn, ctime_fn):
    metas = []
    for path in files:
        try:
            mtime = mtime_fn(path)
        except OSError:
            mtime = 0
        try:
            ctime = ctime_fn(path)
        except OSError:
            ctime = 0
        song, sw, vers, dbg = normalize_tokens(_stem_of(path))
        metas.append({
            "path": path, "name": Path(path).name,
            "mtime": mtime, "ctime": ctime,
            "song": song, "stage_words": sw,
            "key": stage_key(sw), "versions": vers,
            "cleaned": dbg["cleaned"], "tempo": dbg.get("tempo", set())})
    by_song = defaultdict(list)
    for meta in metas:
        by_song[meta["song"] or "<unknown>"].append(meta)
    for song_items in by_song.values():
        demote_outliers(song_items)
    selected, stages = [], []
    for song, items in sorted(by_song.items()):
        tempos_here = set()
        for meta in items:
            tempos_here |= set(meta.get("tempo", set()))
        guard = False
        if len(tempos_here) >= 2:
            ordered = sorted(items, key=lambda m: (m["mtime"], m["ctime"]))
            if not version_run_ok(ordered):
                vals = sorted(tempos_here)
                guard = (max(vals) - min(vals)) <= 50
        groups = defaultdict(list)
        for meta in items:
            groups[meta["key"]].append(meta)
        keys = sorted(groups.keys(),
                      key=lambda k: (k == "<base>", len(k.split())))
        merged_into = {}
        for key in keys:
            if key in merged_into:
                continue
            kw = [] if key == "<base>" else key.split()
            for other in keys:
                if other == key or other in merged_into or other == "<base>":
                    continue
                ow = other.split()
                if len(kw) <= len(ow):
                    sw2, lw2 = kw, ow
                else:
                    sw2, lw2 = ow, kw
                if guard:
                    kb = {w for w in kw if is_bpm_token(w)}
                    ob = {w for w in ow if is_bpm_token(w)}
                    if kb != ob:
                        kn = {w for w in kw if not is_bpm_token(w)}
                        on = {w for w in ow if not is_bpm_token(w)}
                        shared = len(kn & on) >= 2
                        small = kb if len(kb) <= len(ob) else ob
                        big = ob if small is kb else kb
                        sv = sorted(bpm_value(w) for w in small)
                        bv = sorted(bpm_value(w) for w in big)
                        ok = (shared and bool(sv) and bool(bv)
                              and all(v > 50 for v in bv)
                              and all(v <= 50 for v in sv)
                              and (max(bv) - max(sv)) > 50)
                        if not ok:
                            continue
                try:
                    gap = abs(max(x["mtime"] for x in groups[key])
                              - max(x["mtime"] for x in groups[other]))
                except Exception:
                    gap = None
                if merge_ok(sw2, lw2, gap):
                    ak = max(x["mtime"] for x in groups[key])
                    ao = max(x["mtime"] for x in groups[other])
                    if ak >= ao:
                        winner, loser = key, other
                    else:
                        winner, loser = other, key
                    merged_into[loser] = winner
        final = defaultdict(list)
        for key, members in groups.items():
            target = key
            while target in merged_into:
                target = merged_into[target]
            final[target].extend(members)
        for skey, members in sorted(final.items()):
            ordered = sorted(members, key=lambda m: (m["mtime"], m["ctime"]))
            pick = ordered[-1]
            selected.append(pick)
            stages.append({
                "song": song, "stage": skey, "pick": pick,
                "members": ordered,
                "merged_from": [kk for kk, vv in merged_into.items()
                                if vv == skey]})
    picked = {m["path"] for m in selected}
    not_picked = [m for m in metas if m["path"] not in picked]
    return selected, not_picked, stages


def smart_picks_for_files(files, mtime_fn=None, ctime_fn=None):
    mtime_fn = mtime_fn or os.path.getmtime
    ctime_fn = ctime_fn or os.path.getctime
    selected, _np, _st = _cluster_files(list(files), mtime_fn, ctime_fn)
    return [m["path"] for m in selected]


def smart_picks_by_folder(projects_by_folder, mtime_fn=None, ctime_fn=None):
    mtime_fn = mtime_fn or os.path.getmtime
    ctime_fn = ctime_fn or os.path.getctime
    result = {}
    for folder, info in (projects_by_folder or {}).items():
        if isinstance(info, dict):
            files = (info or {}).get("files", [])
        else:
            files = list(info or [])
        if not files:
            continue
        result[folder] = smart_picks_for_files(files, mtime_fn, ctime_fn)
    return result


def cluster_folder(files, mtime_fn=None, ctime_fn=None):
    return _cluster_files(list(files), mtime_fn or os.path.getmtime,
                          ctime_fn or os.path.getctime)

    a, b = " ".join(sw), " ".join(lw)
    if len(sw) == len(lw):
        if difflib.SequenceMatcher(None, a, b).ratio() >= 0.90:
            return True
    return False


def _stem_of(path):
    stem = Path(path).name
    if stem.lower().endswith(".als"):
        stem = stem[:-4]
    cand = TRAILING_ALS_BITS_RE.sub("", stem).strip(" .")
    if cand != stem and cand:
        stem = cand
    return stem


demote_isolated_bpm_outliers = demote_outliers
largest_version_run_ok = version_run_ok
should_merge = merge_ok
