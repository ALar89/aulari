# Aulari

**Download and organize the material of your campus. Works with Moodle™.**

[Leer en español](README.es.md)

Aulari is a desktop app for students. It downloads the files of your courses from a Moodle site, puts them in tidy folders, keeps them up to date and exports your deadlines to a calendar file. It was built for the UAB Virtual Campus ("e-aules"), but it works with any Moodle site that has the mobile app service enabled.

> Aulari is an independent project. It is not affiliated with, endorsed by or supported by Moodle Pty Ltd or by any university. Moodle™ is a registered trademark of Moodle Pty Ltd.

## Features

- **Automatic login with your university's single sign-on** (for example CAS): you log in in your own browser and the app receives the session by itself. No password is ever typed into the app.
- **Tidy folders**: `<destination>/<academic year>/<course>/<NN - section>/`, with short course aliases (`Camps i Ones Electromagnètiques` → `CiOE`) that you can rename.
- **Nothing is ever overwritten**: when a teacher uploads a new version, it is saved next to the old one as `Name (YYYY-MM-DD).ext`. Files removed from Moodle stay on your disk.
- **Files from resources, folders, pages and assignment statements**, plus external links as `.url` shortcuts.
- **Calendar**: your deadlines in an `.ics` file you can import into any calendar app (tested with Proton Calendar, re-importing does not duplicate events).
- **Upcoming deadlines**, **new announcements**, **your submissions** (status, grade, teacher comments) and **your grades** inside the app.
- **`Novedades.txt`**: a log of what each sync added or updated.
- **Recover deleted files**, open course folders, several academic years.

## Requirements

- Windows 10 or 11.
- A Moodle site with the mobile app service enabled (most universities have it, because the official Moodle app needs it).
- To run from source: Python 3.14.

## Installation (from source)

```powershell
git clone https://github.com/ALar89/aulari.git
cd aulari
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
python -m aulari
```

A packaged Windows executable will be published in the releases.

## How to use it

1. **Log in**: press *Abrir navegador*, log in to your campus as usual and accept when the browser asks to open the app. The window continues by itself.
2. **Choose your courses** with the checkboxes and a **destination folder**.
3. Press **Sincronizar**. The summary shows what is new; the calendar is exported to `<destination>/<academic year>/calendari.ics`.

The interface is in Spanish for now; Catalan and English are planned.

## Where your data is stored

| What | Where |
|---|---|
| Session token | Windows Credential Manager (through `keyring`), never in a file |
| Settings, registry of downloads, logs | `%LOCALAPPDATA%\aulari\` |
| Downloaded material | The destination folder you choose |

During login the app registers itself, **only for your Windows user**, as the handler of `moodlemobile://` links, and removes that registration as soon as the login ends.

## Security and privacy

- Read-only access: the app only calls a fixed list of read functions of the Moodle web service. It never submits, posts or changes anything in your campus.
- The token travels only to your Moodle site, in the body of HTTPS requests, and it is removed from every log message.
- No telemetry: nothing is sent anywhere except to your Moodle site.
- The app never opens or runs the files it downloads.
- Honest identification: requests carry the `Aulari/<version>` User-Agent; the app does not pretend to be the official Moodle app.
- Limits you should know: the Windows credential store protects the token from plain-text copies and backups, but not from malware running as your own user. On some sites (e-aules included) students cannot revoke the token from their profile; it expires by itself after a while.

## Personal use only

The material of your courses belongs to its authors and is for enrolled students. Aulari is meant to make **your own** copy easier to keep in order. Do not share or publish downloaded material.

## Limitations

- Windows only for now (macOS and Linux are planned).
- The HTML text of pages and videos from external platforms are not downloaded.
- One computer per destination folder (another computer can take over the folder).

## Development

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
ruff format
ruff check
```

Design decisions, verified findings and risks are documented in [`docs/design.md`](docs/design.md) (in Spanish).

## License

MIT. See [`LICENSE`](LICENSE).
