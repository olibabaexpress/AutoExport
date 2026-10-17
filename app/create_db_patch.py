"""Create sample database for fast searches and fixing broken sample paths."""

import json
import logging
import pathlib
from threading import Event

from abletoolz.misc import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

# Per-file record: name is always a string; size/last_modified come straight off
# os.stat() (int and float respectively) and round-trip through JSON as numbers.
SampleRecord = dict[str, str | int | float]
DatabaseT = dict[str, SampleRecord]


def get_all_audio_files(
    path: pathlib.Path, pause_event: Event | None = None
) -> list[pathlib.Path]:
    """Find all supported audio files in directory."""
    file_suffixes = [
        "*.aiff",
        "*.AIFF",
        "*.aif",
        "*.AIF",
        "*.wav",
        "*.WAV",
        "*.mp3",
        "*.MP3",
        "*.flac",
        "*.FLAC",
        "*.ogg",
        "*.OGG",
        "*.mp4",
        "*.MP4",
        "*.rx2",
    ]
    all_files: list[pathlib.Path] = []
    for file_type in file_suffixes:
        for audio_file in path.rglob(file_type):
            if pause_event is not None:
                pause_event.wait()
            all_files.append(audio_file)
    return all_files


def create_or_update_db(
    paths: list[str],
    db_path: pathlib.Path | None = None,
    progress_callback=None,
    pause_event: Event | None = None,
    ) -> pathlib.Path:
    """Search all samples and add to database, create new one if it doesn't exist.

    db {
        path_string: {
            file_size: sample file size,
            # Crc unsupported for now, can't figure out ableton's crc algorithm! This would allow for
            # near perfect matches, although file size and perfectly matching file name is probably good enough.
            crc: crc size
            last_modified: time of last file modification.
        }
    }
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    # Create the parent dir now, before the (potentially long) scan — a first run
    # on a fresh machine must not crash at write time after scanning everything.
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(
            f"Cannot create database directory {db_path.parent}. "
            f"Check permissions: {e}"
        ) from e
    logger.info(
        "Using db path %s. Creating database from scratch can take a while, please be patient. Updating "
        "an existing one is much faster!",
        db_path.resolve(),
    )
    db: DatabaseT
    if not db_path.exists():
        db = {}
    else:
        try:
            with db_path.open("r") as f:
                db = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise OSError(
                f"Could not read existing database at {db_path}: {e}"
            ) from e
    all_files = []

    if progress_callback:
        progress_callback("discovering", 0, len(paths))

    for path_index, path in enumerate(paths, start=1):
        if pause_event is not None:
            pause_event.wait()
        found = get_all_audio_files(pathlib.Path(path), pause_event)
        all_files.extend(found)

        if progress_callback:
            progress_callback(
                "discovering",
                path_index,
                len(paths),
            )

    # Remove any broken paths.
    to_remove: list[str] = []
    db_items = list(db.keys())
    db_total = len(db_items)

    # Plain loop, no tqdm: the frozen GUI is windowed (noconsole), where
    # sys.stdout/sys.stderr are None - tqdm's console write then crashes with
    # "'NoneType' object has no attribute 'write'". Progress goes through
    # progress_callback to the GUI label instead.
    for index, path in enumerate(db_items, start=1):
        if pause_event is not None:
            pause_event.wait()
        if not pathlib.Path(path).exists():
            to_remove.append(path)

        if progress_callback:
            progress_callback(
                "validating",
                index,
                db_total,
            )
    for path in to_remove:
        db.pop(path)

    file_total = len(all_files)

    for index, sample in enumerate(all_files, start=1):
        if pause_event is not None:
            pause_event.wait()
        if str(sample.resolve()) not in db:
            db[str(sample.resolve())] = {
                "name": sample.name,
                "size": sample.stat().st_size,
                "last_modified": sample.stat().st_mtime,
            }

        if progress_callback:
            progress_callback(
                "indexing",
                index,
                file_total,
            )
    # Write db!
    if pause_event is not None:
        pause_event.wait()
    try:
        with db_path.open("w") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except OSError as e:
        raise OSError(
            f"Could not write database to {db_path}. Check permissions: {e}"
        ) from e
    logger.info("Updated database at %s", db_path.resolve())
    return db_path


def load_db(db_path: pathlib.Path | None = None) -> DatabaseT:
    """Load db from json."""
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    if not db_path.exists():
        raise FileNotFoundError(f"Database {db_path} doesn't exist! Run --db with sample dir(s) first.")
    with db_path.open() as f:
        data: DatabaseT = json.load(f)
    return data
