"""
Organize Stems by Group - move exported stems into a folder tree that
mirrors the Live project's nested group/subgroup structure, and rename each
file according to the naming settings (BPM suffix, project name prefix).

Runs after stem export. The group/subgroup tree comes from TrackGroupManager
(abletoolz, reading the .als file directly), which is what makes nested
subgroups get their own folder instead of being flattened into their parent
group's folder.
"""

import os
import re
import logging
from collections import defaultdict, deque

from track_group_manager import TrackGroupManager
from project_and_file_naming import sanitize_filename
from rendered_files import move_rendered_file

logger = logging.getLogger("AutoExport")

AUDIO_EXTENSIONS = ('.wav', '.mp3', '.aif', '.aiff', '.flac', '.ogg')


class StemsOrganizerByGroup:
    """Moves/renames exported stems into group-based folders."""

    def __init__(self, stems_dir, ableton_working_prefix, project_path,
                 tempo=None, display_name=None,
                 include_bpm=False, include_project_name=False, name_suffix=""):
        """
        Args:
            stems_dir: Directory containing the just-exported stems.
            ableton_working_prefix: The base filename Ableton was given in
                the Save Audio File As dialog - every exported stem is named
                "<ableton_working_prefix> <track name>.wav", so this is what
                gets stripped off to recover the bare track name.
            project_path: Path to the .als file, parsed via abletoolz to
                resolve the group/subgroup tree.
            tempo: Detected project tempo (for the optional BPM suffix).
            display_name: Processed project name to use as the optional
                filename prefix (independent of ableton_working_prefix, which
                may have its own baked-in tempo/shortening for Ableton's
                dialog only).
            include_bpm: Append "_<tempo>bpm" to every stem filename.
            include_project_name: Prefix every stem filename with
                "<display_name> ".
            name_suffix: Appended to every stem filename (e.g. " (dry)").
        """
        self.stems_dir = stems_dir
        self.ableton_working_prefix = ableton_working_prefix
        self.project_path = project_path
        self.tempo = tempo
        self.display_name = display_name
        self.include_bpm = include_bpm
        self.include_project_name = include_project_name
        self.name_suffix = name_suffix or ""
        self.group_manager = TrackGroupManager(project_path)
        self.moved_count = 0
        self.renamed_count = 0
        self._name_counts = {}  # lowercased final base name -> times seen so far

    def organize(self, dry_run=False):
        logger.info("=" * 80)
        logger.info("ORGANIZING STEMS BY GROUP")
        logger.info(f"Stems directory: {self.stems_dir}")
        logger.info(f"Ableton working prefix: {self.ableton_working_prefix}")
        logger.info(f"Dry run: {dry_run}")

        folder_queue_for_name = self._build_track_folder_map()

        for file_name in sorted(os.listdir(self.stems_dir)):
            file_path = os.path.join(self.stems_dir, file_name)

            if os.path.isdir(file_path):
                continue
            if file_name.endswith('.asd'):
                continue  # moved alongside its .wav below
            if not file_name.lower().endswith(AUDIO_EXTENSIONS):
                continue

            track_name = self._extract_track_name(file_name)
            if not track_name:
                logger.debug(f"Could not extract track name from '{file_name}' - leaving in place")
                continue

            folder_parts, is_group = self._resolve_folder(track_name, folder_queue_for_name)

            self._move_stem(file_path, file_name, track_name, folder_parts, is_group, dry_run)

        logger.info(f"Files renamed: {self.renamed_count}")
        logger.info(f"Files moved: {self.moved_count}")
        logger.info("=" * 80)

    def _build_track_folder_map(self):
        """track name -> deque of (folder_parts, is_group), in tree order.

        A deque (rather than a single value) is required because Ableton
        doesn't guarantee unique track names - see the note in organize().
        """
        folder_queue_for_name = defaultdict(deque)
        for node, folder_parts in self.group_manager.walk():
            folder_queue_for_name[node.name].append(
                ([sanitize_filename(part) for part in folder_parts], node.is_group)
            )
        return folder_queue_for_name

    def _resolve_folder(self, track_name, folder_queue_for_name):
        """Look up (folder_parts, is_group) for an extracted track name.

        Falls back to stripping a trailing Ableton-added disambiguation
        suffix (" 2", "-2", etc.) if the exact name isn't found, so a track
        whose exported filename Ableton itself renamed to avoid overwriting
        a same-named sibling still lands in the right folder instead of
        silently falling back to the export root.
        """
        candidates = folder_queue_for_name.get(track_name)
        if not candidates:
            stripped = re.sub(r'[\s-]\d+$', '', track_name)
            if stripped != track_name:
                candidates = folder_queue_for_name.get(stripped)
        if candidates:
            # Two tracks can share a name (e.g. in different groups); since the
            # exported filename only carries the track name, matches are
            # consumed in tree order (first tree occurrence claimed first) so
            # a name collision doesn't silently overwrite an earlier match.
            return candidates.popleft() if len(candidates) > 1 else candidates[0]
        return [], False

    def _extract_track_name(self, file_name):
        """
        Recover the bare track name from an exported filename.

        Ableton names every rendered file "<ableton_working_prefix> <track
        name>.wav", so everything after the prefix (and a single space) is
        the track name.
        """
        if file_name.endswith('.wav'):
            base_name = file_name[:-4]
        else:
            return None

        pattern = re.escape(self.ableton_working_prefix) + r'\s+(.+)$'
        match = re.match(pattern, base_name)
        if match:
            return match.group(1)
        return None

    def _final_filename(self, track_name, is_group):
        """Build the on-disk base name (no extension) per naming settings."""
        name = track_name
        if is_group and not name.endswith(" (group)"):
            name = f"{name} (group)"
        if self.include_bpm and self.tempo:
            name = f"{name}_{self.tempo}bpm"
        if self.include_project_name and self.display_name:
            name = f"{self.display_name} {name}"

        # Tracks sharing a name (Ableton doesn't enforce uniqueness) get
        # numbered from the second occurrence onward.
        key = name.lower()
        seen = self._name_counts.get(key, 0) + 1
        self._name_counts[key] = seen
        if seen > 1:
            name = f"{name} {seen}"

        return sanitize_filename(f"{name}{self.name_suffix}")

    def _move_stem(self, file_path, file_name, track_name, folder_parts, is_group, dry_run):
        dest_dir = os.path.join(self.stems_dir, *folder_parts) if folder_parts else self.stems_dir
        if not dry_run:
            os.makedirs(dest_dir, exist_ok=True)

        new_base = self._final_filename(track_name, is_group)
        new_wav_name = f"{new_base}.wav"
        if new_wav_name != file_name:
            self.renamed_count += 1

        dest_path = os.path.join(dest_dir, new_wav_name)
        rel_dest = os.path.join(*folder_parts, new_wav_name) if folder_parts else new_wav_name
        logger.info(f"  {file_name} -> {rel_dest}")

        if not dry_run:
            move_rendered_file(file_path, dest_path)
        self.moved_count += 1

        asd_name = file_name + '.asd'
        asd_path = os.path.join(self.stems_dir, asd_name)
        if os.path.exists(asd_path):
            asd_dest_path = os.path.join(dest_dir, new_wav_name + '.asd')
            if not dry_run:
                move_rendered_file(asd_path, asd_dest_path)


def main():
    """CLI entry point, for testing the organizer against an already-exported folder."""
    import sys

    if len(sys.argv) < 3:
        print("Usage: python organize_stems_by_group.py <stems_dir> <ableton_working_prefix> <project_als_path> [--dry-run]")
        
        sys.exit(1)

    stems_dir = sys.argv[1]
    ableton_working_prefix = sys.argv[2]
    project_path = sys.argv[3] if len(sys.argv) > 3 else None
    dry_run = "--dry-run" in sys.argv

    if not os.path.exists(stems_dir):
        print(f"ERROR: Stems directory not found: {stems_dir}")
        sys.exit(1)
    if not project_path or not os.path.exists(project_path):
        print("ERROR: A valid project .als path is required to resolve the group structure")
        sys.exit(1)

    try:
        organizer = StemsOrganizerByGroup(stems_dir, ableton_working_prefix, project_path)
        organizer.organize(dry_run=dry_run)
        print("Stems organized successfully!")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
