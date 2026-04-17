#!/usr/bin/env python3
"""Upload files to a cPanel server via its API.

Reads credentials from ~/.config/opentrusttoken/credentials.
Usage: python3 cpanel-upload.py <profile> <local_file> <remote_path>
       python3 cpanel-upload.py scosi /tmp/scosi-fixes/robots.txt /public_html/robots.txt
       python3 cpanel-upload.py scosi --batch /tmp/scosi-fixes/batch.json
"""

import sys
import os
import json
import base64
import urllib.request
import urllib.error
import ssl


def load_credentials(profile: str) -> dict:
    cred_file = os.path.expanduser("~/.config/opentrusttoken/credentials")
    creds = {}
    with open(cred_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                creds[key.strip()] = val.strip()

    if profile == "scosi":
        return {
            "host": creds.get("CPANEL_HOST", ""),
            "user": creds.get("CPANEL_USER", ""),
            "password": creds.get("CPANEL_PASS", ""),
        }
    elif profile == "dathorn":
        return {
            "host": creds.get("DATHORN_HOST", ""),
            "user": creds.get("DATHORN_USER", ""),
            "password": creds.get("DATHORN_PASS", ""),
        }
    else:
        raise ValueError(f"Unknown profile: {profile}")


def cpanel_api(creds: dict, function: str, params: dict = None) -> dict:
    """Call a cPanel UAPI function."""
    host = creds["host"].rstrip("/")
    if ":" not in host.split("/")[-1]:
        host = host + ":2083"
    if not host.startswith("http"):
        host = "https://" + host

    url = f"{host}/execute/{function}"
    if params:
        query = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        url += "?" + query

    auth = base64.b64encode(f"{creds['user']}:{creds['password']}".encode()).decode()

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "status": e.code}


def upload_file(creds: dict, local_path: str, remote_dir: str, filename: str) -> bool:
    """Upload a file via cPanel's Fileman API."""
    host = creds["host"].rstrip("/")
    if ":" not in host.split("/")[-1]:
        host = host + ":2083"
    if not host.startswith("http"):
        host = "https://" + host

    with open(local_path, "rb") as f:
        file_data = f.read()

    boundary = "----OTTUploadBoundary"
    body = b""

    # dir field
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="dir"\r\n\r\n'.encode()
    body += f"{remote_dir}\r\n".encode()

    # file field
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file-1"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_data
    body += b"\r\n"

    body += f"--{boundary}--\r\n".encode()

    url = f"{host}/execute/Fileman/upload_files"
    auth = base64.b64encode(f"{creds['user']}:{creds['password']}".encode()).decode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("status") == 1 or result.get("errors") is None:
                print(f"  OK: {filename} -> {remote_dir}/")
                return True
            else:
                print(f"  FAIL: {filename} -> {result}")
                return False
    except Exception as e:
        print(f"  ERROR: {filename} -> {e}")
        return False


def ensure_dir(creds: dict, remote_path: str):
    """Create a directory if it doesn't exist."""
    result = cpanel_api(creds, "Fileman/mkdir", {
        "path": remote_path,
        "permissions": "0755",
    })
    # Ignore errors (dir may already exist)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 cpanel-upload.py <profile> --batch <batch.json>")
        print("       python3 cpanel-upload.py <profile> <local> <remote_dir> <filename>")
        sys.exit(1)

    profile = sys.argv[1]
    creds = load_credentials(profile)

    if len(sys.argv) >= 4 and sys.argv[2] == "--batch":
        with open(sys.argv[3]) as f:
            batch = json.load(f)
        for item in batch:
            if item.get("mkdir"):
                ensure_dir(creds, item["mkdir"])
            elif item.get("file"):
                upload_file(creds, item["file"], item["dir"], item["name"])
    elif len(sys.argv) == 5:
        upload_file(creds, sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print("Invalid arguments")
        sys.exit(1)


if __name__ == "__main__":
    main()
