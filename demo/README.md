# Demo Files

This directory contains files for testing and presenting the application. 

These demo files mock the response generation by routing requests directly to the LLM instead of using the full RAG pipeline. This setup is particularly useful for quickly testing and iterating on the `prompt_spec` without the overhead of document retrieval and reranking.

## Usage

To run the demo and launch the mock environment, execute the provided script:

```bash
./start-demo.sh
```
