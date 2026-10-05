"""
Missing-media resolution using abletoolz.

This parses a .als file directly (no screen automation, no Ableton needs to
be open) and relinks/copies missing sample references using the local sample
database built by sample_database.py.

Because abletoolz works on the .als file itself, an entire batch of projects
can be resolved up front, before Ableton is ever launched. This has replaced
the older OCR/screen-automation-based media_files_missing_handler pipeline
(archived under app/archive/), which is no longer used. Anything abletoolz
can't resolve (e.g. samples that were never indexed anywhere on disk) is
left missing and reported as such - it needs manual relinking.
"""

import logging
import os
from collections import Counter
from pathlib import Path

from abletoolz.live_set.document import AbletonSet

logger = logging.getLogger(__name__)

# RelativePathType 5 = "relative to an installed Live Pack / Core Library".
LIVE_PACK_RELATIVE_TYPE = 5

# Placeholder root for absolute paths saved by a non-Windows copy of Live,
# which carry no drive letter and so can't be attributed to a local volume.
FOREIGN_PATH_ROOT = "(macOS/Linux path)"


def _is_live_pack_ref(ref):
    """
    Whether Live resolves this reference through an installed Pack rather than
    through the path stored in the set.

    abletoolz's own SampleRef.pack_resolved only recognises Pack references
    that carry *no* absolute path element at all. Sets saved by older Live
    versions keep a stale absolute path (e.g. a Live 10 Core Library path)
    alongside the Pack name and RelativePathType 5, so abletoolz checks that
    dead path and reports the sample as missing - even though Live resolves it
    from the Pack database and never asks the user to relink it.

    Treating these as resolved keeps our report in step with what Live
    actually reports, and prevents a pointless "fix" that would copy some
    same-named file over a reference Live already handles.
    """
    if getattr(ref, "pack_resolved", False):
        return True
    if not getattr(ref, "live_pack", None):
        return False
    try:
        return ref.get_relative_type() == LIVE_PACK_RELATIVE_TYPE
    except Exception:
        return False


def _filter_pack_refs(refs):
    """Split refs into (genuinely_missing, live_pack_resolved)."""
    missing = []
    pack = []
    for ref in refs:
        (pack if _is_live_pack_ref(ref) else missing).append(ref)
    return missing, pack


def _exclude_pack_refs_from_fix(ableton_set):
    """
    Drop Live-Pack-resolved references from the parsed set's SampleRef cache
    so abletoolz's own samples.fix() never sees them.

    abletoolz.Samples.iterate() parses every SampleRef once and serves all
    later passes (including fix()) from that cache, so pruning it here is the
    supported way to keep fix() from rewriting a working Pack reference into a
    hard path. The cache is a parse-time convenience only - save_set() writes
    the underlying XML tree, so nothing is lost from the saved file.
    """
    samples = ableton_set.samples
    cache = getattr(samples, "_cache", None)
    if not cache:
        return 0
    kept, pack = _filter_pack_refs(cache)
    samples._cache = kept
    return len(pack)


def _root_exists(root):
    """Whether a filesystem root (drive letter or UNC share) is reachable."""
    if not root:
        return False
    probe = root if root.endswith(os.sep) else root + os.sep
    try:
        return os.path.exists(probe)
    except Exception:
        return False


def _collect_missing_roots(refs):
    """
    Group unresolved sample references by the filesystem root their stored
    absolute path points at (e.g. 'E:\\' or a UNC share).

    This is what identifies samples that are merely *offline* - living on an
    external drive that isn't plugged in - as opposed to genuinely lost. A
    root that doesn't currently exist is a strong signal the project needs
    that volume mounted rather than a real relink.

    Paths saved by a non-Windows copy of Live have no drive letter at all
    ('/Users/...'); those are bucketed separately since no local volume could
    ever satisfy them.

    Returns:
        dict: root -> {'exists': bool, 'foreign': bool, 'refs': int,
                       'names': set[str]}
    """
    roots = {}
    for ref in refs:
        absolute = getattr(ref, "absolute", None)
        if not absolute:
            continue
        try:
            root = Path(absolute).drive
        except Exception:
            continue
        foreign = False
        if not root:
            if str(absolute).replace("\\", "/").startswith("/"):
                root = FOREIGN_PATH_ROOT
                foreign = True
            else:
                # Relative-only reference: no volume to attribute it to.
                continue
        entry = roots.setdefault(
            root,
            {"exists": False if foreign else _root_exists(root), "foreign": foreign, "refs": 0, "names": set()},
        )
        entry["refs"] += 1
        entry["names"].add(ref.name)
    return roots


