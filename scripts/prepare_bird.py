"""Fetch BIRD-SQL dev set and unpack into data/bird/.

Run once to enable real-benchmark mode:
    python scripts/prepare_bird.py

This is optional — the toy benchmark (`scripts/prepare_sql_toy.py`) does
not depend on it. We keep BIRD out of the repo because the dev databases
total ~1.5 GB and the BIRD authors prefer users download fresh from the
official source so they can track usage.

What gets downloaded:
  - dev.zip (~330 MB) — bundles the 1534-question dev set, gold SQL,
    AND the dev_databases.zip (11 SQLite databases, ~1.5 GB unpacked)

After unpack:
  data/bird/
    dev.json
    dev_databases/<db_id>/<db_id>.sqlite
    dev_databases/<db_id>/database_description/*.csv

The previous version of this script downloaded dev.zip and
dev_databases.zip from two separate direct links. BIRD has since
changed their publication shape: dev_databases.zip is now nested
*inside* dev.zip, and the standalone dev_databases.zip URL returns
HTTP 403 (bucket ACL access denied). We adapted: download dev.zip
only, then unpack twice.

Caveats:
  - the BIRD authors update the data periodically; we print the
    SHA256 of dev.zip so the eval chapter can pin the corpus version
  - the inner zip contains macOS cruft (`__MACOSX/...`, `.DS_Store`)
    which we filter out
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "bird"

# Public download URL — listed on https://bird-bench.github.io/. If the
# BIRD team moves it, update here and re-pin the SHA256 in the eval chapter.
DEV_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"


def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name} already present ({dest.stat().st_size:,} bytes)")
        return
    print(f"  [fetch] {dest.name} from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f)
    print(f"      -> saved to {dest} ({dest.stat().st_size:,} bytes)")


def _sha256(path: Path) -> str:
    """Stream-hash a file with a 1 MiB chunk so big downloads don't OOM."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_populated(target: Path) -> bool:
    return target.exists() and any(target.iterdir())


def _extract_from_outer(zip_path: Path, target: Path) -> Path:
    """Pull dev.json + dev_databases.zip out of dev.zip into target/.

    BIRD wraps everything in a dated dir (``dev_20240627/`` at last
    check). We don't unpack the whole tree — we only need ``dev.json``
    and the nested ``dev_databases.zip``. Helper files (``dev.sql``,
    ``dev_tables.json``, ``dev_tied_append.json``) are extracted alongside
    because future eval work may use them.

    Returns the path of the extracted inner ``dev_databases.zip``.
    """
    target.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # The zip doesn't store explicit directory entries — infer the
        # wrapper prefix from file paths.
        prefixes = {
            n.split("/", 1)[0]
            for n in names
            if "/" in n and n.split("/", 1)[0].startswith("dev_")
        }
        if len(prefixes) != 1:
            raise RuntimeError(
                "dev.zip layout unexpected — expected exactly one "
                f"dev_YYYYMMDD/ wrapper, got: {sorted(prefixes)}. "
                f"First entries: {names[:5]}"
            )
        wrapper = prefixes.pop() + "/"
        wanted = (
            "dev.json",
            "dev_databases.zip",
            "dev.sql",
            "dev_tables.json",
            "dev_tied_append.json",
        )
        for fname in wanted:
            src = wrapper + fname
            dst = target / fname
            if dst.exists():
                continue
            if src not in z.namelist():
                # Helper files may disappear in future BIRD releases.
                # dev.json and dev_databases.zip are required; the rest are optional.
                if fname in ("dev.json", "dev_databases.zip"):
                    raise RuntimeError(f"required entry missing inside dev.zip: {src}")
                continue
            print(f"  [extract] {src} -> {dst}")
            dst.write_bytes(z.read(src))

    inner = target / "dev_databases.zip"
    if not inner.exists():
        raise RuntimeError("dev_databases.zip not extracted from dev.zip")
    return inner


def _extract_databases(zip_path: Path, target: Path) -> None:
    """Unpack the inner ``dev_databases.zip`` into ``target/dev_databases/``.

    The zip already has ``dev_databases/`` as its top-level directory,
    so we extract straight into ``target/`` and the loader's expected
    ``target/dev_databases/<db_id>/<db_id>.sqlite`` layout falls out.

    Filters out macOS cruft (``__MACOSX/...``, ``.DS_Store``) the BIRD
    authors left in the zip.
    """
    dbs_root = target / "dev_databases"
    if _is_populated(dbs_root):
        print(f"  [skip] {dbs_root.name}/ already populated")
        return
    print(f"  [unzip] {zip_path.name} -> {target}/dev_databases/")
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.filename.startswith("__MACOSX/"):
                continue
            if info.filename.endswith(".DS_Store"):
                continue
            z.extract(info, target)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dev_zip = OUT_DIR / "dev.zip"

    print("Downloading BIRD dev set...")
    _download(DEV_URL, dev_zip)
    dev_sha = _sha256(dev_zip)
    print(f"  dev.zip SHA256 = {dev_sha}")

    print("\nUnpacking...")
    already_done = (OUT_DIR / "dev.json").exists() and _is_populated(
        OUT_DIR / "dev_databases"
    )
    if already_done:
        print("  [skip] data/bird/ already fully populated")
    else:
        inner = _extract_from_outer(dev_zip, OUT_DIR)
        _extract_databases(inner, OUT_DIR)

    print("\nDone. Browse data/bird/ to confirm.")
    print("Load BIRD cases with reforge.runtime.sql.bird_loader.")
    print(f"\nFor eval-chapter reproducibility, pin: dev.zip SHA256 = {dev_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
