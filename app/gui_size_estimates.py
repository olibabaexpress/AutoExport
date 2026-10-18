


import json
import os
import logging
import queue
import time
from concurrent.futures import (
    CancelledError,
    ProcessPoolExecutor,
)
import project_structure as project_structure
import size_estimator as size_estimator
from paths import data_file
from find_clusters import TAIL_BARS

SIZE_CACHE_FILE = data_file("SIZE_ESTIMATE_CACHE.json")
# Leave a core free for the GUI (and Ableton, if an export is running).
SIZE_ESTIMATE_WORKERS = 1
# How long a queued project's mtime / recursive-nesting check stays fresh.
QUEUE_META_TTL = 30.0


logger = logging.getLogger(__name__)

class SizeEstimateMixin:
    """Size estimation: worker pool, estimate cache, queue metadata and readouts."""

    def _get_total_size_estimate(self):
        """Sum cached total_bytes for only the tasks currently in the queue."""
        total_bytes = 0
        for task in self.task_manager.get_tasks():
            size = self._task_size_bytes(task)
            if size is not None:
                total_bytes += size
        return total_bytes



    def _task_size_bytes(self, task):
        """Estimated bytes for a task, or None while pending / on error.
        Wet+Dry renders everything twice, so it shows double (an upper bound:
        a dry pass is skipped when there are no effects to switch off)."""
        cached = self.size_cache.get(self._size_cache_key(task))
        if not cached or "error" in cached:
            return None
        return cached["total_bytes"] * (2 if task.get("wet_dry", False) else 1)

    def _size_cache_key(self, task):
        """Cache key for a task's estimate, or None until the background
        inspection has read the project's mtime."""
        path = task["project_path"]
        meta = self.task_meta.get(path)
        if meta is None:
            return None
        s = self.size_render_settings
        return (path, meta["mtime"],
                bool(task.get("export_stems", False)),
                bool(task.get("export_main", False)),
                bool(task.get("include_return_and_main_effects", False)),
                s["sample_rate"], s["bit_depth"], s["channels"])
    def request_size_estimates(self):
        if (
            not self._initial_estimates_started
            or getattr(self, "_bulk_fix_priority_active", False)
        ):
            return
        for task in self.task_manager.get_tasks():
            self._initial_estimate_paths.add(task["project_path"])
            self._request_size_estimate(task)

    def _start_initial_size_estimates(self):
        """Begin queued project size estimates after initial UI setup."""
        if self._closing or self._initial_estimates_started:
            return
        self._initial_estimates_started = True
        self._initial_estimate_cursor = 0
        self._queue_initial_estimate_batch()

    def _queue_initial_estimate_batch(self):
        self._initial_estimate_after_id = None
        if self._closing or getattr(self, "_bulk_fix_priority_active", False):
            return
        tasks = self.task_manager.get_tasks()
        end = min(self._initial_estimate_cursor + 8, len(tasks))
        for task in tasks[self._initial_estimate_cursor:end]:
            self._initial_estimate_paths.add(task["project_path"])
            self._request_size_estimate(task)
        self._initial_estimate_cursor = end
        if end < len(tasks):
            self._initial_estimate_after_id = self.root.after(
                120, self._queue_initial_estimate_batch
            )

    def _request_size_estimate(self, task):
        if (
            self._closing
            or self.script_running
            or getattr(self, "_bulk_fix_priority_active", False)
        ):
            return
        key = self._size_cache_key(task)
        if key is None:
            logger.debug(f"size estimate skipped (no meta yet): {task.get('project_path')}")
            return
        if key in self.size_cache or key in self.size_pending:
            return
        self.size_pending.add(key)
        self.size_jobs.put((key, dict(task), self.size_generation))

    def _size_submitter(self):
        """Background thread: hands estimate jobs to the process pool."""
        while True:
            item = self.size_jobs.get()
            if self._closing:
                return
            if item is None:
                continue
            key, task, generation = item
            if key is None or task is None:
                continue
            if generation != self.size_generation:
                logger.debug(
                    "discarding stale size estimate request for %s (generation %s != %s)",
                    key[0], generation, self.size_generation,
                )
                continue
            if (
                self._closing
                or self.script_running
                or getattr(self, "_bulk_fix_priority_active", False)
            ):
                self.size_pending.discard(key)
                continue
            pool = None
            try:
                with self._size_pool_lock:
                    if (
                        self._closing
                        or self.script_running
                        or getattr(self, "_bulk_fix_priority_active", False)
                    ):
                        self.size_pending.discard(key)
                        continue
                    if self._size_pool is None:
                        self._size_pool = ProcessPoolExecutor(
                            max_workers=SIZE_ESTIMATE_WORKERS,
                            initializer=size_estimator.lower_worker_priority,
                        )
                    pool = self._size_pool
                    future = pool.submit(
                        size_estimator.estimate_task,
                        task,
                        dict(self.size_render_settings),
                    )
            except Exception as exc:
                # The pool broke (a worker died); start a fresh one next time.
                # Report the failed job instead of leaving it pending forever.
                logger.exception(
                    "Could not submit size estimate for %s", key[0]
                )
                with self._size_pool_lock:
                    if self._size_pool is pool:
                        self._size_pool = None
                self.size_pending.discard(key)
                self.size_results.put(
                    (key, generation, {"error": str(exc)})
                )
                continue
            future.add_done_callback(
                lambda f, key=key, generation=generation: self.size_results.put(
                    (
                        key,
                        generation,
                        self._future_result(
                            f, "size estimate worker", return_error=True
                        ),
                    )
                )
            )

    def _cancel_size_estimates(self):
        """Stop queued and active parses while the export script is running."""
        self.size_generation += 1
        self.size_pending.clear()
        while True:
            try:
                self.size_jobs.get_nowait()
            except queue.Empty:
                break

        with self._size_pool_lock:
            pool = self._size_pool
            self._size_pool = None
            if pool is not None:
                processes = list(
                    (getattr(pool, "_processes", None) or {}).values()
                )
                for proc in processes:
                    try:
                        proc.terminate()
                    except Exception:
                        logger.exception("Could not stop size-estimator worker")
                pool.shutdown(wait=False, cancel_futures=True)
                self._join_or_kill_size_workers(processes)

    def suspend_size_estimates_for_heavy_task(self):
        """Stop estimate workers before a CPU-heavy background task starts."""
        self._bulk_fix_priority_active = True
        self._cancel_size_estimates()

    def resume_size_estimates_after_heavy_task(self):
        """Resume estimates after a CPU-heavy background task releases priority."""
        if self._closing:
            return
        self._bulk_fix_priority_active = False
        self.request_size_estimates()
        tasks = self.task_manager.get_tasks()
        self._initial_estimate_paths.update(
            task["project_path"] for task in tasks
        )
        self._request_inspections(tasks)
        if (
            self._initial_estimates_started
            and self._initial_estimate_after_id is None
            and self._initial_estimate_cursor < len(tasks)
        ):
            self._initial_estimate_after_id = self.root.after(
                0, self._queue_initial_estimate_batch
            )

    @staticmethod
    def _join_or_kill_size_workers(processes):
        deadline = time.monotonic() + 1.0
        for proc in processes:
            proc.join(timeout=max(0.0, deadline - time.monotonic()))
        for proc in processes:
            if proc.is_alive():
                logger.warning(
                    "Size-estimator worker %s did not stop; killing it",
                    proc.pid,
                )
                try:
                    proc.kill()
                    proc.join(timeout=1.0)
                except OSError:
                    logger.exception(
                        "Could not kill size-estimator worker %s", proc.pid
                    )
                    continue
                if proc.is_alive():
                    logger.error(
                        "Size-estimator worker %s is still running", proc.pid
                    )

    @staticmethod
    def _future_result(future, operation="background worker", return_error=False):
        try:
            return future.result()
        except CancelledError:
            logger.debug("%s was cancelled", operation)
            return None
        except Exception as exc:
            logger.exception("%s failed", operation)
            if return_error:
                return {"error": str(exc)}
            return None

    @staticmethod
    def _inspect_project(path):
        """Background thread: filesystem facts the queue view needs."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        try:
            recursive = project_structure.plan_recursive_fix(path)
        except OSError:
            recursive = None
        return {"mtime": mtime, "recursive": recursive, "checked": time.monotonic()}

    def _request_inspections(self, tasks):
        """Queue background checks for projects never inspected or not
        inspected for QUEUE_META_TTL seconds."""
        now = time.monotonic()
        for task in tasks:
            path = task["project_path"]
            if path in self.meta_pending:
                continue
            meta = self.task_meta.get(path)
            if meta is not None and now - meta["checked"] < QUEUE_META_TTL:
                continue
            self.meta_pending.add(path)
            future = self.meta_executor.submit(self._inspect_project, path)
            future.add_done_callback(
                lambda f, path=path: self.meta_results.put(
                    (
                        path,
                        self._future_result(f, "queue project inspection"),
                    )
                )
            )

    def _invalidate_task_meta(self, paths):
        """Make the next queue refresh re-check these projects (their stale
        data stays on screen until the new check lands)."""
        for path in paths:
            meta = self.task_meta.get(path)
            if meta is not None:
                meta["checked"] = float("-inf")

    def poll_size_estimates(self):
        """UI thread: apply finished background work to just the affected rows."""
        try:
            self._poll_size_estimates_once()
        except Exception:
            # A self-rescheduling loop that re-arms only at its end dies forever
            # if anything above that re-arm throws once - which freezes every row
            # on "…" permanently. Trap it so one bad result can never stall the
            # estimator, and log it so the real cause is visible.
            logger.exception("poll_size_estimates failed; will keep polling")
        self.root.after(200, self.poll_size_estimates)

    def _poll_size_estimates_once(self):
        tasks_by_path = None
        changed = False
        while not self.meta_results.empty():
            try:
                path, meta = self.meta_results.get_nowait()
            except queue.Empty:
                break
            self.meta_pending.discard(path)
            if meta is None:
                continue
            self.task_meta[path] = meta
            tasks_by_path = tasks_by_path or {t["project_path"]: t for t in self.task_manager.get_tasks()}
            task = tasks_by_path.get(path)
            if task is not None:
                self._refresh_queue_row(task)
                if (
                    self._initial_estimates_started
                    and path in self._initial_estimate_paths
                ):
                    self._request_size_estimate(task)
                changed = True
        while not self.size_results.empty():
            try:
                key, generation, result = self.size_results.get_nowait()
            except queue.Empty:
                break
            if generation != self.size_generation:
                # Even if settings changed while parsing was in flight, the
                # project analysis is reusable when the project and export
                # options still match. Recalculate just the size for the
                # current audio settings instead of making this parse a loss.
                tasks_by_path = tasks_by_path or {
                    t["project_path"]: t for t in self.task_manager.get_tasks()
                }
                task = tasks_by_path.get(key[0]) if key is not None else None
                current_key = self._size_cache_key(task) if task is not None else None
                if (result is not None and current_key is not None
                        and key[:5] == current_key[:5]
                        and "error" not in result):
                    reused = False
                    settings = self.size_render_settings
                    try:
                        self.size_cache[current_key] = (
                            size_estimator.rescale_estimate(
                                result,
                                settings["sample_rate"],
                                settings["bit_depth"],
                                settings["channels"],
                            )
                        )
                        reused = True
                    except (KeyError, TypeError, ValueError):
                        logger.exception(
                            "Could not reuse in-flight size estimate for %s",
                            task["project_path"],
                        )
                    if reused:
                        self._refresh_queue_row(task)
                        changed = True
                continue

            self.size_pending.discard(key)
            if result is None:
                continue
            tasks_by_path = tasks_by_path or {
                t["project_path"]: t for t in self.task_manager.get_tasks()
            }
            task = tasks_by_path.get(key[0])
            if task is not None:
                current_key = self._size_cache_key(task)
                if current_key is not None and key != current_key:
                    logger.debug(
                        "size estimate stale (audio settings changed mid-flight), "
                        "ignoring: %s",
                        key[0],
                    )
                    continue
            self.size_cache[key] = result
            if task is not None:
                self._refresh_queue_row(task)
                changed = True

        folder_results_changed = False
        while not self.folder_sort_results.empty():
            try:
                folder_path, result = self.folder_sort_results.get_nowait()
            except queue.Empty:
                break
            self.folder_sort_pending.discard(folder_path)
            if isinstance(result, dict) and "error" not in result:
                self.folder_sort_cache[folder_path] = result
                folder_results_changed = True
        if folder_results_changed:
            self._schedule_folder_sort_refresh()
        if changed:
            self._update_size_total_label()
            if self.queue_sort_by in {
               # "Task - Light to Heavy",
               # "Task - Heavy to Light",
                "Size - Largest to Smallest",
                "Size - Smallest to Largest",
            } and self._apply_queue_sort():
                self.update_queue_tree()



    def _load_size_cache(self):
        if not os.path.exists(SIZE_CACHE_FILE):
            return
        try:
            with open(SIZE_CACHE_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception as e:
            print(f"Error loading size cache: {e}")
            return
        for r in records:
            if (not isinstance(r.get("result"), dict)
                    or "render_ranges" not in r["result"]
                    or r["result"].get("tail_padding_bars") != TAIL_BARS):
                continue
            key = (r["path"], r["mtime"], r["export_stems"], r["export_main"],
                   r["include_return_and_main_effects"], r["sample_rate"],
                   r["bit_depth"], r["channels"])
            self.size_cache[key] = r["result"]

    def _prune_size_cache(self):
        """Drop entries for projects that no longer exist or have since changed."""
        stale = []
        for key in self.size_cache:
            path, mtime = key[0], key[1]
            meta = self.task_meta.get(path)
            if meta is not None:
                current = meta["mtime"]
            else:
                try:
                    current = os.path.getmtime(path)
                except OSError:
                    current = None
            if current is None or current != mtime:
                stale.append(key)
        for key in stale:
            del self.size_cache[key]

    def _save_size_cache(self):
        self._prune_size_cache()
        records = []
        for key, result in self.size_cache.items():
            path, mtime, export_stems, export_main, include_rfx, sample_rate, bit_depth, channels = key
            records.append({
                "path": path, "mtime": mtime,
                "export_stems": export_stems, "export_main": export_main,
                "include_return_and_main_effects": include_rfx,
                "sample_rate": sample_rate, "bit_depth": bit_depth, "channels": channels,
                "result": result,
            })
        try:
            with open(SIZE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
        except Exception as e:
            print(f"Error saving size cache: {e}")

    def refresh_size_estimates(self):
        """Drop cached size estimates and re-run them under the new audio settings."""
        self.size_generation += 1
        self.size_pending.clear()
        settings = self.size_render_settings
        cached_by_analysis = {}
        for old_key, result in self.size_cache.items():
            if "error" not in result:
                cached_by_analysis[old_key[:5]] = result

        for task in self.task_manager.get_tasks():
            self._refresh_queue_row(task)
            current_key = self._size_cache_key(task)
            if current_key is None:
                continue

            # The parsed project data (render ranges, BPM and WAV count) is
            # independent of audio settings. Reuse any matching estimate and
            # update only its PCM byte math.
            result = cached_by_analysis.get(current_key[:5])
            if result is not None:
                try:
                    self.size_cache[current_key] = size_estimator.rescale_estimate(
                        result,
                        settings["sample_rate"],
                        settings["bit_depth"],
                        settings["channels"],
                    )
                except (KeyError, TypeError, ValueError):
                    logger.exception(
                        "Could not reuse cached size estimate for %s",
                        task["project_path"],
                    )
                else:
                    self._refresh_queue_row(task)
                    continue
            self._request_size_estimate(task)

        self._update_size_total_label()


    def update_size_readout(self):
        """Update the WAV export size readout."""
        try:
            total_tracks, total_size = size_estimator.estimate_queue()
            self.total_tracks = total_tracks
            self.total_size = total_size
            # Update GUI labels
            self.track_count_label.config(text=f"{total_tracks} WAVs")
            self.size_label.config(text=total_size)
        except Exception as e:
            self.track_count_label.config(text="—")
            self.size_label.config(text="Calculating...")
