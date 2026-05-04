
import json
import re
from collections import Counter
from pathlib import Path

def detailed_audit(json_path: str):
    path = Path(json_path)
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    total = len(chunks)
    
    # Noise Patterns
    contributor_pattern = re.compile(r"Chapter \d+ [A-Z][a-z]+ [A-Z][a-z]+")
    toc_pattern = re.compile(r"\.{10,}")
    image_pattern = re.compile(r"!\[image \d+\]")
    
    noise_stats = {
        "contributor_lists": 0,
        "toc_noise": 0,
        "image_placeholders": 0
    }
    
    source_noise = {}

    for c in chunks:
        src = c.get("source_name", "Unknown")
        content = c.get("content", "")
        
        has_noise = False
        if contributor_pattern.search(content):
            noise_stats["contributor_lists"] += 1
            has_noise = True
        if toc_pattern.search(content):
            noise_stats["toc_noise"] += 1
            has_noise = True
        if image_pattern.search(content):
            noise_stats["image_placeholders"] += 1
            has_noise = True
            
        if has_noise:
            source_noise[src] = source_noise.get(src, 0) + 1

    print(json.dumps({
        "total_chunks": total,
        "noise_stats": noise_stats,
        "source_noise_distribution": source_noise,
        "avg_length": sum(len(c["content"]) for c in chunks) / total if total > 0 else 0
    }, indent=2))

if __name__ == "__main__":
    detailed_audit("data/rag/trusted/chunks.json")
