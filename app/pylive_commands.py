"""
pylive (OSC) track/device commands.

Thin wrappers around a `live.Set` object for common track-level
operations: unmuting/unsoloing all tracks, disabling external
instrument/audio-effect devices, and reading the current tempo.
"""

import logging
import time

logger = logging.getLogger("AutoExport")


def unmute_all_tracks(set):    
    """Unmute all tracks in the current Ableton Live set"""
    

    try:
        # Try direct OSC query first, fallback to locally-cached track list
        try:
            num_tracks = set.num_tracks
            
        except:
            # Fallback: use local track list from scan (more reliable on first project)
            num_tracks = len(set.tracks)
            
        
        for i in range(num_tracks):
            try:
                set.tracks[i].mute = False
                set.tracks[i].solo = False
                set.tracks[i].arm = False
            except Exception as e:
                logger.debug(f"Note: Could not unmute return track {i}: {e}")
                continue
        
        logger.info("Unmuted all tracks successfully")
        return True
    except Exception as e:
        logger.info(f"Error unmuting tracks: {e}")
        return False

def unsolo_all_tracks(set):    
    """Unsolo all tracks in the current Ableton Live set"""
    
    try:
        # Try direct OSC query first, fallback to locally-cached track list
        try:
            num_tracks = set.num_tracks
            
        except:
            # Fallback: use local track list from scan (more reliable on first project)
            num_tracks = len(set.tracks)
            
        
        for i in range(num_tracks): 
            try:
                set.tracks[i].solo = False
                set.tracks[i].arm = False
            except Exception as e:
                logger.debug(f"Note: Could not unsolo return track {i}: {e}")
                continue
        
        logger.info("Unsoloed all tracks successfully")
        return True
    except Exception as e:
        logger.info(f"Error unsoloing tracks: {e}")
        return False

def disable_external_devices(set):
    """Disable all Ext. Instrument and Ext. Audio Effect devices in the current set."""
    
    disabled_count = 0
    
    try:
        tracks_list = list(set.tracks)
    except Exception as e:
        logger.info(f"Warning: Could not get tracks list: {e}")
        return 0
    
    try:
        for track_obj in tracks_list:
            try:
                devices_list = list(track_obj.devices)
                track_name = track_obj.name
            except Exception as e:
                logger.debug(f"Note: Could not access track info: {e}")
                continue
            
            for device in devices_list:
                try:
                    if device.name in ["Ext. Instrument", "Ext. Audio Effect"]:
                        device.set_parameter('Device On', 0)
                        logger.info(f"Disabled '{device.name}' on track '{track_name}'")
                        disabled_count += 1
                except Exception as e:
                    logger.debug(f"Note: Error disabling device: {e}")
                    continue
        
        if disabled_count > 0:
            logger.info(f"Disabled {disabled_count} external device(s)")
        else:
            logger.info("No external devices found to disable")
        
        return disabled_count
        
    except Exception as e:
        logger.info(f"Error in disable_external_devices: {e}")
        return 0


# Live.Device.DeviceType values as reported by AbletonOSC's /devices/type
# (0 undefined, 1 instrument, 2 audio effect, 4 MIDI effect).
DEVICE_TYPE_AUDIO_EFFECT = 2

# Class names of MIDI effects (Live's own, MIDI Effect Racks, Max for Live MIDI
# devices). Checked as well as the type so a MIDI effect is never switched off.
MIDI_EFFECT_CLASS_NAMES = {"MidiEffectGroupDevice", "MxDeviceMidiEffect"}


def _is_midi_effect_class(class_name):
    return class_name in MIDI_EFFECT_CLASS_NAMES or str(class_name).startswith("Midi")


def _is_audio_effect(device_type, class_name):
    if _is_midi_effect_class(class_name):
        return False
    try:
        return int(device_type) == DEVICE_TYPE_AUDIO_EFFECT
    except (TypeError, ValueError):
        return str(device_type).lower() == "audio_effect"


MAIN_DEVICE_PREFIX = "/live/master_track/device"
TRACK_DEVICE_PREFIX = "/live/device"

