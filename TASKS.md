# Agent Tasks — a_comp_hcp_communication (Service 1.2)

## Pipeline Stages

- [ ] Stage 01: Identify Competitors (`01_identify_competitors.py`)
- [ ] Stage 02: Validated Corpus (`02_validated_corpus.py`)
- [ ] Stage 03: Wiki Extract (`03_wiki_extract.py`)
- [ ] Stage 04: Sentiment Synthesis (`04_sentiment_synthesis.py`)
- [ ] Stage 05: Generate Report (`05_generate_report.py`)

## Review

- [ ] Data contract consistency (output schema of each stage matches input of next)
- [ ] Edge cases handled (0 SQL results, empty facts, LLM failures)
- [ ] No hardcoded values — all params in config.ini

## How agents update this file

When your stage is built and tested:
Replace `- [ ] Stage 0N` with `- [x] Stage 0N: DONE`
