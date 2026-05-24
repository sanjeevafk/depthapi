import json
from pathlib import Path
import sys

# Load results JSON produced previously
results_path = Path("results/raw/all_results.json")
if not results_path.exists():
    print(f"Results file not found: {results_path}")
    sys.exit(1)

with open(results_path, "r") as f:
    results = json.load(f)

# Add repo to sys.path and import analyze helper
sys.path.insert(0, ".")
try:
    from analyze_results import analyze_and_report
except Exception as e:
    print(f"Failed to import analyze_results: {e}")
    sys.exit(1)

out_dir = "results/analysis_report"
analyze_and_report(results, out_dir)
print(f"Analysis complete. Reports in {out_dir}")
