ZPYTHON ?= .venv/bin/python

.PHONY: research-corpus test-corpus

research-corpus:
	$(PYTHON) scripts/ingest_corpus/build_research_corpus.py

publish-research-corpus:
	$(PYTHON) scripts/ingest_corpus/build_research_corpus.py --publish

publish-hf-low-mem:
	$(PYTHON) scripts/release/export_to_hf.py

test-corpus:
	$(PYTHON) -m pytest tests/research_corpus -q