def _build_name_index(db):
    """
    Group a sample database (path -> {name, size, last_modified}) by sample
    name so matching is O(1) per missing reference instead of O(len(db)).
    """
    index = {}
    for smp_path, smp_info in db.items():
        index.setdefault(smp_info.get("name"), []).append((smp_path, smp_info))
    return index


def _simulate_fix(ableton_set, missing_refs, db, name_index=None):
    """
    Read-only replay of abletoolz's own SampleRef-matching rules, without
    copying any files or mutating the parsed document. Mirrors the decision
    logic in abletoolz.live_set.samples.Samples._fix_one() exactly (strict
    size-or-mtime match only, same as abletoolz itself - no "unique name"
    guessing, to avoid false-positive relinks), so dry runs report accurately
    what a real run would do.

    Returns:
        (collected_names, linked_names, still_missing_names, still_missing_refs)
        - the first three are lists of sample names, matching the shape
        resolve_missing_media() exposes; the fourth is the underlying
        SampleRef objects, which retain the stored absolute paths needed for
        offline-drive reporting.
    """
    if name_index is None:
        name_index = _build_name_index(db)

    project_root = ableton_set.find_project_root_folder()

    collected_names = []
    linked_names = []
    still_missing_names = []
    still_missing_refs = []

    for ref in missing_refs:
        candidates = name_index.get(ref.name, [])
        match_info = None
        for _smp_path, smp_info in candidates:
            size_match = ref.size is not None and smp_info.get("size") == ref.size
            raw_mtime = smp_info.get("last_modified")
            try:
                last_mod_int = int(float(raw_mtime)) if raw_mtime is not None else None
            except (TypeError, ValueError):
                last_mod_int = None
            modified_match = (
                ref.last_modified is not None and last_mod_int is not None and ref.last_modified == last_mod_int
            )
            if size_match or modified_match:
                match_info = smp_info
                break

        if match_info is None:
            still_missing_names.append(ref.name)
            still_missing_refs.append(ref)
            continue

        if not project_root:
            # No project root found - abletoolz would fall back to an
            # absolute-path link even on the "collect" pass.
            linked_names.append(ref.name)
            continue

        try:
            relative_type = ref.get_relative_type()
        except Exception:
            relative_type = None
        rel_path = str(ref.get_relative_value()) if relative_type == 3 else "Samples/Imported"
        smp_name = match_info.get("name") or ref.name
        target = project_root / rel_path / smp_name

        if target.exists() and target.stat().st_size != match_info.get("size"):
            # Same-name/different-size collision - the collect pass would be
            # blocked, but the link fallback would still resolve it.
            linked_names.append(ref.name)
        else:
            collected_names.append(ref.name)

    return collected_names, linked_names, still_missing_names, still_missing_refs


