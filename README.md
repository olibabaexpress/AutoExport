# AutoExport


Vibe-coded rookie script that solves a real problem for Ableton users.
AutoExport can batch-export audio from your Ableton Live projects by automating the process (open Ableton Live Set, navigate dialogue through to Export Audio/Video, running export, repeat). 

The GUI scans your project folders, lets you queue the sets you want, and then begins an automation procedure: opening the set, applying your export settings, saving the audio with a consistent name, and moving on to the next. It also includes tools for finding and fixing missing samples across a whole library of projects.

  
> **Platform:** Windows 10/11 x64 only. Targets **Ableton Live 12 with the English UI**.

  
---

  
## Features


### Export automation

- **Project browser** - scan folders with .asl files, search, and queue sets for export.

- **Batch queue** - queue up projects for automation, provides estimated output size of the resulting rendered audio.

- **Smart Select** - automatically picks the likely "final" version of each project from file names so you don't export every draft.

- **Export settings** - sample rate, bit depth, stems (all individual tracks) / main track export, configurable file and folder naming (limited).

- **Automation** - handles Live's dialogs and popups, detects a hung Live (not responding), kills it and skips that task rather than stalling the queue. Some popups that don't appear on my system may not be handled. 

- **Rendered-file Organisation** - checks that output actually appeared and organizes stems into group/cluster folders. Stem folders follow Ableton Set's group hierarchy - with group tracks labelled "(group)". Configurable naming for rendered stems. 

  
### Missing-media tools

- **Missing media scan** – finds missing samples in `.als` sets, powered by [abletoolz](https://pypi.org/project/abletoolz/).

- **Scan & Relink / Scan & Collect** - resolve missing samples using a local sample index (generated within the GUI), then either relink to the found file or copy it into the project (making it portable).

- **Bulk Fix** - run a scan over whole folders or selected projects, with pause/resume, progress, and a saved report.

- **Sample index** – index your sample drives once; matches use name plus size (with tolerance for small size differences) and handle same-name ambiguities.

- **Network sample index** - import the AutoExport sample index from another PC on your LAN (e.g. `\\MyComputer\C\Users\<you>\AppData\Local\AutoExport\data`). Samples that only exist on the other machine are copied across into the project and relinked, along with their `.asd` files. This feature makes it easy to transfer projects from one computer to another, or helps to fix broken sample references after a messy transfer. 

- **Media status** – GUI displays the "media status" of Ableton Sets (ie: whether it has missing media, has external references or is fully self contianed) Portable / Linked / Missing state is remembered between sessions. 

  

---

  

## Requirements

  

| Needed | Notes |

|---|---|

| Windows 10/11 x64 | UI automation relies on Win32 |

| Ableton Live 12 | English UI; project plugins/samples must already be installed |

| [AbletonOSC](https://github.com/ideoforms/AbletonOSC) | A patched copy is bundled in `vendor/AbletonOSC` and installed for you |

| Tesseract OCR | Used to read Live's export dialogs; installed/detected by setup |

| Python 3.11–3.13 | Only when running from source |

  

## Installation

  

### Option A – Installer (recommended)

Build or download the installer (see [Building](#building-the-installer)). It installs per-user under `%LOCALAPPDATA%\AutoExport`, bundles Python, all dependencies, Tesseract and the patched AbletonOSC, and works fully offline. A first-run window detects Live and your User Library, and lets you pick an export folder and a project folder.

  

### Option B – From source

```powershell

git clone https://github.com/olibabaexpress/AutoExport.git

cd AutoExport

powershell -ExecutionPolicy Bypass -File setup.ps1

```

`setup.ps1` is safe to re-run. It creates `.venv`, installs `requirements.txt`, finds (or offers to install) Tesseract, detects Live, installs the patched AbletonOSC into your User Library, saves default folders, and creates a desktop shortcut. Non-interactive example:

  

```powershell

.\setup.ps1 -ExportPath "D:\Bounces" -ProjectDir "D:\Ableton Projects" -NonInteractive

```

  

Launch with:

```powershell

python runautoexport.py

```

  

### Live setup

1. Open Live → **Settings → Link, Tempo & MIDI → Control Surface**.
2. Enable **AbletonOSC**.
3. Make sure your windows display resolution / Ableton zoom preferences are set to a level where the Export Audio / Video window displays in full across your screen with no parts hidden. Adjust your zoom level through Ableton preferences (Settings>Display/Input>Zoom) to an appropriate level (125% works for my machine). This is to ensure OSR can read and parse all the Export Audio/Video text. 
4. Pick a "normal" Ableton theme (not Angst Robot). Automation script uses saturation to detect on/off state of Ableton buttons so works best when the background is the default grey (I use immaterial).



---


## Usage

  

1. **Add project folders** with the add-folder button (or drag and drop).
2. **Select projects** in the browser (or use Smart Select) and add them to the queue.
3. Choose your **export settings** and output folder.
4. Press start. Leave the machine alone while Live is being driven - it uses real UI automation, so don't move the mouse or type during an export.
5. To cancel, close the console window. 

  

### Fixing missing samples

1. Open **Sample Index** and add the drives/folders that hold your samples; build the index
2. Open **Bulk Fix**, choose folders (or right-click projects > **Bulk Fix Selected**), then run **Scan & Relink** or **Scan & Collect**.
3. Review the saved missing-media report for anything still unresolved.

  
### Using a second PC's samples

Install AutoExport on the other PC and build its sample index. On this PC, use the **network** button next to the add-folder button; AutoExport will try to find the index automatically, otherwise browse to it through a network path (`\\COMPUTER\share\...`). Once connected the icon turns active and the remote index folders are listed with the computer's name.

  

---

  

## Data and configuration

  

User data (config, sample index, media status, logs) lives in the `data/` and `LOGS/` folders next to the app (or under `%LOCALAPPDATA%\AutoExport` when installed). These are not part of the repository.

  
## Building the installer

Requires Python 3.11–3.13 x64, [Inno Setup 6](https://jrsoftware.org/isinfo.php) and a Windows Tesseract install with English data.


```powershell

.\build-installer.ps1                # installer lands in dist\installer

.\build-installer.ps1 -Version 0.1.11

```

Optional parameters: `-Python`, `-TesseractDir`, `-InnoCompiler`, `-Version`. Build dependencies go into an isolated `.build-venv`. See [packaging/README.md](packaging/README.md) for details.

  

## Development

  

```powershell

python -m venv .venv

.\.venv\Scripts\pip install -r requirements.txt

.\.venv\Scripts\python -m unittest discover -s tests

```

  

Layout:

  

| Path | Contents |

|---|---|

| `app/` | Application source (GUI, automation, media tools) |

| `tests/` | Unit tests |

| `packaging/` | PyInstaller spec, Inno Setup script, installer artwork |

| `vendor/AbletonOSC` | Patched AbletonOSC remote script |

  

## Troubleshooting

  

- **Export dialogs aren't recognised?** confirm Live is using the English UI and Tesseract is detected.

- **Live can't be controlled?** make sure AbletonOSC is selected as a Control Surface and Live was restarted.

- **Network index not found?**  confirm the other PC shares its drive with full permissions (e.g. share name `c`) and that you can open `\\COMPUTER\c` in Explorer; otherwise browse to the index manually.

- Logs are written to the `LOGS/` folder.

  

## Disclaimer

  

AutoExport is an independent project and is not affiliated with or endorsed by Ableton AG. "Ableton" and "Live" are trademarks of Ableton AG. Always keep backups of your projects before running Scan & Collect/Relink, since those operations modify `.als` files.

  
