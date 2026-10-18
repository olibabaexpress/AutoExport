"""Starting exports and watching the export worker / task file."""

import json
import os
from tkinter import messagebox
import subprocess
import time
import psutil
from export_path_config import get_export_path
from paths import data_file
from runtime_launch import export_command, export_creationflags
import ableton_build as ableton_build

import logging

logger = logging.getLogger("AutoExport")

TASK_FILE = data_file("EXPORT_TASKS.json")
SCRIPT_STATE_FILE = data_file("SCRIPT_STATE.json")


class ExportRunnerMixin:

    def start_export_from_queue(self):
        """Start export using items from EXPORT_TASKS.json"""
        try:
            self.task_manager.flush_pending_save()
        except Exception as error:
            logger.exception("Could not persist the queue before export")
            messagebox.showerror(
                "Could Not Start Export",
                f"The export queue could not be saved:\n\n{error}",
                parent=self.root,
            )
            return
        tasks = self.task_manager.get_tasks()
        
        if not tasks:
            return
        
        # Only get pending (not done) tasks
        pending_tasks = [t for t in tasks if not t.get("done", False)]
        if not pending_tasks:
            return

        if not ableton_build.is_installed(ableton_build.load_selected_build()):
            if not messagebox.askyesno(
                "Ableton Live Not Set",
                "No Ableton Live 12 executable is configured.\n\nLocate it now?",
                parent=self.root,
            ) or not self._prompt_for_ableton_exe(ableton_build.RELEASE):
                return

        if not get_export_path():
            if not messagebox.askyesno(
                "Export Folder Not Set",
                "No export folder is set.\n\nChoose one now?",
                parent=self.root,
            ) or not self.set_export_path():
                return
        
        summary = "Export Queue (Pending):\n\n"
        for task in pending_tasks:
            project_path = task["project_path"]
            project_name = os.path.basename(project_path)
            
            if project_name.endswith('.als'):
                project_name = project_name[:-4]
            
            task_types = []
            if task.get("export_stems"):
                task_types.append("Stems")
            if task.get("export_main"):
                task_types.append("Main")
            if task.get("collect_all_and_save"):
                task_types.append("Collect")
            
            task_str = " + ".join(task_types) if task_types else "Main"
            summary += f"• {project_name} ({task_str})\n"
        
      ##  if not self.confirm_export_dialog(summary):
      ##      return
        
        try:
            self.task_manager.commit_for_export()
            subprocess.Popen(
                export_command(TASK_FILE),
                creationflags=export_creationflags()
            )
        except OSError as e:
            messagebox.showerror("Could Not Start Export", str(e), parent=self.root)

    def start_export(self):
        """Start the export process - deprecated, use start_export_from_queue instead"""
        messagebox.showinfo("Queue Required", "Please use the queue system:\n\n1. Select projects from the tree\n2. Click 'Add to Queue'\n3. Arrange order if needed\n4. Click 'Start Export from Queue'")
        return

    def start_file_watcher(self):
        """Start watching EXPORT_TASKS.json for changes from main script"""
        self.check_task_file_changes()
        self.poll_size_estimates()

    def check_task_file_changes(self):
        """Periodically check for task updates and script state"""
        try:
            if not self.task_manager.detached and os.path.exists(TASK_FILE):
                current_mtime = os.path.getmtime(TASK_FILE)
                # Our own saves update known_mtime, so only changes made by
                # another process (the export script) trigger a reload.
                if current_mtime != self.task_manager.known_mtime:
                    before = {t["project_path"]: dict(t) for t in self.task_manager.get_tasks()}
                    self.task_manager.load_tasks()
                    # A task the script just finished or flagged has usually
                    # been re-saved by Collect All and Save: re-check it.
                    self._invalidate_task_meta(
                        t["project_path"] for t in self.task_manager.get_tasks()
                        if before.get(t["project_path"]) != t
                    )
                    self.update_queue_tree()
        except Exception as e:
            pass  # Silently ignore errors in file watcher
        
        # A stale SCRIPT_STATE.json must not hold the estimator disabled after
        # the export worker has exited or crashed.
        try:
            script_was_running = self.script_running
            self.script_running = self._is_export_worker_running()
            if self.script_running and not script_was_running:
                self._cancel_size_estimates()
            elif script_was_running and not self.script_running:
                tasks = self.task_manager.get_tasks()
                self._invalidate_task_meta(
                    task["project_path"] for task in tasks
                )
                self._request_inspections(tasks)
            
        except Exception as e:
            logger.exception("Could not update export worker state: %s", e)
        
        # Schedule next check (every 500ms)
        self.root.after(500, self.check_task_file_changes)

    @staticmethod
    def _is_export_process(pid):
        """Return whether pid belongs to this application's export worker."""
        try:
            process = psutil.Process(pid)
            name = (process.name() or "").casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False
        if name == "autoexportworker.exe":
            return True
        if name not in ("python.exe", "pythonw.exe"):
            return False
        try:
            return "orchestration.py" in " ".join(process.cmdline()).casefold()
        except (psutil.Error, OSError):
            return False

    def _is_export_worker_running(self):
        """Check the state-file PID, falling back to process discovery for legacy markers."""
        try:
            with open(SCRIPT_STATE_FILE, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            return False
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read export worker state file")
            state = {}

        pid = state.get("pid") if isinstance(state, dict) else None
        if isinstance(pid, int) and self._is_export_process(pid):
            return True

        # Older versions wrote no PID, so identify their worker by executable
        # name or the source script in its command line. Avoid querying command
        # lines for every process on every watcher tick: Windows may deny access.
        now = time.monotonic()
        if now - self._last_export_process_scan < 2.0:
            return self._export_process_scan_result

        found = False
        try:
            for process in psutil.process_iter():
                try:
                    name = (process.name() or "").casefold()
                except (psutil.Error, OSError):
                    continue
                if name == "autoexportworker.exe":
                    found = True
                    break
                if name in ("python.exe", "pythonw.exe"):
                    try:
                        command = " ".join(process.cmdline()).casefold()
                    except (psutil.Error, OSError):
                        continue
                    if "orchestration.py" in command:
                        found = True
                        break
        except (psutil.Error, OSError):
            logger.exception("Could not discover export worker processes")
        self._last_export_process_scan = now
        self._export_process_scan_result = found
        return found
