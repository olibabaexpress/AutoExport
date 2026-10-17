"""
Project/file naming helpers.

Pure text-processing functions used to build export filenames and shorten
project names according to the AHK-derived naming rules. No window
automation or file-system side effects beyond reading a file's modified
date.
"""

import re
import os
import logging
from datetime import datetime

logger = logging.getLogger("AutoExport")

# Fixed English stopword vocabulary; no corpus download is needed for naming.
ENGLISH_STOPWORDS = frozenset("""
a about above after again against ain all am an and any are aren aren't as at
be because been before being below between both but by can couldn couldn't d
did didn didn't do does doesn doesn't doing don don't down during each few for
from further had hadn hadn't has hasn hasn't have haven haven't having he he'd
he'll her here hers herself he's him himself his how i i'd if i'll i'm in into
is isn isn't it it'd it'll it's its itself i've just ll m ma me mightn mightn't
more most mustn mustn't my myself needn needn't no nor not now o of off on once
only or other our ours ourselves out over own re s same shan shan't she she'd
she'll she's should shouldn shouldn't should've so some such t than that
that'll the their theirs them themselves then there these they they'd they'll
they're they've this those through to too under until up ve very was wasn
wasn't we we'd we'll we're were weren weren't we've what when where which
while who whom why will with won won't wouldn wouldn't y you you'd you'll
your you're yours yourself yourselves you've
""".split())

def append_version_to_filename(filename, ableton_version):
    """Append Ableton version to filename before .als extension."""
    if filename.endswith('.als'):
        base = filename[:-4]
        return f"{base} (AL{ableton_version}).als"
    else:
        return f"{filename} (AL{ableton_version})"

def sanitize_filename(name):
    """Remove invalid filename characters"""
    return re.sub(r'[<>:"/\\|?*]', '', name)

# Matches a "(AL<version>)" tag such as "(AL12.3)" or "(AL12.4 BETA)".
# Version-agnostic and case-insensitive so it strips a tag from any Live
# build, not just whatever version is currently running.
_AL_VERSION_TAG_RE = re.compile(r"\(al[^)]*\)", re.IGNORECASE)


def strip_al_version_tag(text):
    """Remove an existing "(AL<version>)" tag (added by
    append_version_to_filename) from a project name, if present. Shared by
    shorten_project_name() and build_main_mix_filename() so exported
    filenames/folders never end up with the raw .als file's version tag
    baked into them.
    """
    return _AL_VERSION_TAG_RE.sub("", text)


def resolve_directory_collision(path):
    """
    Given a desired directory path that might already exist (e.g. a leftover
    empty folder from a previous run that was interrupted before cleanup),
    return a path that's safe to create or rename onto:

      - if it doesn't exist, return it unchanged.
      - if it exists and is an empty directory, remove the empty leftover
        and return the same path, so the caller's rename/mkdir lands
        cleanly instead of raising WinError 183.
      - if it exists and has content, append " (2)", " (3)", ... until a
        free name is found, so real data is never overwritten or lost.
    """
    if not os.path.exists(path):
        return path

    if os.path.isdir(path) and not os.listdir(path):
        try:
            os.rmdir(path)
            return path
        except OSError:
            pass  # not removable (permissions, race) - fall through to numbering

    n = 2
    while True:
        candidate = f"{path} ({n})"
        if not os.path.exists(candidate):
            return candidate
        n += 1

def get_project_modification_date(project_path):
    """Get the last modified date of the project file (DD-MM-YY format)"""
    try:
        mod_time = os.path.getmtime(project_path)
        date_obj = datetime.fromtimestamp(mod_time)
        return date_obj.strftime("%d-%m-%y")
    except Exception as e:
        logger.info(f"Error getting file modification date: {e}")
        return datetime.now().strftime("%d-%m-%y")

def remove_stopwords(text):
    """Lowercase and remove English stopwords, matching whole whitespace-separated words."""
    return ' '.join(word for word in text.lower().split() if word not in ENGLISH_STOPWORDS)