# OSC set commands get no reply, so a change is confirmed by reading it back.
VERIFY_TIMEOUT = 5.0


def _has_device_on(live, address_prefix, ids, name, where):
    """True if parameter 0 is "Device On" (it is for Live devices and
    plug-ins); checked so nothing else is ever changed by mistake. `ids`
    addresses the device: (track_index, device_index) for tracks,
    (device_index,) for main."""
    param_name = live.query(f"{address_prefix}/get/parameter/name", ids + (0,))[len(ids) + 1]
    if param_name != "Device On":
        logger.debug(f"Skipped '{name}' on {where}: parameter 0 is '{param_name}'")
        return False
    return True


def _get_device_on(live, address_prefix, ids):
    """True/False for a device's "Device On" state."""
    value = live.query(f"{address_prefix}/get/parameter/value", ids + (0,))[len(ids) + 1]
    return float(value) >= 0.5


def _set_device_on(live, address_prefix, ids, on):
    live.cmd(f"{address_prefix}/set/parameter/value", ids + (0, 1 if on else 0))


def _wait_for_device_state(live, address_prefix, ids_list, on, timeout=VERIFY_TIMEOUT):
    """Poll until every device in `ids_list` reads back as `on`. True if they
    all did within `timeout`."""
    pending = list(ids_list)
    deadline = time.monotonic() + timeout
    while pending:
        still = []
        for ids in pending:
            try:
                if _get_device_on(live, address_prefix, ids) != on:
                    still.append(ids)
            except Exception:
                still.append(ids)
        pending = still
        if not pending:
            return True
        if time.monotonic() >= deadline:
            logger.warning(f"{len(pending)} device(s) did not switch {'on' if on else 'off'} "
                           f"within {timeout}s: {pending}")
            return False
        time.sleep(0.2)
    return True


def _switch_off(live, address_prefix, ids, name, where):
    """Switch a device off if it is on. Returns True if a command was sent."""
    try:
        if not _has_device_on(live, address_prefix, ids, name, where):
            return False
        if not _get_device_on(live, address_prefix, ids):
            logger.debug(f"'{name}' on {where} is already off")
            return False
        _set_device_on(live, address_prefix, ids, False)
        logger.debug(f"Disabled '{name}' on {where}")
        return True
    except Exception as e:
        logger.debug(f"Could not disable '{name}' on {where}: {e}")
        return False


def disable_audio_effects(set, verify=True):
    """Switch off every top-level audio effect device on every track.

    Instruments and MIDI effects are left on, and so are return tracks and the
    main track: AbletonOSC's /live/track/* addresses index song.tracks, which
    contains only the regular audio/MIDI/group tracks.

    Queries Live directly instead of using set.tracks[...].devices, because
    pylive's file scan attaches a group track's devices to the previous track.
    Effects nested inside rack chains aren't visited individually (switching
    off an Audio Effect Rack bypasses everything inside it).

    With `verify`, waits until every switched device reads back as off.

    Returns the number of devices switched off (devices already off aren't
    counted), or None if the track count couldn't be read or verification
    failed.
    """
    live = set.live
    switched = []

    try:
        num_tracks = set.num_tracks
    except Exception as e:
        logger.info(f"Could not read track count, audio effects left as they are: {e}")
        return None

    for track_index in range(num_tracks):
        try:
            names = live.query("/live/track/get/devices/name", (track_index,))[1:]
            if not names:
                continue
            types = live.query("/live/track/get/devices/type", (track_index,))[1:]
            class_names = live.query("/live/track/get/devices/class_name", (track_index,))[1:]
        except Exception as e:
            logger.debug(f"Could not list devices on track {track_index}: {e}")
            continue

        for device_index, (name, device_type, class_name) in enumerate(zip(names, types, class_names)):
            if not _is_audio_effect(device_type, class_name):
                continue
            ids = (track_index, device_index)
            if _switch_off(live, TRACK_DEVICE_PREFIX, ids, name, f"track {track_index}"):
                switched.append(ids)

    if verify and switched and not _wait_for_device_state(live, TRACK_DEVICE_PREFIX, switched, False):
        return None
    logger.info(f"Disabled {len(switched)} audio effect(s)")
    return len(switched)


