<p align="center"><img src="packaging/aulari-logo-1024.png" alt="Aulari logo: a folder with a download arrow" width="128"></p>

# Aulari

**Download and organize the material of your campus. Works with Moodle™.**

[Leer en español](README.es.md)

Aulari is a desktop app for students. It downloads the files of your courses from a Moodle site, puts them in tidy folders, keeps them up to date and exports your deadlines to a calendar file. It was built for the UAB Virtual Campus ("e-aules"), but it works with any Moodle site that has the mobile app service enabled.

> Aulari is an independent project. It is not affiliated with, endorsed by or supported by Moodle Pty Ltd or by any university. Moodle™ is a registered trademark of Moodle Pty Ltd.

## Features

- **Login with your university's single sign-on** (for example CAS): you log in in your own browser and, on Windows, the app receives the session by itself (on macOS you copy one address by hand for now, see below). No password is ever typed into the app.
- **Tidy folders**: `<destination>/<academic year>/<course>/<NN - section>/`, with short course aliases (`Camps i Ones Electromagnètiques` → `CiOE`) that you can rename.
- **Nothing is ever overwritten**: when a teacher uploads a new version, it is saved next to the old one as `Name (YYYY-MM-DD).ext`. Files removed from Moodle stay on your disk.
- **Files from resources, folders, pages and assignment statements**, plus external links as `.url` shortcuts.
- **Calendar**: your deadlines in an `.ics` file you can import into any calendar app (tested with Proton Calendar, re-importing does not duplicate events).
- **Upcoming deadlines**, **new announcements**, **your submissions** (status, grade, teacher comments) and **your grades** inside the app.
- **`Novedades.txt`**: a log of what each sync added or updated.
- **Recover deleted files**, open course folders, several academic years.

## Requirements

- Windows 10 or 11, or macOS on a Mac with Apple Silicon (M1 or later).
- A Moodle site with the mobile app service enabled (most universities have it, because the official Moodle app needs it).
- To run from source: Python 3.14 (on macOS, the python.org installer, which includes Tk).

## Installation

### Windows

Download `Aulari-<version>-windows.zip` from the [releases](https://github.com/ALar89/aulari/releases/latest), unzip it anywhere and open `Aulari.exe` inside the folder.

The executable is not code-signed yet, so the first time Windows SmartScreen shows *Windows protected your PC*. Press **More info → Run anyway**; it does not ask again.

Optionally, check the download first: the release includes its SHA-256 (`Get-FileHash Aulari-<version>-windows.zip`) and a build provenance attestation (`gh attestation verify Aulari-<version>-windows.zip --repo ALar89/aulari`).

To update, download the new zip and unzip it over the old folder. Your settings and session are kept, because they are stored outside it.

### macOS

Download `Aulari-<version>-macos-arm64.dmg` from the [releases](https://github.com/ALar89/aulari/releases), open it and drag **Aulari** to **Applications**.

The app is **not signed or notarized by Apple** (that needs a paid Apple developer account), so the first time macOS blocks it with a message saying that Apple could not verify it. To open it:

1. Open Aulari once and close the message.
2. Go to **System Settings → Privacy & Security**, scroll down to *Security* and press **Open Anyway** next to the message about Aulari.
3. Confirm with your password or Touch ID. macOS will not ask again for this version.

Optionally, check the download first: the release includes its SHA-256 (`shasum -a 256 -c Aulari-<version>-macos-arm64.dmg.sha256`) and a build provenance attestation (`gh attestation verify Aulari-<version>-macos-arm64.dmg --repo ALar89/aulari`).

### From source

Windows, in PowerShell:

```powershell
git clone https://github.com/ALar89/aulari.git
cd aulari
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.txt
pip install --no-deps -e .
python -m aulari
```

macOS, in Terminal:

```bash
git clone https://github.com/ALar89/aulari.git
cd aulari
python3.14 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install --no-deps -e .
python -m aulari
```

## How to use it

1. **Log in**: press *Abrir navegador*, log in to your campus as usual and accept when the browser asks to open the app. The window continues by itself.
   On macOS, automatic login is not available yet. In Google Chrome or another Chromium-based browser, open a new tab with the developer tools recording (*Network* → *Preserve log*), press **Copy login address** in Aulari and paste it into that tab. After logging in, copy the `Location` header of the `launch.php` request (status 302, it starts with `moodlemobile://`) and paste it into the app. The login screen shows these steps. This also works when your campus session is still open in the browser, which redirects at once and leaves the tab blank.
2. **Choose your courses** with the checkboxes and a **destination folder**.
3. Press **Sincronizar**. The summary shows what is new; the calendar is exported to `<destination>/<academic year>/calendari.ics`.

The interface is available in Catalan, Spanish and English (selector at the bottom of the window; by default it follows the Windows language).

## Where your data is stored

| What | Where |
|---|---|
| Session token | Windows Credential Manager or macOS Keychain (through `keyring`), never in a file |
| Settings, registry of downloads, logs | Windows: `%LOCALAPPDATA%\aulari\`. macOS: `~/Library/Application Support/aulari/` |
| Downloaded material | The destination folder you choose |

On Windows, during login the app registers itself, **only for your Windows user**, as the handler of `moodlemobile://` links, and removes that registration as soon as the login ends.

## Security and privacy

- Read-only access: the app only calls a fixed list of read functions of the Moodle web service. It never submits, posts or changes anything in your campus.
- The token travels only to your Moodle site, in the body of HTTPS requests, and it is removed from every log message.
- No telemetry: nothing is sent anywhere except to your Moodle site.
- The app never opens or runs the files it downloads.
- Honest identification: requests carry the `Aulari/<version>` User-Agent; the app does not pretend to be the official Moodle app.
- Limits you should know: the system credential store (Windows Credential Manager, macOS Keychain) protects the token from plain-text copies and backups, but not from malware running as your own user. On some sites (e-aules included) students cannot revoke the token from their profile; it expires by itself after a while.

## Personal use only

The material of your courses belongs to its authors and is for enrolled students. Aulari is meant to make **your own** copy easier to keep in order. Do not share or publish downloaded material.

## Limitations

- Windows and macOS (Apple Silicon) only; Linux is planned.
- On macOS, automatic login and automatic sync are not available yet, the app is not notarized, downloaded files are not marked as coming from the internet, and external links are saved as Windows `.url` shortcuts. After an update, macOS may ask again whether Aulari can use the Keychain.
- The HTML text of pages and videos from external platforms are not downloaded.
- One computer per destination folder (another computer can take over the folder).

## Development

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
ruff format
ruff check
```

On macOS the same commands work in Terminal (`source .venv/bin/activate` instead of `Activate.ps1`). To build the app and the disk image there:

```bash
pyinstaller --clean --noconfirm aulari.spec
packaging/make_dmg.sh Aulari-test.dmg
```

Design decisions, verified findings and risks are documented in [`docs/design.md`](docs/design.md) (in Spanish).

## License

MIT. See [`LICENSE`](LICENSE).
