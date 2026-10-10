"""Download Massive Flat Files over their S3-compatible endpoint.

Credentials come from the environment (never from the repo):
  MASSIVE_S3_ACCESS_KEY, MASSIVE_S3_SECRET_KEY
The futures prefix is NOT documented by Massive: discover it with `list_prefix`.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def key_date(key: str) -> dt.date | None:
    m = _DATE.search(key.rsplit("/", 1)[-1])
    return dt.date.fromisoformat(m.group(1)) if m else None


def select_keys(keys, start: dt.date, end: dt.date) -> list[str]:
    """Keep keys whose filename date lies in [start, end]; layout-agnostic."""
    return sorted(k for k in keys if (d := key_date(k)) and start <= d <= end)


def _client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ["MASSIVE_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MASSIVE_S3_SECRET_KEY"],
        config=Config(signature_version="s3v4"))


def list_prefix(prefix: str = "", delimiter: str = "/") -> list[str]:
    """One directory level: sub-prefixes and keys under `prefix`."""
    out = []
    for page in _client().get_paginator("list_objects_v2").paginate(
            Bucket=BUCKET, Prefix=prefix, Delimiter=delimiter):
        out += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
        out += [o["Key"] for o in page.get("Contents", [])]
    return out


def download_range(prefix: str, start: dt.date, end: dt.date, out_dir: str) -> list[Path]:
    """Download every file under `prefix` dated in [start, end]; skips existing files."""
    s3 = _client()
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", [])]
    done = []
    for key in select_keys(keys, start, end):
        dest = Path(out_dir) / key
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, key, str(dest))
        done.append(dest)
    return done
