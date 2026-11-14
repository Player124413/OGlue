#!/usr/bin/env python3
"""Download a user-supplied HTTPS disc image without putting its URL in logs.

GitHub workflow_dispatch has no file-upload field. The workflow passes a direct
HTTPS URL through the environment; this helper streams it to disk, bounds the
download, checks an optional SHA-256, and never forwards credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
DEFAULT_MAX_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MIN_BYTES = 1024 * 1024
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """A message safe to print: never contains the requested URL."""


def validate_url(url: str, *, allow_private: bool = False) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("The ISO URL is not a valid HTTPS URL.") from exc
    if (parts.scheme.lower() != "https" or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.fragment):
        raise FetchError("Use a direct HTTPS URL without credentials or a fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise FetchError("The ISO URL has an invalid port.")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii")
    try:
        addresses = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FetchError("The ISO host could not be resolved.") from exc
    if not addresses:
        raise FetchError("The ISO host has no addresses.")
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if not allow_private and not ip.is_global:
            raise FetchError("The ISO URL must resolve to public addresses.")
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urllib.parse.urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str, output: Path, *, expected_sha256: str = "",
          min_bytes: int = DEFAULT_MIN_BYTES, max_bytes: int = DEFAULT_MAX_BYTES,
          timeout: float = 60.0, max_seconds: float = 1800.0,
          allow_private: bool = False) -> dict:
    if min_bytes < 1 or max_bytes < min_bytes:
        raise FetchError("Invalid download size bounds.")
    if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise FetchError("iso_sha256 must contain exactly 64 hexadecimal characters.")
    if timeout <= 0 or max_seconds <= 0:
        raise FetchError("Download timeouts must be positive.")
    output = output.resolve()
    if output.exists():
        raise FetchError("The download destination already exists; use a fresh work directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output.parent).free
    if free < min(max_bytes, 1024 * 1024 * 1024) + 64 * 1024 * 1024:
        raise FetchError("Not enough free disk space for the ISO download.")

    opener = urllib.request.build_opener(_NoRedirect())
    current = validate_url(url, allow_private=allow_private)
    deadline = time.monotonic() + max_seconds
    response = None
    for _ in range(7):
        if time.monotonic() >= deadline:
            raise FetchError("The ISO download exceeded its time limit.")
        request = urllib.request.Request(current, headers={
            "User-Agent": "OGlue-Heroes-CI/1",
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
        }, method="GET")
        try:
            response = opener.open(request, timeout=timeout)
            break
        except urllib.error.HTTPError as exc:
            code = exc.code
            location = exc.headers.get("Location") if exc.headers else None
            exc.close()
            if code in REDIRECTS and location:
                current = validate_url(urllib.parse.urljoin(current, location),
                                       allow_private=allow_private)
                continue
            raise FetchError(f"The ISO server returned HTTP {code}.") from None
        except (urllib.error.URLError, OSError) as exc:
            raise FetchError("The ISO download failed (network/TLS error).") from None
    if response is None:
        raise FetchError("The ISO server redirected too many times.")

    tmp_name = None
    try:
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding not in ("", "identity"):
            raise FetchError("The server compressed the response; provide the raw ISO file.")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                raise FetchError("The server sent an invalid Content-Length.") from None
            if declared < min_bytes or declared > max_bytes:
                raise FetchError("The ISO size is outside the allowed range.")
            if free < declared + 64 * 1024 * 1024:
                raise FetchError("Not enough free disk space for the declared ISO size.")
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(prefix=".iso-", suffix=".part",
                                         dir=output.parent, delete=False) as out:
            tmp_name = out.name
            while True:
                if time.monotonic() >= deadline:
                    raise FetchError("The ISO download exceeded its time limit.")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("The ISO exceeds the maximum download size.")
                digest.update(chunk)
                out.write(chunk)
        if size < min_bytes:
            raise FetchError("The downloaded file is too small to be a game image.")
        actual = digest.hexdigest()
        if expected_sha256 and actual != expected_sha256.lower():
            raise FetchError("ISO SHA-256 mismatch. The input is not the expected disc image.")
        os.replace(tmp_name, output)
        tmp_name = None
        return {"size": size, "sha256": actual}
    finally:
        response.close()
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="direct HTTPS URL (never logged)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sha256", default="")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-seconds", type=float, default=1800.0)
    args = parser.parse_args(argv)
    try:
        result = fetch(args.url, args.output, expected_sha256=args.sha256,
                       max_bytes=args.max_bytes, timeout=args.timeout,
                       max_seconds=args.max_seconds)
    except FetchError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 1
    except OSError:
        print("ERROR: Could not write the ISO download.", flush=True)
        return 1
    except http.client.HTTPException:
        print("ERROR: The ISO server sent an invalid HTTP response.", flush=True)
        return 1
    print(json.dumps({"downloaded": result}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
