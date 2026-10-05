import os
import time
from pywinauto import Application
import pygetwindow as gw
from PIL import ImageGrab

def capture_ableton_window(window_title='Ableton Live 12 Suite', output_path='ableton_window.png'):
    """
    Captures the Ableton window and saves it as a screenshot.
    
    Args:
        window_title: The title of the Ableton window to capture
        output_path: Where to save the screenshot (default: ableton_window.png)
    
    Returns:
        dict: {
            'success': bool,
            'message': str,
            'path': str (path to saved file if successful),
            'window_info': dict with window details
        }
    """
    try:
        # Find all windows matching the title (partial match)
        windows = gw.getWindowsWithTitle(window_title)
        
        if not windows:
            # Try a broader search
            windows = gw.getWindowsWithTitle('Ableton Live')
            
        if not windows:
            return {
                'success': False,
                'message': f'No window found with title containing: {window_title}',
                'path': None,
                'window_info': None
            }
        
        win = windows[0]  # Get the first matching window
        print(f"Found window: {win.title}")
        
        # Activate the window
        win.activate()
        time.sleep(0.5)  # Give it time to activate
        
        # Get the bounding box
        bbox = (win.left, win.top, win.right, win.bottom)
        print(f"Capturing from bbox: {bbox}")
        
        # Capture the screenshot
        screenshot = ImageGrab.grab(bbox=bbox)
        
        # Save the screenshot
        screenshot.save(output_path)
        
        abs_path = os.path.abspath(output_path)
        
        return {
            'success': True,
            'message': f'Screenshot saved successfully: {output_path}',
            'path': abs_path,
            'window_info': {
                'title': win.title,
                'left': win.left,
                'top': win.top,
                'width': win.width,
                'height': win.height,
                'right': win.right,
                'bottom': win.bottom
            }
        }
    
    except Exception as e:
        return {
            'success': False,
            'message': f'Error capturing Ableton window: {str(e)}',
            'path': None,
            'window_info': None
        }


def list_all_windows():
    """List all currently open windows - helpful for finding exact window titles"""
    windows = gw.getAllWindows()
    ableton_windows = [w.title for w in windows if 'ableton' in w.title.lower()]
    return ableton_windows



def win_activate_ableton():
    """Activate the Ableton Live window and bring it to focus."""
    import ctypes
    import subprocess
    
    try:
        # Find all windows with 'Ableton Live' in the title
        windows = gw.getWindowsWithTitle('Ableton Live')
        
        if not windows:
            print("✗ No Ableton Live window found")
            return False
        
        win = windows[0]
        print(f"Found: {win.title}")
        print(f"Window handle: {win._hWnd}")
        
        # Method 1: Try pygetwindow activate
        print("  → Activating with pygetwindow...")
        win.activate()
        time.sleep(0.2)
        
        # Method 2: Force to foreground with Windows API
        print("  → Bringing to foreground with SetForegroundWindow...")
        ctypes.windll.user32.SetForegroundWindow(win._hWnd)
        time.sleep(0.2)
        
        # Method 3: Send Alt+Tab if still not focused (nuclear option)
        print("  → Ensuring focus with keyboard input...")
        import pyautogui
        pyautogui.press('tab')
        time.sleep(0.1)
        
        print(f"✓ Ableton window activated and focused")
        return True
        
    except Exception as e:
        print(f"✗ Error activating Ableton window: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__": 

    win_activate_ableton()
    # Try to capture the Ableton window


    result = capture_ableton_window()
    
    if result['success']:
        print(f"✓ {result['message']}")
        print(f"  Window info: {result['window_info']}")
    else:
        print(f"✗ {result['message']}")
        print("\nAvailable Ableton windows:")
        ableton_windows = list_all_windows()
        if ableton_windows:
            for title in ableton_windows:
                print(f"  - {title}")
        else:
            print("  No Ableton windows found")