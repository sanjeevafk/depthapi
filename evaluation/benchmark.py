import asyncio
import typer
import json
from pathlib import Path
from tqdm.asyncio import tqdm
from typing import List

from generate_benchmark import generate_benchmark_dataset
from depthapi_client import DepthAPIClient
from langchain_baseline import LangChainBaseline
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
        context = res.get("context", res.get("sources", [])) # Handle different keys
        
        result_entry = {
            "id": item["id"],
            "system": system_name,
            "query": query,
            "prompt_spec": prompt_spec,
            "metadata": item.get("metadata", {}),
            "answer": answer
        }
        
        if "judge" in evals_to_run:
            judge_res = await judge.evaluate(query, answer, context, prompt_spec)
            result_entry["judge"] = judge_res
            
        if "deepeval" in evals_to_run:
            deepeval_res = evaluate_deepeval(query, answer, context)
            result_entry["deepeval"] = deepeval_res
            
        if "ragas" in evals_to_run:
            ragas_res = evaluate_ragas(query, answer, context)
            result_entry["ragas"] = ragas_res
            
        return result_entry

async def run_benchmark(size: int, evals: List[str], compare_baseline: bool):
    """Run the complete benchmark."""
    print(f"Generating dataset of size {size}...")
    dataset = generate_benchmark_dataset(size)
    
    # Save dataset
    Path("evaluation").mkdir(exist_ok=True)
    with open("evaluation/benchmark_dataset.json", "w") as f:
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
        # Create some dummy docs for baseline initialization
        dummy_docs = [{"content": f"Dummy context for {item['query']}", "metadata": {}} for item in dataset]
        baseline_client = LangChainBaseline(dummy_docs)
        tasks = [evaluate_single(item, "langchain_baseline", baseline_client, judge, evals) for item in dataset]
        baseline_results = await tqdm.gather(*tasks)
        results.extend(baseline_results)
        
    print("Analyzing results...")
    analyze_and_report(results, "evaluation/results/reports")
    
    # Save raw to evaluation/results/raw
    Path("evaluation/results/raw").mkdir(parents=True, exist_ok=True)
    with open("evaluation/results/raw/all_results.json", "w") as f:
        json.dump(results, f, indent=2)
        
    print("Benchmark complete!")

@app.command()
def main(
    size: int = typer.Option(120, help="Number of test cases to generate"),
    evals: List[str] = typer.Option(["deepeval", "ragas", "judge"], help="Evaluations to run"),
    compare_baseline: bool = typer.Option(True, "--compare-baseline/--no-baseline", help="Run against LangChain baseline")
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
    fix_sys_argv()
    app()