def shorten_project_name(current_text):
    """Shorten/clean a raw project name according to the AHK-derived logic,
    without appending tempo. This is the "processed version of the Ableton
    set's name" used wherever a display-friendly project name is needed
    (e.g. as an optional filename prefix) independently of whatever tempo
    formatting a caller wants alongside it.
    """
    logger.debug(f"Current text in Edit1: {current_text}")
    
    # Remove ".als" or ".wav" from the end
    if current_text.endswith(".als"):
        text_no_als = current_text[:-4]
    elif current_text.endswith(".wav"):
        text_no_als = current_text[:-4]
    else:
        text_no_als = current_text
    logger.debug(f"Current text without extension: {text_no_als}")

    text_no_als = remove_stopwords(text_no_als)
    logger.debug(f"Current text without stopwords: {text_no_als}")

    # Remove any existing "(AL<version>)" tag. Version-agnostic and
    # case-insensitive because remove_stopwords() has already lowercased the
    # text, and a project may carry a tag from a different build (e.g.
    # "(AL12.3)" when now running "12.4 BETA").
    text_no_al12 = strip_al_version_tag(text_no_als)
    
    # If over 20 characters (excluding whitespace), take first 10 letters of each word
    if len(re.sub(r"\s", "", text_no_al12)) > 20:
        words = text_no_al12.split()
        shortened_text1 = " ".join([word[:10] for word in words])
    else:
        shortened_text1 = text_no_al12
    logger.debug(f"Shortened text 1: {shortened_text1}")
    
    # Remove special characters (but keep decimals in numbers)
    shortened_text1 = re.sub(r"[^a-zA-Z0-9\s.]", "", shortened_text1)
    
    # Split into words
    words = shortened_text1.split()
    
    # Find all numbers (including decimals like 1.4, 3.1)
    numbers = [word for word in words if re.match(r'^\d+(\.\d+)?$', word)]
    
    # Find the last occurring number that is 50 or less
    last_number = ""
    for num in numbers:
        if float(num) <= 50:
            last_number = num
    
    # Process text based on word count
    if len(words) > 2:
        if last_number and float(last_number) <= 50:
            shortened_text2 = f"{words[0]} {words[1]} {last_number}"
        else:
            shortened_text2 = f"{words[0]} {words[1]}"
    else:
        # If 2 or fewer words, keep full words
        shortened_text2 = " ".join(words)
    
    shortened_text2 = shortened_text2.strip()
    
    # Capitalize first letter of every word
    shortened_text2 = " ".join([word.capitalize() for word in shortened_text2.split()])
    logger.debug(f"Shortened text 2: {shortened_text2}")
    
    # If over 25 characters (excluding whitespace and dots), process further
    if len(re.sub(r"[\s.]", "", shortened_text2)) > 25:
        words = shortened_text2.split()
        shortened_text3 = ""
        for i, word in enumerate(words):
            if i == 0:
                shortened_text3 += word[:8]
            elif i == 1:
                shortened_text3 += word[:7]
            else:
                shortened_text3 += word[:5]
        
        # Check if last word is a decimal number between 0 and 99
        if words and re.match(r'^\d+(\.\d+)?$', words[-1]) and float(words[-1]) <= 99:
            shortened_text3 += words[-1]
        shortened_text3 = shortened_text2.strip()
    else:
        shortened_text3 = shortened_text2
    logger.debug(f"Shortened text 3: {shortened_text3}")
    
    # Remove all whitespace (but keep decimals)
    short_text_no_white = re.sub(r"\s", "", shortened_text3)
    logger.debug(f"Current text without whitespace: {short_text_no_white}")
    
    # Ensure text is 20 characters or less (excluding dots)
    text_length = len(re.sub(r"\.", "", short_text_no_white))
    logger.debug(f"Current text length (excluding dots): {text_length}")
    
    if text_length > 20:
        # Remove characters from middle more gently
        chars_to_remove = text_length - 20
        start_pos = text_length // 2 - chars_to_remove // 2
        nine_character_text = short_text_no_white[:start_pos] + short_text_no_white[start_pos + chars_to_remove:]
    else:
        nine_character_text = short_text_no_white

    return nine_character_text


def process_project_name(current_text, tempo, ableton_version):
    """Process and shorten project name according to AHK logic, with tempo
    appended. Used as the working base name typed into Ableton's Save Audio
    File As dialog before an "All Individual Tracks" render - Ableton then
    appends " <track name>" to this for every exported stem file.
    """
    nine_character_text = shorten_project_name(current_text)
    # Add tempo to the text
    final_text = f"{nine_character_text}_{tempo}"
    return final_text

def build_main_mix_filename(base_name, tempo, project_date, include_bpm=True, include_date=True):
    """
    Shared builder for the main mix export filename (without extension):
    "<name>_<tempo>bpm_<dd-mm-yy>" - both segments are optional, and a BPM
    that could not be queried is simply omitted rather than rendered as
    "nonebpm". Used both for the name typed into Ableton's Save dialog
    (process_project_name_main) and for the safety-net rename in
    export_automation.move_and_rename_main_mix, so the two can never drift
    out of sync with each other.
    """
    # base_name is often derived straight from the raw .als filename, which
    # may still carry a "(AL<version>)" tag (added by
    # append_version_to_filename) - strip it so exported files/folders don't
    # end up named "... (AL12.3)_115.0bpm_...".
    base_name = strip_al_version_tag(base_name).strip()
    parts = [base_name]
    if include_bpm and tempo:
        parts.append(f"{tempo}bpm")
    if include_date and project_date:
        parts.append(project_date)
    return "_".join(parts)


def build_stems_folder_name(base_name, tempo, project_date, include_bpm=True, include_date=True):
    """
    Same "<name>_<tempo>bpm_<dd-mm-yy>" convention as the main mix filename,
    used for a project's live-set folder inside its stems project folder.
    Tempo is normally unavailable until Ableton has the project open, so
    callers typically build this name twice: once without a tempo when the
    folder is first created, and again once the tempo is known, renaming the
    folder in place if the name changed.
    """
    return build_main_mix_filename(base_name, tempo, project_date, include_bpm, include_date)


def process_project_name_main(current_text, tempo, ableton_version, project_date,
                               include_bpm=True, include_date=True):
    """
    Build the main mix export filename (without extension).

    Format: "<name>_<tempo>bpm_<dd-mm-yy>" - both the BPM and date segments
    are individually optional (include_bpm / include_date), and a BPM that
    could not be queried is simply omitted rather than rendered as
    "nonebpm".
    """
    logger.debug(f"Current text in Edit1: {current_text}")
    
    # Remove ".als" or ".wav" from the end
    if current_text.endswith(".als"):
        text_no_als = current_text[:-4]
    elif current_text.endswith(".wav"):
        text_no_als = current_text[:-4]
    else:
        text_no_als = current_text
    logger.debug(f"Current text without extension: {text_no_als}")

    return build_main_mix_filename(text_no_als, tempo, project_date, include_bpm, include_date)