def resolve_missing_media(project_path, db, dry_run=False, name_index=None):
    """
    Attempt to resolve missing samples in a single .als file using abletoolz.

    Args:
        project_path: Path to the .als file.
        db: Sample database dict as returned by sample_database.load_index().
            If None, only the "check" step runs (no fixing is attempted).
        dry_run: If True, no files are touched at all (no audio files are
            copied into the project, no .als changes are made/saved) - a
            read-only simulation of abletoolz's own matching rules is used
            instead so 'fixed'/'fixed_names'/'linked_names' still reflect
            what *would* happen on a real run.
        name_index: Optional pre-built db grouped by sample name (see
            _build_name_index()). Only used for dry_run - pass this in from
            resolve_missing_media_bulk() to avoid rebuilding it per project.

    Returns:
        dict: {
            'success': bool,          # True if abletoolz was able to parse the set
            'error': str | None,
            'missing_before': int,
            'missing_after': int,
            'fixed': int,
            'saved': bool,            # True only if changes were actually written to disk
            'would_save': bool,       # True if a fix was found (whether or not it was saved)
            'fixed_names': list[str],         # sample names successfully relinked (collected/copied)
            'linked_names': list[str],        # sample names resolved via absolute-path link fallback
                                               # (collect was blocked by a same-name/different-size
                                               # file already present in the project's Samples/Imported)
            'still_missing_names': list[str], # sample names still unresolved
            'pack_skipped': int,              # references Live resolves from an installed Pack
                                               # (excluded from the counts above - see
                                               # _is_live_pack_ref())
            'missing_roots': dict,            # filesystem root -> {'exists', 'refs', 'names'}
                                               # for still-missing samples; roots where
                                               # exists=False are offline volumes (e.g. an
                                               # external drive that isn't plugged in)
        }
    """
    result = {
        'success': False,
        'error': None,
        'missing_before': 0,
        'missing_after': 0,
        'fixed': 0,
        'saved': False,
        'would_save': False,
        'fixed_names': [],
        'linked_names': [],
        'still_missing_names': [],
        'pack_skipped': 0,
        'missing_roots': {},
    }
    try:
        als_path = Path(project_path)
        if not als_path.exists():
            result['error'] = f"File not found: {als_path}"
            return result

        ableton_set = AbletonSet(als_path)
        if not ableton_set.parse():
            result['error'] = "abletoolz could not parse the .als file"
            return result

        ableton_set.find_project_root_folder()

        missing_before_refs, pack_refs = _filter_pack_refs(ableton_set.samples.check())
        result['pack_skipped'] = len(pack_refs)
        if pack_refs:
            logger.info(
                f"[abletoolz] {als_path.name}: ignoring {len(pack_refs)} reference(s) that Live resolves "
                "from an installed Pack (stale path stored alongside the Pack name)."
            )
            _exclude_pack_refs_from_fix(ableton_set)
        missing_before_names = [ref.name for ref in missing_before_refs]
        result['missing_before'] = len(missing_before_names)
        result['success'] = True

        if not missing_before_refs:
            logger.info(f"[abletoolz] {als_path.name}: no missing samples detected.")
            return result

        if db is None:
            logger.warning(
                f"[abletoolz] {als_path.name}: {len(missing_before_names)} missing sample(s) found but no "
                "sample database is loaded - skipping fix, will fall back to OCR handler."
            )
            result['missing_after'] = result['missing_before']
            result['still_missing_names'] = missing_before_names
            result['missing_roots'] = _collect_missing_roots(missing_before_refs)
            return result

        logger.info(
            f"[abletoolz] {als_path.name}: {len(missing_before_names)} missing sample reference(s) found, "
            "attempting fix via sample database..."
        )

        if dry_run:
            # Read-only simulation: abletoolz's own fix(collect_and_save=True)
            # actually copies matched audio files to disk as a side effect,
            # even if save_set() is never called - so a real dry run must
            # avoid invoking it at all and instead replay its matching rules
            # without touching the filesystem.
            collected_names, linked_names, still_missing_names, still_missing_refs = _simulate_fix(
                ableton_set, missing_before_refs, db, name_index=name_index
            )
            fixed_names = collected_names
            result['would_save'] = bool(collected_names or linked_names)
            result['missing_roots'] = _collect_missing_roots(still_missing_refs)
        else:
            # Predict the collect/link split up front using the same
            # read-only matching rules abletoolz itself applies - this is
            # used only for the human-readable breakdown below, not for the
            # ground-truth missing count (see re-parse note further down).
            predicted_collected, predicted_linked, _predicted_missing, _predicted_refs = _simulate_fix(
                ableton_set, missing_before_refs, db, name_index=name_index
            )

            did_fix_collect = ableton_set.samples.fix(db, collect_and_save=True)

            # Second pass: anything still missing after the collect-and-save
            # attempt is likely blocked because the project's own
            # Samples/Imported folder already contains a same-named file
            # with a different size (abletoolz refuses to overwrite it).
            # Fall back to linking the absolute path instead - this resolves
            # the reference without touching the conflicting file.
            did_fix_link = ableton_set.samples.fix(db, collect_and_save=False)

            result['would_save'] = bool(did_fix_collect or did_fix_link)

            if result['would_save']:
                ableton_set.save_set()
                result['saved'] = True

            # IMPORTANT: abletoolz's SampleRef.set_absolute()/set_relative()
            # only rewrite the underlying XML element - they never update the
            # SampleRef's own cached `.absolute`/`.relative` fields, which is
            # what `.absolute_exists`/`.relative_exists` (and therefore
            # `check()`) read. Calling check() again on this same in-memory
            # AbletonSet after fix() is unreliable and can under-report real
            # fixes (verified: a project reported as "10 still missing" right
            # after fixing showed 0 missing once reloaded fresh from disk).
            # Re-parsing the just-saved file gives the true, ground-truth
            # result.
            if result['saved']:
                reparsed = AbletonSet(als_path)
                if reparsed.parse():
                    reparsed.find_project_root_folder()
                    missing_after_refs, _ = _filter_pack_refs(reparsed.samples.check())
                else:
                    logger.warning(
                        f"[abletoolz] {als_path.name}: could not re-parse after saving to verify the fix; "
                        "falling back to in-memory (possibly stale) check."
                    )
                    missing_after_refs, _ = _filter_pack_refs(ableton_set.samples.check())
            else:
                missing_after_refs, _ = _filter_pack_refs(ableton_set.samples.check())

            missing_after_names = [ref.name for ref in missing_after_refs]
            still_missing_names = sorted(missing_after_names)
            result['missing_roots'] = _collect_missing_roots(missing_after_refs)

            # Attribute the ground-truth fixed count to the predicted
            # collect/link split for the report, capping each bucket to what
            # was actually resolved so the two always add up correctly.
            actually_fixed = Counter(missing_before_names) - Counter(missing_after_names)
            remaining = Counter(actually_fixed)
            collected_final = Counter()
            linked_final = Counter()
            for name, count in Counter(predicted_collected).items():
                take = min(count, remaining.get(name, 0))
                if take:
                    collected_final[name] += take
                    remaining[name] -= take
            for name, count in Counter(predicted_linked).items():
                take = min(count, remaining.get(name, 0))
                if take:
                    linked_final[name] += take
                    remaining[name] -= take
            # Anything fixed that our prediction didn't anticipate (edge
            # case) is still counted as fixed - just bucketed as "collected"
            # since that's the default/primary path.
            for name, count in remaining.items():
                if count > 0:
                    collected_final[name] += count

            fixed_names = sorted(collected_final.elements())
            linked_names = sorted(linked_final.elements())

        result['missing_after'] = len(still_missing_names)
        result['fixed'] = result['missing_before'] - result['missing_after']
        result['fixed_names'] = fixed_names
        result['linked_names'] = linked_names
        result['still_missing_names'] = still_missing_names

        if result['would_save'] and not dry_run:
            logger.info(
                f"[abletoolz] {als_path.name}: fixed {result['fixed']} sample reference(s) "
                f"({len(result['fixed_names'])} collected, {len(result['linked_names'])} linked by "
                "absolute path due to name collisions), saved set (original backed up automatically "
                "under abletoolz_backup/)."
            )
        elif result['would_save'] and dry_run:
            logger.info(
                f"[abletoolz] {als_path.name}: DRY RUN - would fix {result['fixed']} sample reference(s) "
                f"({len(result['fixed_names'])} collected, {len(result['linked_names'])} linked), "
                "nothing written to disk."
            )
        else:
            logger.warning(f"[abletoolz] {als_path.name}: could not fix any missing samples from the current database.")

        return result

    except Exception as e:
        logger.error(f"[abletoolz] Error resolving missing media for {project_path}: {e}")
        result['error'] = str(e)
        return result


