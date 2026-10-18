


from gui_theme import enable_dark_title_bar, set_window_icon
import tkinter as tk
import gui_missingmedia_tools as gui_missingmedia_tools



class GuiwindowhelpersMixin:
    def _get_existing_tool_window(self, key):
        """Return an existing tool window, or None if it is closed."""
        if not hasattr(self, "_tool_windows"):
            self._tool_windows = {}

        win = self._tool_windows.get(key)

        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.after(50, win.focus_force)
                    return win
            except tk.TclError:
                pass

        self._tool_windows.pop(key, None)
        return None


    def _register_tool_window(self, key, win):
        """Track a tool window and remove it from the registry when closed."""
        if not hasattr(self, "_tool_windows"):
            self._tool_windows = {}

        self._tool_windows[key] = win

        def on_destroy(event):
            if event.widget is win:
                if self._tool_windows.get(key) is win:
                    self._tool_windows.pop(key, None)

        win.bind("<Destroy>", on_destroy, add="+")


    def _center_and_show_toplevel(self, win):
        """Center the tool window and bring it to the foreground."""
        win.withdraw()
        set_window_icon(win)
        win.after(100, lambda target=win: enable_dark_title_bar(target))
        win.transient(self.root)
        win.update_idletasks()

        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        ww = max(win.winfo_reqwidth(), win.winfo_width())
        wh = max(win.winfo_reqheight(), win.winfo_height())

        x = max(0, (sw - ww) // 2)
        y = max(0, (sh - wh) // 2)
        win.geometry(f"{ww}x{wh}+{x}+{y}")
        win.update_idletasks()
        win.deiconify()

        # Raise the window above the main application.
        win.lift()

        # Ask Windows to activate and focus the new window.
        win.after(50, win.focus_force)







    