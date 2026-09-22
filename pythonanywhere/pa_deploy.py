"""Create/manage the PythonAnywhere ASGI site for DriveDoc Control.

Run this INSIDE a PythonAnywhere Bash console (in the project venv), e.g.:

    python ~/document-control-platform/pythonanywhere/pa_deploy.py \
        --user YOURUSERNAME \
        --token YOUR_API_TOKEN \
        --mode create

Requires: requests (pip install requests)
"""  # noqa: D404
from __future__ import annotations

import argparse
import sys
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("pip install requests  (run: pip install requests)")

REPO_DIR = "document-control-platform"
VENV = "drivedoc"
BACKEND_REL = f"{REPO_DIR}/backend"


def build_command(username: str) -> str:
    return (
        f"/home/{username}/.virtualenvs/{VENV}/bin/uvicorn "
        "app.main:app "
        f"--app-dir /home/{username}/{BACKEND_REL} "
        "--uds ${DOMAIN_SOCKET} "
        "--proxy-headers --forwarded-allow-ips=* "
        "--log-level info"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage the DriveDoc ASGI site on PythonAnywhere")
    ap.add_argument("--user", required=True, help="PythonAnywhere username")
    ap.add_argument("--token", required=True, help="PythonAnywhere API token")
    ap.add_argument("--host", default="www.pythonanywhere.com",
                    help="www.pythonanywhere.com or eu.pythonanywhere.com")
    ap.add_argument("--domain", default="pythonanywhere.com",
                    help="pythonanywhere.com or eu.pythonanywhere.com")
    ap.add_argument("--mode", choices=["create", "reload", "info", "delete"], default="create")
    args = ap.parse_args()

    username = args.user
    domain_name = f"{username}.{args.domain}"
    headers = {"Authorization": f"Token {args.token}"}
    api_base = f"https://{args.host}/api/v1/user/{username}/"

    if args.mode == "create":
        command = build_command(username)
        print("Creating site with command:")
        print(f"  {command}\n")
        resp = requests.post(
            urljoin(api_base, "websites/"),
            headers=headers,
            json={"domain_name": domain_name, "enabled": True,
                  "webapp": {"command": command}},
            timeout=60,
        )
        print(resp.status_code)
        try:
            print(resp.json())
        except ValueError:
            print(resp.text)
        print(f"\nSite URL: https://{domain_name}")
    elif args.mode == "reload":
        resp = requests.post(
            urljoin(api_base, f"websites/{domain_name}/reload"),
            headers=headers, timeout=60,
        )
        print(f"reload -> {resp.status_code}")
    elif args.mode == "delete":
        resp = requests.delete(
            urljoin(api_base, f"websites/{domain_name}/"),
            headers=headers, timeout=60,
        )
        print(f"delete -> {resp.status_code}")
    else:  # info
        resp = requests.get(
            urljoin(api_base, f"websites/{domain_name}/"),
            headers=headers, timeout=60,
        )
        print(resp.status_code)
        try:
            print(resp.json())
        except ValueError:
            print(resp.text)


if __name__ == "__main__":
    main()