from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .io_utils import write_markdown, write_yaml_like


def build_governance_artifacts(rows: list[dict], license_summary_path: Path, manifest_path: Path) -> dict:
    licenses = Counter()
    sources: dict[str, dict] = {}
    per_source_documents: defaultdict[str, int] = defaultdict(int)

    for row in rows:
        license_name = str(row.get("upstream_license") or "unknown")
        source = str(row.get("source") or "unknown")
        url = str(row.get("source_url") or "")
        licenses[license_name] += 1
        per_source_documents[source] += 1
        if source not in sources:
            sources[source] = {
                "source": source,
                "source_url": url,
                "upstream_license": license_name,
                "retrieved_at": row.get("retrieved_at", ""),
                "documents": 0,
            }
        sources[source]["documents"] += 1

    license_lines = [
        "# License Summary",
        "",
        "This dataset is mixed-license. Downstream consumers must respect the upstream license attached to each chunk.",
        "",
        "| Upstream License | Documents |",
        "| --- | ---: |",
    ]
    for name, count in sorted(licenses.items(), key=lambda item: (-item[1], item[0])):
        license_lines.append(f"| {name} | {count} |")
    license_lines.extend(
        [
            "",
            "## Policy",
            "",
            "- No dataset-wide MIT claim is made for upstream source content.",
            "- Per-chunk `upstream_license`, `source`, and `source_url` fields are authoritative.",
            "- Unknown licenses remain in the export and should be filtered for conservative enterprise use.",
        ]
    )
    write_markdown(license_summary_path, "\n".join(license_lines))

    manifest_lines = ["version: 1", "sources:"]
    for source_name in sorted(sources):
        info = sources[source_name]
        manifest_lines.extend(
            [
                f"  - source: {info['source']}",
                f"    source_url: {info['source_url'] or 'unknown'}",
                f"    upstream_license: {info['upstream_license']}",
                f"    retrieved_at: {info['retrieved_at'] or 'unknown'}",
                f"    documents: {info['documents']}",
            ]
        )
    write_yaml_like(manifest_path, manifest_lines)

    return {
        "licenses": dict(licenses),
        "sources": len(sources),
        "manifest_path": str(manifest_path),
        "license_summary_path": str(license_summary_path),
    }