def get_main_devices(set):
    """Devices on the main (master) track, in chain order, as dicts with
    index, name, class_name and on (True/False, or None if the device has no
    "Device On" parameter).

    Needs the /live/master_track/* handlers added to AbletonOSC's track.py
    (stock AbletonOSC can't reach the main track). Returns None if those
    aren't available.
    """
    live = set.live
    try:
        names = live.query("/live/master_track/get/devices/name")
        class_names = live.query("/live/master_track/get/devices/class_name") if names else []
    except Exception as e:
        logger.info("Could not list main track devices; is the patched AbletonOSC "
                    f"(with /live/master_track handlers) installed and Live restarted? {e}")
        return None

    devices = []
    for device_index, (name, class_name) in enumerate(zip(names, class_names)):
        ids = (device_index,)
        on = None
        try:
            if _has_device_on(live, MAIN_DEVICE_PREFIX, ids, name, "main track"):
                on = _get_device_on(live, MAIN_DEVICE_PREFIX, ids)
        except Exception as e:
            logger.info(f"Could not read the on/off state of '{name}' on the main track "
                        f"(is the patched AbletonOSC up to date?): {e}")
            return None
        devices.append({"index": device_index, "name": name, "class_name": class_name, "on": on})
    return devices


def enable_main_devices_named(set, text, verify=True):
    """Switch on every disabled main-track device whose name contains `text`
    (case-insensitive). Returns the names switched on, or None on failure."""
    devices = get_main_devices(set)
    if devices is None:
        return None
    live = set.live
    needle = text.lower()
    switched = [d for d in devices if d["on"] is False and needle in str(d["name"]).lower()]
    for d in switched:
        _set_device_on(live, MAIN_DEVICE_PREFIX, (d["index"],), True)
        logger.info(f"Enabled '{d['name']}' on the main track")
    if verify and switched and not _wait_for_device_state(
            live, MAIN_DEVICE_PREFIX, [(d["index"],) for d in switched], True):
        return None
    return [d["name"] for d in switched]


def get_enabled_main_effects(set):
    """Names of the switched-on devices on the main track, in chain order,
    or None if they can't be read."""
    devices = get_main_devices(set)
    if devices is None:
        return None
    return [d["name"] for d in devices if d["on"] and not _is_midi_effect_class(d["class_name"])]


def disable_main_effects(set, verify=True):
    """Switch off every device on the main (master) track.

    MIDI effects can't live on the main track, but are still skipped
    defensively. With `verify`, waits until every switched device reads back
    as off.

    Returns the number of devices switched off (devices already off aren't
    counted), or None if AbletonOSC doesn't support the main track or
    verification failed.
    """
    live = set.live
    try:
        names = live.query("/live/master_track/get/devices/name")
        class_names = live.query("/live/master_track/get/devices/class_name") if names else []
    except Exception as e:
        logger.info("Could not list main track devices; is the patched AbletonOSC "
                    f"(with /live/master_track handlers) installed and Live restarted? {e}")
        return None

    switched = []
    for device_index, (name, class_name) in enumerate(zip(names, class_names)):
        if _is_midi_effect_class(class_name):
            continue
        if _switch_off(live, MAIN_DEVICE_PREFIX, (device_index,), name, "main track"):
            switched.append((device_index,))

    if verify and switched and not _wait_for_device_state(live, MAIN_DEVICE_PREFIX, switched, False):
        return None
    logger.info(f"Disabled {len(switched)} main track effect(s)")
    return len(switched)


def get_tempo(set):
    """Get the current tempo from Ableton Live set"""
    
    try:
        
        tempo = set.tempo
        if tempo is None:
            return None
            
        if int(tempo * 10) % 10 == 0:
            tempo = str(int(tempo))
        else:
            tempo = "{:.2f}".format(float(tempo))
        return tempo
    except Exception as e:
        logger.debug(f"Error getting tempo: {e}")
        return None
