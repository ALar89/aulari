"""Phase 1, step 1: query tool_mobile_get_public_config on a Moodle site.

This function needs no session and no token. It tells us which login
methods the site allows (typeoflogin, qrcodetype, launchurl), the Moodle
version and the canonical site URL.

Usage (PowerShell, from the project folder):
    py -3.14 poc\\public_config.py https://<moodle-site-url>

The full response is saved to %LOCALAPPDATA%\\campus-sync\\recordings\\
(outside the repository). Only a summary is printed.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

USER_AGENT = "CampusSync/0.1.0-poc (+https://github.com/TODO/campus-sync)"
EXPECTED_ARGS = 2  # script name + site URL
TIMEOUT_SECONDS = 15
FUNCTION_NAME = "tool_mobile_get_public_config"

TYPE_OF_LOGIN = {
    1: "app (username/password inside the app)",
    2: "browser (launch.php, system browser)",
    3: "embedded browser",
}
QR_CODE_TYPE = {
    0: "QR disabled",
    1: "QR with site URL only",
    2: "QR with automatic login",
}

# Fields worth showing in the summary. Everything else stays in the recording.
SUMMARY_FIELDS = [
    "wwwroot",
    "httpswwwroot",
    "sitename",
    "release",
    "version",
    "typeoflogin",
    "launchurl",
    "qrcodetype",
    "tool_mobile_disabledfeatures",
    "warnings",
]


def build_request(site_url: str) -> urllib.request.Request:
    """Return a POST request for the no-login AJAX endpoint of the site."""
    endpoint = site_url.rstrip("/") + "/lib/ajax/service-nologin.php"
    payload = [{"index": 0, "methodname": FUNCTION_NAME, "args": {}}]
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    result = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    return result


def fetch_public_config(site_url: str) -> tuple[list, str]:
    """Call the endpoint and return (parsed JSON, final URL after redirects).

    Raises: urllib.error.URLError (includes HTTPError) on network or HTTP
    errors; json.JSONDecodeError if the body is not JSON.
    """
    request = build_request(site_url)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        final_url = response.geturl()
        raw_body = response.read().decode("utf-8")
    result = (json.loads(raw_body), final_url)
    return result


def recordings_dir() -> Path:
    """Return the recordings folder under %LOCALAPPDATA%, creating it if needed.

    Raises: KeyError if LOCALAPPDATA is not defined; OSError if it cannot be created.
    """
    result = Path(os.environ["LOCALAPPDATA"]) / "campus-sync" / "recordings"
    result.mkdir(parents=True, exist_ok=True)
    return result


def save_recording(site_url: str, response_json: list) -> Path:
    """Write the full response to a timestamped JSON file and return its path.

    Raises: OSError if the file cannot be written.
    """
    host = urlparse(site_url).hostname or "unknown-host"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = recordings_dir() / f"{host}_{FUNCTION_NAME}_{stamp}.json"
    with result.open("w", encoding="utf-8") as out_file:
        json.dump(response_json, out_file, indent=2, ensure_ascii=False)
    return result


def describe_value(field: str, value: object) -> str:
    """Return a human-readable text for a summary field."""
    result = json.dumps(value, ensure_ascii=False)
    if field == "typeoflogin":
        result = f"{value} = {TYPE_OF_LOGIN.get(value, 'unknown')}"
    elif field == "qrcodetype":
        result = f"{value} = {QR_CODE_TYPE.get(value, 'unknown')}"
    return result


def print_summary(site_url: str, final_url: str, data: dict) -> None:
    """Print the fields that matter for decision D1."""
    print(f"Requested URL : {site_url}")
    print(f"Final URL     : {final_url}")
    print(f"HTTPS         : {urlparse(final_url).scheme == 'https'}")
    for field in SUMMARY_FIELDS:
        value = data.get(field, "<missing>")
        print(f"{field:<29}: {describe_value(field, value)}")
    providers = data.get("identityproviders", [])
    provider_names = [provider.get("name", "?") for provider in providers]
    print(f"{'identityproviders (names)':<29}: {provider_names}")


def main(argv: list[str]) -> int:
    """Run the query and return the process exit code (0 = success)."""
    exit_code = 1
    if len(argv) != EXPECTED_ARGS:
        print("Usage: py -3.14 poc\\public_config.py https://<moodle-site-url>")
    else:
        site_url = argv[1]
        try:
            response_json, final_url = fetch_public_config(site_url)
            recording_path = save_recording(site_url, response_json)
            first = response_json[0]
            if first.get("error"):
                print("Moodle returned an error:")
                print(json.dumps(first.get("exception", first), indent=2, ensure_ascii=False))
            else:
                print_summary(site_url, final_url, first.get("data", {}))
                exit_code = 0
            print(f"\nFull response saved to: {recording_path}")
        except urllib.error.HTTPError as error:
            print(f"HTTP error {error.code}: {error.reason}")
        except urllib.error.URLError as error:
            print(f"Network error: {error.reason}")
        except json.JSONDecodeError as error:
            print(f"The response is not JSON: {error}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