def resolve_missing_media_bulk(project_paths, db, progress_callback=None, dry_run=False):
    """
    Run resolve_missing_media() over a batch of .als files up front, before any
    of them are opened in Ableton.

    Args:
        project_paths: Iterable of .als file paths.
        db: Sample database dict, or None.
        progress_callback: Optional callable(message: str) for status updates.
        dry_run: If True, no .als files are modified - the report shows what
            *would* be fixed so it can be reviewed before committing.

    Returns:
        dict mapping project_path -> result dict (see resolve_missing_media()).
    """
    results = {}
    project_paths = list(project_paths)
    total = len(project_paths)

    if db is None and progress_callback:
        progress_callback("No sample database loaded - missing media will only be checked, not fixed. Build the index from Sample Library... first.")
    if dry_run and progress_callback:
        progress_callback("DRY RUN mode - no files will be modified.")

    name_index = _build_name_index(db) if (dry_run and db) else None

    for i, project_path in enumerate(project_paths, 1):
        name = Path(project_path).name
        if progress_callback:
            progress_callback(f"[{i}/{total}] Checking {name}...")
        result = resolve_missing_media(project_path, db, dry_run=dry_run, name_index=name_index)
        results[project_path] = result

        if progress_callback:
            if not result['success']:
                progress_callback(f"  -> could not analyze: {result['error']}")
            elif result['missing_before'] == 0:
                progress_callback("  -> no missing samples")
            else:
                progress_callback(
                    f"  -> {result['fixed']}/{result['missing_before']} fixed, "
                    f"{result['missing_after']} still missing"
                )

    total_missing_before = sum(r['missing_before'] for r in results.values())
    total_fixed = sum(r['fixed'] for r in results.values())
    total_still_missing = sum(r['missing_after'] for r in results.values())
    mode_word = "would fix" if dry_run else "fixed"
    logger.info(
        f"[abletoolz] Bulk resolve complete: {total} project(s) checked, "
        f"{total_missing_before} missing sample reference(s) found, "
        f"{total_fixed} {mode_word}, {total_still_missing} still missing (will use OCR fallback where needed)."
    )
    if progress_callback:
        progress_callback(
            f"Bulk resolve complete: {total_fixed}/{total_missing_before} {mode_word} across {total} project(s), "
            f"{total_still_missing} still missing."
        )

    return results


