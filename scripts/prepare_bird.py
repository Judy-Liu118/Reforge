"""Fetch BIRD-SQL dev set and unpack into data/bird/.

Run once to enable real-benchmark mode:
    python scripts/prepare_bird.py

This is optional — the toy benchmark (`scripts/prepare_sql_toy.py`) does
not depend on it. We keep BIRD out of the repo because the dev databases
total ~1.5 GB and the BIRD authors prefer users download fresh from the
official source so they can track usage.

What gets downloaded:
  - dev.zip        : the 1534-question dev set + per-question gold SQL
  - dev_databases.zip : 11 SQLite databases referenced by the dev set

After unpack:
  data/bird/
    dev.json
    dev_databases/<db_id>/<db_id>.sqlite
    dev_databases/<db_id>/database_description/*.csv

Caveats:
  - the download is ~1.5 GB and may take a while
  - the BIRD authors update the data periodically — pin a version if you
    need reproducibility (see the URLs below)
"""

from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "bird"

# These URLs are the public download links advertised on
# https://bird-bench.github.io/ — if the BIRD authors move them you may
# need to copy the latest URL from the website's "Download" section.
DEV_QUESTIONS_URL = (
    "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
)
DEV_DATABASES_URL = (
    "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev_databases.zip"
)


def _download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"  [skip] {dest.name} already present")
        return
    print(f"  [fetch] {dest.name} from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f)
    print(f"      -> saved to {dest}")


def _unpack(zip_path: Path, target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        print(f"  [skip] {target.name} already populated")
        return
    print(f"  [unzip] {zip_path.name} -> {target}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(target)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    dev_zip = OUT_DIR / "dev.zip"
    dbs_zip = OUT_DIR / "dev_databases.zip"

    print("Downloading BIRD dev set...")
    _download(DEV_QUESTIONS_URL, dev_zip)
    _download(DEV_DATABASES_URL, dbs_zip)

    print("\nUnpacking...")
    _unpack(dev_zip, OUT_DIR)
    _unpack(dbs_zip, OUT_DIR)

    print("\nDone. Browse data/bird/ to confirm.")
    print("Then load BIRD cases with reforge.runtime.sql.bird_loader.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
