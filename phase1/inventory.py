"""
Phase 1 · Corpus inventory and content-hash deduplication.

Answers three questions with evidence rather than assertion:
  1. What files are actually here, and how big is the real corpus?
  2. Which of them are byte-identical duplicates? (Finding D)
  3. What failed to download, and why? (Finding A)

Output: inventory.csv, duplicates.csv, missing.csv, inventory_summary.json
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path

import config


@dataclass
class FileRecord:
    path: str                 # relative to CORPUS_ROOT
    name: str
    ext: str
    kind: str
    size_bytes: int
    sha256: str
    zone: str                 # live | archive | project_docs | code | other
    is_error_stub: bool = False
    error_type: str = ""
    cmr_refs: list = field(default_factory=list)


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _zone_of(rel: Path) -> str:
    parts = rel.parts
    if not parts:
        return "other"
    if parts[0] == "Archive":
        return "archive"
    if parts[0] == "fis group data":
        return "live"
    if parts[0] == "FIS Project Documents":
        return "project_docs"
    if rel.suffix in {".py", ".txt", ".json"} and len(parts) == 1:
        return "code"
    return "other"


ERROR_TYPE_RE = re.compile(r"ExceptionType:\s*([A-Za-z]+)")


def _read_error_stub(path: Path) -> str:
    """`*_Error.txt` files are SharePoint download failures. Pull the exception."""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unreadable"
    m = ERROR_TYPE_RE.search(txt)
    return m.group(1) if m else "unknown"


def walk_corpus(root: Path | None = None) -> list[FileRecord]:
    root = root or config.CORPUS_ROOT
    records: list[FileRecord] = []

    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in config.SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in config.SKIP_FILE_NAMES:
            continue

        rel = path.relative_to(root)
        ext = path.suffix.lower()
        is_stub = path.name.endswith("_Error.txt")

        rec = FileRecord(
            path=str(rel).replace("\\", "/"),
            name=path.name,
            ext=ext,
            kind="error_stub" if is_stub else config.EXT_CLASS.get(ext, "other"),
            size_bytes=path.stat().st_size,
            sha256=_sha256(path),
            zone=_zone_of(rel),
            is_error_stub=is_stub,
            error_type=_read_error_stub(path) if is_stub else "",
            cmr_refs=sorted(set(re.findall(config.CMR_REF_PATTERN, path.stem))),
        )
        records.append(rec)

    return records


def find_duplicates(records: list[FileRecord]) -> dict[str, list[FileRecord]]:
    """Group by content hash. Only groups with >1 member are duplicates."""
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for r in records:
        by_hash[r.sha256].append(r)
    return {h: rs for h, rs in by_hash.items() if len(rs) > 1}


def parse_missing_manifest() -> list[dict]:
    """
    `___All_Errors.txt` lists every path the bulk SharePoint export failed on.
    That list is the authoritative statement of what we are missing (Finding A).
    """
    manifest = config.LIVE_DATA / "___All_Errors.txt"
    if not manifest.exists():
        return []

    rows = []
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.*\S)\s*$", line)
        if not m:
            continue
        p = m.group(2)
        rows.append({
            "index": int(m.group(1)),
            "path": p,
            "dataset": _dataset_of(p),
            "is_folder": "." not in Path(p).name,
            "ext": Path(p).suffix.lower(),
        })
    return rows


def _dataset_of(p: str) -> str:
    low = p.lower()
    if "future food" in low:
        return "Future Food"
    if "glp-1" in low or "glp1" in low or "appetite" in low:
        return "Appetite Shift (GLP-1)"
    if "foodfax" in low:
        return "FoodFax"
    return "Other"


def summarise(records: list[FileRecord],
              dupes: dict[str, list[FileRecord]],
              missing: list[dict]) -> dict:
    live = [r for r in records if r.zone in ("live", "project_docs")]
    real = [r for r in live if not r.is_error_stub]

    dup_wasted = sum(
        sum(x.size_bytes for x in group[1:]) for group in dupes.values()
    )

    return {
        "files_total_walked": len(records),
        "files_live_zone": len(live),
        "files_live_real": len(real),
        "error_stubs_live": sum(1 for r in live if r.is_error_stub),
        "bytes_live_real": sum(r.size_bytes for r in real),
        "duplicate_groups": len(dupes),
        "duplicate_files": sum(len(g) - 1 for g in dupes.values()),
        "duplicate_bytes_wasted": dup_wasted,
        "by_kind": dict(Counter(r.kind for r in real).most_common()),
        # live zone only — the Archive copy carries an identical stub for
        # every failure, which would double the count
        "error_types": dict(Counter(
            r.error_type for r in live if r.is_error_stub)),
        "missing_manifest_entries": len(missing),
        "missing_by_dataset": dict(Counter(m["dataset"] for m in missing)),
        "missing_folders": sum(1 for m in missing if m["is_folder"]),
    }


def run() -> dict:
    out = config.ensure_out()
    print("  walking corpus and hashing (276 MB, takes a moment)...")
    records = walk_corpus()
    dupes = find_duplicates(records)
    missing = parse_missing_manifest()
    summary = summarise(records, dupes, missing)

    with (out / "inventory.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(records[0]).keys()))
        w.writeheader()
        for r in records:
            d = asdict(r)
            d["cmr_refs"] = ";".join(d["cmr_refs"])
            w.writerow(d)

    with (out / "duplicates.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sha256", "size_bytes", "copies", "kept_path", "duplicate_paths"])
        for h, group in sorted(dupes.items(), key=lambda kv: -kv[1][0].size_bytes):
            live_first = sorted(group, key=lambda r: (r.zone != "live", r.path))
            w.writerow([h[:16], group[0].size_bytes, len(group),
                        live_first[0].path,
                        " | ".join(r.path for r in live_first[1:])])

    with (out / "missing.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["index", "dataset", "path", "is_folder", "ext"])
        w.writeheader()
        w.writerows(missing)

    (out / "inventory_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
