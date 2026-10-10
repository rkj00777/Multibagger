"""Resolve concurrent PIT JSONL appends during a GitHub Actions rebase."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def stage_text(stage: int, path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f":{stage}:{path}"], text=True, encoding="utf-8"
    )


def fact_key(row: dict) -> tuple:
    return (
        row.get("symbol"), row.get("metric"), row.get("period_end"),
        row.get("period_start"), row.get("available_at"), row.get("value"),
        row.get("source_url"),
    )


def read_jsonl(text: str) -> dict:
    rows = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        rows[fact_key(row)] = row
    return rows


def merge_jsonl(path: str) -> None:
    merged = read_jsonl(stage_text(2, path))
    merged.update(read_jsonl(stage_text(3, path)))
    ordered = sorted(
        merged.values(),
        key=lambda x: (
            x.get("available_at", ""), x.get("symbol", ""),
            x.get("metric", ""), x.get("period_end", ""),
        ),
    )
    Path(path).write_text(
        "\n".join(json.dumps(x, separators=(",", ":"), sort_keys=True) for x in ordered) + "\n",
        encoding="utf-8",
    )


def merge_manifest(path: str) -> None:
    left = json.loads(stage_text(2, path))
    right = json.loads(stage_text(3, path))
    merged = {**left, **right}
    root = Path("data/pit/facts")
    merged["fact_files"] = sorted(p.name for p in root.glob("*.jsonl"))
    merged["merged_after_concurrent_pit_writes"] = True
    Path(path).write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    result = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=U"], text=True
    )
    paths = [p.strip() for p in result.splitlines() if p.strip()]
    if not paths:
        return
    for path in paths:
        if path.startswith("data/pit/facts/") and path.endswith(".jsonl"):
            merge_jsonl(path)
        elif path == "data/pit/manifest.json":
            merge_manifest(path)
        else:
            raise RuntimeError(f"Unsupported rebase conflict: {path}")
        subprocess.run(["git", "add", path], check=True)


if __name__ == "__main__":
    main()
