import asyncio
import typer
import json
from pathlib import Path
from tqdm.asyncio import tqdm
from typing import List

from generate_benchmark import generate_benchmark_dataset
from depthapi_client import DepthAPIClient
from langchain_baseline import SupabaseCorpusBaseline
from run_judge import CustomLLMJudge
from run_deepeval import evaluate_deepeval
from run_ragas import evaluate_ragas
from analyze_results import analyze_and_report

app = typer.Typer()

SEMAPHORE = asyncio.Semaphore(1)

async def evaluate_single(item, system_name, client, judge, evals_to_run):
    """Run evaluation for a single item."""
    async with SEMAPHORE:
        await asyncio.sleep(1.0)
        query = item["query"]
        prompt_spec = item.get("prompt_spec")
        
        if system_name == "depthapi":
            res = await client.query(query, prompt_spec)
        else:
            res = await client.query(query)
            
        answer = res.get("answer", "")
        context = res.get("context", res.get("sources", []))
        if not context and isinstance(res.get("contexts"), list):
            context = [
                str(c.get("text") or c.get("content") or "")
                for c in res.get("contexts")
                if isinstance(c, dict) and (c.get("text") or c.get("content"))
            ]

        result_entry = {
            "id": item["id"],
            "system": system_name,
            "query": query,
            "prompt_spec": prompt_spec,
            "metadata": item.get("metadata", {}),
            "answer": answer,
            "ground_truth": item.get("ground_truth"),
            "relevant_doc_ids": item.get("relevant_doc_ids"),
            "relevant_chunk_ids": item.get("relevant_chunk_ids"),
            "difficulty": item.get("difficulty"),
            "category": item.get("category"),
            "expected_citations": item.get("expected_citations"),
        }
        if isinstance(res.get("error"), str):
            result_entry["runtime_error"] = res.get("error")
        if isinstance(res.get("contexts"), list):
            result_entry["contexts"] = res.get("contexts")
        if isinstance(res.get("citations"), list):
            result_entry["citations"] = res.get("citations")
        if isinstance(res.get("metadata"), dict):
            result_entry["runtime_metadata"] = res.get("metadata")
        
        if "judge" in evals_to_run:
            judge_res = await judge.evaluate(query, answer, context, prompt_spec, sample_id=str(item["id"]))
            result_entry["judge"] = judge_res
            
        if "deepeval" in evals_to_run:
            deepeval_res = evaluate_deepeval(query, answer, context, sample_id=str(item["id"]))
            result_entry["deepeval"] = deepeval_res
            
        if "ragas" in evals_to_run:
            ragas_res = evaluate_ragas(query, answer, context, sample_id=str(item["id"]))
            result_entry["ragas"] = ragas_res
            
        return result_entry

async def run_benchmark(size: int, evals: List[str], compare_baseline: bool):
    """Run the complete benchmark."""
    print(f"Generating dataset of size {size}...")
    dataset_path = Path("benchmark_corpus.json")
    if dataset_path.exists():
        with open(dataset_path, "r") as f:
            dataset = json.load(f)[:size]
    else:
        dataset = generate_benchmark_dataset(size)
    
    # Save dataset
    Path("results").mkdir(exist_ok=True)
    with open("results/benchmark_dataset.json", "w") as f:
        json.dump(dataset, f, indent=2)
        
    judge = CustomLLMJudge() if "judge" in evals else None
    
    results = []
    
    async with DepthAPIClient() as depth_client:
        print("Evaluating DepthAPI...")
        tasks = [evaluate_single(item, "depthapi", depth_client, judge, evals) for item in dataset]
        depth_results = await tqdm.gather(*tasks)
        results.extend(depth_results)
        
    if compare_baseline:
        print("Evaluating LangChain Baseline...")
        baseline_client = SupabaseCorpusBaseline()
        tasks = [evaluate_single(item, "langchain_baseline", baseline_client, judge, evals) for item in dataset]
        baseline_results = await tqdm.gather(*tasks)
        results.extend(baseline_results)
        
    print("Analyzing results...")
    analyze_and_report(results, "results/reports")

    # Save raw to results/raw
    Path("results/raw").mkdir(parents=True, exist_ok=True)
    with open("results/raw/all_results.json", "w") as f:
        json.dump(results, f, indent=2)
        
    print("Benchmark complete!")

@app.command()
def main(
    size: int = typer.Option(120, help="Number of test cases to generate"),
    evals: List[str] = typer.Option(["deepeval", "ragas", "judge"], help="Evaluations to run"),
    compare_baseline: bool = typer.Option(True, "--compare-baseline", help="Run against LangChain baseline")
):
    """Run the DepthAPI evaluation benchmark."""
    asyncio.run(run_benchmark(size, evals, compare_baseline))

def fix_sys_argv():
    """Workaround for Typer to support `--evals a b c` syntax."""
    import sys
    new_argv = []
    i = 0
    while i < len(sys.argv):
        if sys.argv[i] == "--evals":
            new_argv.append("--evals")
            i += 1
            if i < len(sys.argv) and not sys.argv[i].startswith("-"):
                new_argv.append(sys.argv[i])
                i += 1
            while i < len(sys.argv) and not sys.argv[i].startswith("-"):
                new_argv.append("--evals")
                new_argv.append(sys.argv[i])
                i += 1
            continue
        new_argv.append(sys.argv[i])
        i += 1
    sys.argv = new_argv

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the DepthAPI evaluation benchmark.")
    parser.add_argument("--size", type=int, default=120)
    parser.add_argument("--evals", action="append", default=[])
    parser.add_argument("--compare-baseline", action="store_true", default=False)
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.size, args.evals or ["deepeval", "ragas", "judge"], args.compare_baseline))
