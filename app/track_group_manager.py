"""
Track Group Manager - resolves the exact nested group/subgroup hierarchy of
an Ableton Live set directly from its .als XML via abletoolz.

This replaces an earlier pylive/OSC-based version that only exposed one flat
level of grouping (set.groups / group.tracks), so a subgroup's own tracks
ended up sorted into the *outer* group's folder alongside the subgroup's own
submix track, with no folder ever created for the subgroup itself. Reading
straight from the .als file's TrackGroupId chain (the same approach as
abletoolz's own track listing) gives the full nesting instead, however deep
it goes.
"""

import logging
import pathlib

from abletoolz.live_set import AbletonSet

logger = logging.getLogger("AutoExport")

GROUP_TRACK_TYPE = "GroupTrack"
# Track types that are ever rendered as separate stems. Return/Master tracks
# never appear as "All Individual Tracks" render output, so they are dropped
# rather than left as unmatched, folder-less nodes.
_EXPORTABLE_TYPES = ("MidiTrack", "AudioTrack", GROUP_TRACK_TYPE)


class TrackNode:
    """One track in the hierarchy, with its resolved children (if a group)."""

    __slots__ = ("id", "name", "type", "group_id", "children")

    def __init__(self, ableton_track):
        self.id = ableton_track.id
        self.name = ableton_track.name
        self.type = ableton_track.type
        self.group_id = ableton_track.group_id
        self.children = []

    @property
    def is_group(self):
        return self.type == GROUP_TRACK_TYPE

    def __repr__(self):
        return f"TrackNode({self.name!r}, type={self.type!r}, children={len(self.children)})"


class TrackGroupManager:
    """Parses an .als file's track list into a group/subgroup tree.

    Unlike the tracks pylive exposes over OSC, every node here carries its
    resolved parent-child relationship, so arbitrarily nested subgroups can
    be told apart from their parent group and from each other.
    """

    def __init__(self, project_path):
        self.project_path = project_path
        self.nodes_by_id = {}
        self.top_level = []  # TrackNode list, in document order
        self._build_hierarchy()

    def _build_hierarchy(self):
        try:
            live_set = AbletonSet(pathlib.Path(self.project_path))
            live_set.parse()
            tracks = live_set.tracks.load()
        except Exception as e:
            logger.error(f"Could not parse project structure via abletoolz: {e}")
            return

        for track in tracks:
            if track.type not in _EXPORTABLE_TYPES:
                continue
            self.nodes_by_id[track.id] = TrackNode(track)

        for node in self.nodes_by_id.values():
            parent = None
            if node.group_id not in (None, "-1"):
                parent = self.nodes_by_id.get(node.group_id)
            if parent is not None:
                parent.children.append(node)
            else:
                self.top_level.append(node)

    def walk(self):
        """Yield (node, folder_parts) for every track, matching the folder
        structure callers should build.

        ``folder_parts`` is the list of folder names (relative to the stems
        export root) the track's exported file belongs in. A group's own
        submix track shares a folder with its members - both live inside a
        folder named after the group - while nested subgroups get one more
        folder level for their own members, and so on:

            (root)
              group folder/                <- group_a's own folder
                group_a (group).wav        <- group_a's own submix track
                member track.wav           <- direct member of group_a
                subgroup folder/           <- group_b's own folder (nested)
                  group_b (group).wav
                  member of group_b.wav
              ungrouped track.wav          <- no folder_parts (root)
        """
        def _walk(nodes, chain):
            for node in nodes:
                if node.is_group:
                    own_folder = chain + [node.name]
                    yield node, own_folder
                    yield from _walk(node.children, own_folder)
                else:
                    yield node, chain

        yield from _walk(self.top_level, [])

    def get_all_group_names(self):
        """Every group/subgroup name found anywhere in the tree."""
        return [node.name for node in self.nodes_by_id.values() if node.is_group]
