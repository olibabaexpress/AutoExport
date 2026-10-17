"""Saved project-browser state and application shutdown."""

import json
import os
import copy
import queue
import tempfile
import time
import multiprocessing
import tkinter as tk
from tkinter import messagebox
from explorer_launch import InstanceServer
from paths import data_file

import logging

logger = logging.getLogger("AutoExport")

PROJECT_BROWSER_FILE = data_file("PROJECT_BROWSER.json")


class SessionMixin:

    def _start_instance_server(self):
        """Accept paths from later Explorer launches as if they were dropped."""
        self._instance_server = InstanceServer(self._ephemeral_browser)
        if self._instance_server.start():
            self._poll_instance_requests()

    def _poll_instance_requests(self):
        server = self._instance_server
        try:
            while True:
                paths = server.requests.get_nowait()
                for path in paths:
                    self.add_path(path)
                self._raise_window()
        except queue.Empty:
            pass
        self.root.after(300, self._poll_instance_requests)

    def _raise_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def _restore_project_browser(self):
        """Restore the projects and per-project options saved by the last GUI session."""
        try:
            with open(PROJECT_BROWSER_FILE, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not load saved project browser state")
            return

        if not isinstance(state, dict):
            logger.warning("Ignoring invalid project browser state: expected an object")
            return

        saved_sort = state.get("sort_by")
        if saved_sort == "Size":
            saved_sort = "Size - Largest to Smallest"
        if saved_sort in (
            "Alphabetical", "Last Modified", "Date Created",
            "Size - Smallest to Largest", "Size - Largest to Smallest",
        ):
            self.sort_by = saved_sort

        files = state.get("files", [])
        if not isinstance(files, list):
            logger.warning("Ignoring invalid project browser file list")
            return

        configs = state.get("projects", {})
        if not isinstance(configs, dict):
            configs = {}
        for raw_path in files:
            if not isinstance(raw_path, str):
                continue
            project_path = os.path.normpath(raw_path)
            if not os.path.isfile(project_path) or not project_path.casefold().endswith(".als"):
                continue
            folder = os.path.dirname(project_path)
            project_info = self.projects_by_folder.setdefault(
                folder, {"path": folder, "files": []}
            )
            if project_path not in project_info["files"]:
                project_info["files"].append(project_path)
            config = configs.get(project_path)
            if isinstance(config, dict):
                self.selected_projects[project_path] = {
                    **self._default_project_config(project_path),
                    **config,
                    "project_path": project_path,
                }
            else:
                self.selected_projects[project_path] = self._default_project_config(
                    project_path
                )

    def _save_project_browser(self):
        """Persist the visible project list and its export options."""
        if self._ephemeral_browser:
            return
        project_paths = {
            path
            for project in self.projects_by_folder.values()
            for path in project["files"]
        }
        state = {
            "files": sorted(project_paths),
            "sort_by": self.sort_by,
            "projects": {
                path: copy.deepcopy(config)
                for path, config in self.selected_projects.items()
                if path in project_paths
            },
        }
        future = self._cache_executor.submit(
            self._write_json_snapshot, PROJECT_BROWSER_FILE, state
        )
        future.add_done_callback(
            lambda completed: self._log_cache_save_failure(
                completed, "project browser"
            )
        )

    def on_close(self):
        self._closing = True
        server = getattr(self, "_instance_server", None)
        if server is not None:
            server.stop()
        try:
            self.task_manager.flush_pending_save()
        except Exception as error:
            logger.exception("Could not finish saving export tasks during shutdown")
            messagebox.showerror(
                "Could Not Save Queue",
                f"The export queue could not be saved:\n\n{error}",
                parent=self.root,
            )
        finally:
            self.task_manager.close()
            self._cache_executor.shutdown(wait=True)
        self._save_size_cache()
        # Stop accepting new meta tasks and cancel in-flight size estimates.
        self.meta_executor.shutdown(wait=False, cancel_futures=True)
        self.folder_sort_executor.shutdown(wait=False, cancel_futures=True)
        self._cancel_size_estimates()
        # Wake the submitter so it exits.
        self.size_jobs.put(None)

        # Give child worker processes a short grace period to exit cleanly.
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                children = multiprocessing.active_children()
                if not children:
                    break
                time.sleep(0.1)
            # If any remain, try terminating them politely then escalate to kill.
            children = multiprocessing.active_children()
            if children:
                for p in children:
                    try:
                        p.terminate()
                    except Exception:
                        logger.exception("Could not terminate child process %s", getattr(p, 'pid', p))
                # Short wait for termination
                time.sleep(0.2)
                children = multiprocessing.active_children()
                for p in children:
                    if p.is_alive():
                        try:
                            # Use kill if available (POSIX/modern Python). On Windows
                            # terminate() is usually sufficient but attempt kill for
                            # completeness.
                            p.kill()
                        except Exception:
                            logger.exception("Could not kill child process %s", getattr(p, 'pid', p))
        except Exception:
            logger.exception("Error while cleaning up child processes on exit")

        # Finally close the GUI window.
        try:
            self.root.destroy()
        except Exception:
            # If destroying the root triggers Tk errors during shutdown, log and ignore.
            logger.exception("Exception while destroying root window")


    @staticmethod
    def _write_json_snapshot(path, state):
        target_directory = os.path.dirname(os.path.abspath(path))
        temporary_file = None
        try:
            file_descriptor, temporary_file = tempfile.mkstemp(
                prefix=f".{os.path.basename(path)}.",
                suffix=".tmp",
                dir=target_directory,
                text=True,
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as state_file:
                json.dump(state, state_file, indent=2)
            for attempt in range(6):
                try:
                    os.replace(temporary_file, path)
                    temporary_file = None
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * (2 ** attempt))
        except OSError:
            try:
                if temporary_file and os.path.exists(temporary_file):
                    os.remove(temporary_file)
            except OSError:
                logger.exception(
                    "Could not remove temporary cache file %s", temporary_file
                )
            raise


    @staticmethod
    def _log_cache_save_failure(future, cache_name):
        try:
            future.result()
        except Exception:
            logger.exception("Could not save %s cache", cache_name)
