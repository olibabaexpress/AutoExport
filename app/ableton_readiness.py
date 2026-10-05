"""
Ableton Project Readiness Monitor Module
Clean, reusable module for detecting when Ableton projects are fully loaded.
Works as a better WinWait for Ableton Live.
"""

import time
import psutil
from collections import deque

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False


class AbletonReadinessMonitor:
    """Monitors Ableton Live process to detect when projects are fully loaded"""
    
    def __init__(self, poll_interval=0.5, window_size=10, timeout=120, window=None):
        self.poll_interval = poll_interval
        self.window_size = window_size
        self.timeout = timeout
        self.provided_window = window  # Optional pre-found window reference
        
        # Memory tracking
        self.memory_history = deque(maxlen=window_size)
        self.memory_stable_threshold = 50  # 50MB variance
        
        # State tracking
        self._last_io = None
        self._cached_proc = None
        self.window_responsive = True
    
    def get_ableton_window(self):
        """Find Ableton Live window (or return the provided one)"""
        # If window was provided at init, use it (avoids repeated lookups)
        if self.provided_window:
            try:
                # Quick check that provided window still exists
                _ = self.provided_window.width
                return self.provided_window
            except Exception:
                # Window became invalid, fall through to find it again
                pass
        
        if not HAS_PYGETWINDOW:
            return None
        
        try:
            windows = gw.getWindowsWithTitle('Ableton Live')
            return windows[0] if windows else None
        except Exception:
            return None
    
    def is_window_responding(self):
        """Check if Ableton window exists and is responsive"""
        window = self.get_ableton_window()
        if not window:
            return False
        
        try:
            _ = window.width
            _ = window.height
            return True
        except Exception:
            return False
    
    def get_ableton_process(self):
        """Get the main Ableton Live process.

        The handle is cached because this is polled twice a second while
        waiting for readiness, and a full psutil.process_iter() scan per poll
        is by far the most expensive part of that loop.
        """
        cached = getattr(self, '_cached_proc', None)
        if cached is not None:
            try:
                if cached.is_running():
                    return cached
            except Exception:
                pass
            self._cached_proc = None

        try:
            ableton_procs = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if 'Ableton Live' in proc.name():
                        self._cached_proc = proc
                        return proc
                    elif 'Ableton' in proc.name():
                        ableton_procs.append(proc)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            # Return largest process by memory if main process not found
            if ableton_procs:
                proc = max(ableton_procs, key=lambda p: p.memory_info().rss)
                self._cached_proc = proc
                return proc
        except Exception:
            pass
        return None
    
    def check_memory_stability(self):
        """Check if Ableton memory usage has stabilized"""
        proc = self.get_ableton_process()
        if not proc:
            return False, 0
        
        try:
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            self.memory_history.append(mem_mb)
            
            if len(self.memory_history) < 3:
                return False, mem_mb
            
            # Calculate variance in last N readings
            avg = sum(self.memory_history) / len(self.memory_history)
            variance = sum((x - avg) ** 2 for x in self.memory_history) / len(self.memory_history)
            
            return variance < self.memory_stable_threshold, mem_mb
        except Exception:
            return False, 0
    
    def is_ready(self):
        """Check if Ableton project is ready (memory stable is the reliable indicator)"""
        mem_stable, _ = self.check_memory_stability()
        return mem_stable
    
    def wait_for_ready(self, verbose=False):
        """
        Wait for Ableton project to be fully loaded.
        
        Args:
            verbose: If True, print status updates to console
        
        Returns:
            Tuple: (success: bool, elapsed_from_window_detection: float)
            Returns (True, time_in_seconds) if ready
            Returns (False, time_elapsed) if timeout
        """
        start_time = time.time()
        last_print_time = start_time
        window_detect_time = None
        
        if verbose:
            print("Waiting for Ableton project to be ready...")
        
        # First, wait for Ableton window to appear
        while time.time() - start_time < self.timeout:
            if self.is_window_responding():
                window_detect_time = time.time()
                break
            
            if verbose and time.time() - last_print_time > 5:
                elapsed = time.time() - start_time
                print(f"  [{elapsed:.0f}s] Waiting for Ableton window...")
                last_print_time = time.time()
            
            time.sleep(0.5)
        else:
            if verbose:
                print("ERROR: Ableton window did not appear within timeout")
            return False, time.time() - start_time
        
        if verbose:
            print("Ableton window detected. Monitoring readiness...")
        
        # Now wait for project to be ready
        while time.time() - start_time < self.timeout:
            mem_stable, mem_mb = self.check_memory_stability()
            if mem_stable:
                if verbose:
                    elapsed_from_window = time.time() - window_detect_time
                    print(f"Project ready! ({elapsed_from_window:.1f}s after window detection)")
                return True, time.time() - window_detect_time
            
            if verbose and time.time() - last_print_time > 2:
                elapsed_from_window = time.time() - window_detect_time
                print(f"  [{elapsed_from_window:.1f}s] Memory: [WAIT] ({mem_mb:.0f}MB)")
                last_print_time = time.time()
            
            time.sleep(self.poll_interval)
        
        if verbose:
            print("ERROR: Timeout waiting for Ableton project to be ready")
        return False, time.time() - window_detect_time


def wait_for_ableton_ready(timeout=120, verbose=False, window=None):
    """
    Simple function interface: Wait for Ableton project to be fully loaded.
    
    This is equivalent to a sophisticated WinWait for Ableton that detects
    project readiness rather than just window appearance.
    
    Args:
        timeout: Maximum time to wait in seconds (default 120)
        verbose: If True, print status updates (default False)
        window: Optional pre-found window reference (if None, will search for it)
    
    Returns:
        Tuple: (success: bool, elapsed_time_from_window_detection: float)
        Example:
            success, elapsed = wait_for_ableton_ready(timeout=60, verbose=True)
            if success:
                print(f"Project ready in {elapsed:.1f}s after window detection")
            else:
                print(f"Timeout after {elapsed:.1f}s")
    
    Usage:
        from ableton_readiness import wait_for_ableton_ready
        
        # Simple: module finds window
        success, elapsed = wait_for_ableton_ready(timeout=60, verbose=True)
        
        # Or pass pre-found window (avoids redundant window searches)
        import pygetwindow as gw
        ableton_window = gw.getWindowsWithTitle('Ableton Live')[0]
        success, elapsed = wait_for_ableton_ready(timeout=60, window=ableton_window)
    """
    monitor = AbletonReadinessMonitor(timeout=timeout, window=window)
    return monitor.wait_for_ready(verbose=verbose)


if __name__ == "__main__":
    # Demo usage
    success, elapsed = wait_for_ableton_ready(timeout=120, verbose=True)
    if success:
        print(f"\n[OK] Ableton is ready! ({elapsed:.1f}s after window detection)")
    else:
        print(f"\n[FAIL] Timeout waiting for Ableton ({elapsed:.1f}s elapsed)")
