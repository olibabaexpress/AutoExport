import copy
import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor

import audio_settings
import project_structure
from gui_project_panel import ProjectFinder
from paths import data_file

logger = logging.getLogger("AutoExport")

TASK_FILE = data_file("EXPORT_TASKS.json")
QUEUE_CACHE_FILE = data_file("QUEUE_CACHE.json")
# Present while EXPORT_TASKS.json holds a right-click session's queue rather
# than the user's saved one.
EPHEMERAL_FLAG_FILE = data_file("QUEUE_EPHEMERAL.flag")

class TaskManager:
    """Manages the export queue.

    Normal sessions work on EXPORT_TASKS.json (the file the export worker
    reads and updates) and mirror every save into QUEUE_CACHE.json.
    Ephemeral sessions (Explorer right-click) start with an empty queue kept in
    memory only; it is copied into EXPORT_TASKS.json when an export starts, and
    the saved queue is restored from the cache on the next normal session.
    """
    
    def __init__(self, task_file=TASK_FILE, cache_file=QUEUE_CACHE_FILE,
                 flag_file=EPHEMERAL_FLAG_FILE, ephemeral=False):
        self.task_file = task_file
        self.cache_file = cache_file
        self.flag_file = flag_file
        self.ephemeral = ephemeral
        self._committed = False
        self.tasks = []
        self._save_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="task-save"
        )
        self._pending_save = None
        # mtime of the task file as of our last load/save; anything else means
        # another process (the export script) changed it.
        self.known_mtime = None
        if not ephemeral:
            self.load_tasks()

    @property
    def detached(self):
        """True while an ephemeral queue has not yet been handed to the worker."""
        return self.ephemeral and not self._committed

    def commit_for_export(self):
        """Copy an ephemeral queue into EXPORT_TASKS.json for the worker."""
        if not self.detached:
            return
        self._committed = True
        try:
            with open(self.flag_file, "w", encoding="utf-8") as flag:
                flag.write("ephemeral")
        except OSError:
            logger.exception("Could not mark the export queue as temporary")
        self.save_tasks(background=False)

    def load_tasks(self):
        """Load tasks from EXPORT_TASKS.json (or the cache after a right-click run)."""
        if self.detached:
            return
        restore_from_cache = (
            not self.ephemeral
            and os.path.exists(self.flag_file)
            and os.path.exists(self.cache_file)
        )
        source = self.cache_file if restore_from_cache else self.task_file
        if os.path.exists(source):
            try:
                self.known_mtime = os.path.getmtime(self.task_file) if os.path.exists(self.task_file) else None
                with open(source, 'r', encoding='utf-8') as f:
                    loaded_tasks = json.load(f)
                self.tasks = [
                    task for task in loaded_tasks
                    if not (
                        isinstance(task, dict)
                        and isinstance(task.get("project_path"), str)
                        and ProjectFinder.is_backup_path(task["project_path"])
                    )
                ]
                if restore_from_cache or len(self.tasks) != len(loaded_tasks):
                    self.save_tasks(background=not restore_from_cache)
            except Exception as e:
                print(f"Error loading tasks: {e}")
                self.tasks = []
        else:
            self.tasks = []
    
    def save_tasks(self, background=True):
        """Persist a task snapshot without blocking queue interactions."""
        if self.detached:
            return
        snapshot = copy.deepcopy(self.tasks)
        if not background:
            self._write_task_snapshot(snapshot)
            return

        self._pending_save = self._save_executor.submit(
            self._write_task_snapshot, snapshot
        )
        self._pending_save.add_done_callback(self._log_save_failure)

    @staticmethod
    def _atomic_write(path, tasks):
        temporary_file = f"{path}.tmp"
        try:
            with open(temporary_file, "w", encoding="utf-8") as task_file:
                json.dump(tasks, task_file, indent=2)
            os.replace(temporary_file, path)
        except Exception:
            try:
                if os.path.exists(temporary_file):
                    os.remove(temporary_file)
            except OSError:
                logger.exception("Could not remove temporary export queue")
            raise

    def _write_task_snapshot(self, tasks):
        self._atomic_write(self.task_file, tasks)
        self.known_mtime = os.path.getmtime(self.task_file)
        if not self.ephemeral:
            self._atomic_write(self.cache_file, tasks)
            try:
                os.remove(self.flag_file)
            except OSError:
                pass

    @staticmethod
    def _log_save_failure(future):
        try:
            future.result()
        except Exception:
            logger.exception("Background export-task save failed")

    def flush_pending_save(self):
        """Wait for queued task writes before another process reads the file."""
        if self._pending_save is not None:
            self._pending_save.result()

    def close(self):
        self._save_executor.shutdown(wait=True)
    
    def add_task(self, project_path, export_stems, export_main, collect_all_and_save,
                 include_return_and_main_effects=True, normalize=False, wet_dry=False):
        """Add or update task in EXPORT_TASKS.json"""
        self.add_tasks([{
            "project_path": project_path,
            "export_stems": export_stems,
            "export_main": export_main,
            "collect_all_and_save": collect_all_and_save,
            "include_return_and_main_effects": include_return_and_main_effects,
            "normalize": normalize,
            "wet_dry": wet_dry,
        }])

    def add_tasks(self, entries):
        """Add or update several tasks (dicts of add_task's arguments) with a
        single save, so adding hundreds of projects doesn't rewrite the file
        hundreds of times."""
        index = {task.get("project_path"): task for task in self.tasks}
        audio = audio_settings.load()
        for entry in entries:
            settings = {
                "export_stems": entry["export_stems"],
                "export_main": entry["export_main"],
                "collect_all_and_save": entry["collect_all_and_save"],
                "include_return_and_main_effects": entry.get("include_return_and_main_effects", True),
                "normalize": entry.get("normalize", False),
                "wet_dry": entry.get("wet_dry", False),
                # Audio format baked into each task at enqueue time, so a task
                # always renders with the settings that were active when it was
                # queued (mirrors how normalize etc. are snapshotted).
                "sample_rate": audio.sample_rate,
                "bit_depth": audio.bit_depth,
                "file_type": audio.file_type,
                "dither": audio.dither,
            }
            task = index.get(entry["project_path"])
            if task is not None:
                task.update(settings)
                task.setdefault("done", False)
            else:
                task = {"project_path": entry["project_path"], **settings, "done": False}
                self.tasks.append(task)
                index[entry["project_path"]] = task
        if entries:
            self.save_tasks()
    
    def remove_task(self, project_path):
        """Remove task from queue"""
        self.remove_tasks({project_path})

    def remove_tasks(self, project_paths):
        """Remove multiple queue items with one in-memory update and one save."""
        remaining = [
            task for task in self.tasks
            if task.get("project_path") not in project_paths
        ]
        if len(remaining) != len(self.tasks):
            self.tasks = remaining
            self.save_tasks()
    
    def move_task(self, from_index, to_index):
        """Move task in queue"""
        if 0 <= from_index < len(self.tasks) and 0 <= to_index < len(self.tasks):
            task = self.tasks.pop(from_index)
            self.tasks.insert(to_index, task)
            self.save_tasks()
    
    def clear_queue(self):
        """Clear all tasks from queue completely"""
        self.tasks = []
        self.save_tasks()

    def clear_failure(self, project_path):
        """Drop a task's recorded failure (it stays pending either way)."""
        for task in self.tasks:
            if task.get("project_path") == project_path:
                task.pop("failed", None)
                task.pop("failure", None)
        self.save_tasks()

    def remap_paths(self, old_root, new_root):
        """Point tasks under a moved folder at its new location."""
        for task in self.tasks:
            new_path = project_structure.remap_path(task["project_path"], old_root, new_root)
            if new_path != task["project_path"]:
                task["project_path"] = new_path
                task.pop("failed", None)
                task.pop("failure", None)
        self.save_tasks()
    
    def get_tasks(self):
        """Get all tasks"""
        return self.tasks