def format_bulk_report(results, dry_run=False):
    """
    Turn resolve_missing_media_bulk()'s results dict into a human-readable
    text report suitable for display in the GUI or saving to a file.

    Args:
        results: dict mapping project_path -> result dict.
        dry_run: If True, labels the report as a preview and describes fixes
            as "would fix" rather than "fixed" (nothing was written to disk).

    Returns:
        str report text.
    """
    lines = []
    total = len(results)
    total_missing_before = sum(r['missing_before'] for r in results.values())
    total_fixed = sum(r['fixed'] for r in results.values())
    total_still_missing = sum(r['missing_after'] for r in results.values())
    total_errors = sum(1 for r in results.values() if not r['success'])
    total_clean = sum(1 for r in results.values() if r['success'] and r['missing_before'] == 0)
    total_pack_skipped = sum(r.get('pack_skipped', 0) for r in results.values())
    unique_still_missing = len({n for r in results.values() for n in r['still_missing_names']})

    lines.append("=" * 70)
    lines.append("MISSING MEDIA BULK FIX REPORT" + ("  (DRY RUN - PREVIEW ONLY)" if dry_run else ""))
    lines.append("=" * 70)
    if dry_run:
        lines.append("No files were modified. This is a preview of what 'Apply Fixes' would do.")
    lines.append(f"Projects scanned:        {total}")
    lines.append(f"Projects with no issues: {total_clean}")
    lines.append(f"Projects with errors:    {total_errors}")
    lines.append(f"Missing samples found:   {total_missing_before}")
    lines.append(f"Samples {'that would be fixed' if dry_run else 'fixed/relinked'}:  {total_fixed}")
    total_linked = sum(len(r.get('linked_names', [])) for r in results.values())
    if total_linked:
        word = "would be" if dry_run else "were"
        lines.append(
            f"  (of which {total_linked} {word} linked by absolute path, not collected, because a "
            "same-name file with a different size already exists in that project's Samples/Imported)"
        )
    lines.append(f"Still missing:           {total_still_missing}  ({unique_still_missing} unique file(s))")
    if total_pack_skipped:
        lines.append(
            f"Live Pack references:    {total_pack_skipped} ignored (Live resolves these from an "
            "installed Pack, not from the stored path)"
        )
    lines.append("=" * 70)
    lines.append("")
    lines.append("NOTE: counts are sample *references*, not files - one file used by 70 clips")
    lines.append("counts 70 times. The '(xN)' after each name is that reference count.")
    lines.append("")
    lines.append("NOTE: Ableton will usually report FEWER missing files than this report. Live's")
    lines.append("File Manager preferences let it auto-search your Library and a chosen search")
    lines.append("folder by filename, so it silently relinks references whose stored path is")
    lines.append("dead (e.g. samples that moved between drive letters). This report only asks")
    lines.append("whether the path stored in the set still resolves, which is the stricter")
    lines.append("question - and the one that matters if you ever move the project elsewhere.")
    lines.append("")

    # Aggregate the volumes that still-missing samples point at. Roots that
    # aren't currently mounted are the actionable ones: those projects most
    # likely just need the external drive plugged in, not a manual relink.
    offline_roots = {}
    online_roots = {}
    foreign_roots = {}
    for project_path, r in results.items():
        for root, info in (r.get('missing_roots') or {}).items():
            if info.get('foreign'):
                bucket = foreign_roots
            elif info.get('exists'):
                bucket = online_roots
            else:
                bucket = offline_roots
            entry = bucket.setdefault(root, {'refs': 0, 'names': set(), 'projects': set()})
            entry['refs'] += info.get('refs', 0)
            entry['names'] |= set(info.get('names', ()))
            entry['projects'].add(Path(project_path).name)

    if offline_roots or online_roots or foreign_roots:
        lines.append("-" * 70)
        lines.append("VOLUMES REFERENCED BY STILL-MISSING SAMPLES")
        lines.append("-" * 70)
    if offline_roots:
        lines.append("OFFLINE - these volumes are not currently mounted. The samples below are")
        lines.append("probably not lost; these projects just need that drive connected.")
        lines.append("")
        for root in sorted(offline_roots):
            entry = offline_roots[root]
            lines.append(
                f"  {root}  OFFLINE - {entry['refs']} ref(s), {len(entry['names'])} unique file(s), "
                f"{len(entry['projects'])} project(s):"
            )
            for name in sorted(entry['projects']):
                lines.append(f"      {name}")
        lines.append("")
    if online_roots:
        lines.append("ONLINE - these volumes are mounted, so the samples really are gone from")
        lines.append("the stored path and were not found anywhere in the sample index.")
        lines.append("")
        for root in sorted(online_roots):
            entry = online_roots[root]
            lines.append(
                f"  {root}  online  - {entry['refs']} ref(s), {len(entry['names'])} unique file(s), "
                f"{len(entry['projects'])} project(s)"
            )
        lines.append("")
    if foreign_roots:
        lines.append("NO DRIVE LETTER - these paths were saved by Live on macOS/Linux, so no")
        lines.append("local volume can satisfy them. They need the file re-indexed or relinked")
        lines.append("by hand; connecting a drive will not help.")
        lines.append("")
        for root in sorted(foreign_roots):
            entry = foreign_roots[root]
            lines.append(
                f"  {root} - {entry['refs']} ref(s), {len(entry['names'])} unique file(s), "
                f"{len(entry['projects'])} project(s):"
            )
            for name in sorted(entry['projects']):
                lines.append(f"      {name}")
        lines.append("")

    # Sort so problem projects (errors, then still-missing) surface first.
    def sort_key(item):
        _, r = item
        if not r['success']:
            return (0, 0)
        if r['missing_after'] > 0:
            return (1, -r['missing_after'])
        if r['missing_before'] > 0:
            return (2, 0)
        return (3, 0)

    for project_path, r in sorted(results.items(), key=sort_key):
        name = Path(project_path).name
        if not r['success']:
            lines.append(f"[ERROR] {name}")
            lines.append(f"    {r['error']}")
            lines.append("")
            continue

        if r['missing_before'] == 0:
            continue  # Don't clutter the report with clean projects; counted in the summary above.

        if r['missing_after'] == 0:
            status = "WOULD BE FULLY FIXED" if dry_run else "FULLY FIXED"
        else:
            unique = len(set(r['still_missing_names']))
            status = f"{unique} FILE(S) STILL MISSING / {r['missing_after']} refs"
        fixed_word = "would fix" if dry_run else "fixed"
        lines.append(f"[{status}] {name}  ({r['fixed']}/{r['missing_before']} refs {fixed_word})")
        project_roots = r.get('missing_roots') or {}
        if project_roots:
            rendered = [
                f"{root}{'' if info.get('exists') or info.get('foreign') else ' (OFFLINE)'}"
                for root, info in sorted(project_roots.items())
            ]
            lines.append(f"    Volumes needed:   {', '.join(rendered)}")
        fixed_label = "OK would collect: " if dry_run else "OK collected:     "
        linked_label = "-- would link:    " if dry_run else "-- linked (abs):  "
        for sample_name, count in sorted(Counter(r['fixed_names']).items()):
            suffix = f"  (x{count})" if count > 1 else ""
            lines.append(f"    {fixed_label}{sample_name}{suffix}")
        for sample_name, count in sorted(Counter(r.get('linked_names', [])).items()):
            suffix = f"  (x{count})" if count > 1 else ""
            lines.append(f"    {linked_label}{sample_name}{suffix}")
        for sample_name, count in sorted(Counter(r['still_missing_names']).items()):
            suffix = f"  (x{count})" if count > 1 else ""
            lines.append(f"    XX still missing: {sample_name}{suffix}")
        lines.append("")

    clean_projects = [Path(p).name for p, r in results.items() if r['success'] and r['missing_before'] == 0]
    if clean_projects:
        lines.append("-" * 70)
        lines.append(f"Projects with no missing media ({len(clean_projects)}):")
        for name in sorted(clean_projects):
            lines.append(f"    {name}")

    return "\n".join(lines)
