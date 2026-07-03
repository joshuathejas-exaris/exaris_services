# Agent Tasks — a_comp_hcp_communication (Service 1.2)

## LLM-Wiki grounded rework (2026-07-03)

Spec:  `docs/superpowers/specs/2026-07-03-llm-wiki-grounded-hcp-monitoring-design.md`
Plan:  `docs/superpowers/plans/2026-07-03-llm-wiki-grounded-hcp-monitoring.md`

- [x] Stage 01 — Identify Competitors (`01_identify_competitors.py`) + indication-bug fix
- [x] Stage 02 — Retrieve Sources (`02_retrieve_sources.py`) — revised gate + scoped vector search
- [x] Stage 03 — Wiki Build (`03_wiki_build.py`) — ingest + quote-grounding + verify + map
- [x] Stage 04 — Synthesize (`04_synthesize.py`) — mapped/unmapped split + narratives
- [x] Stage 05 — Generate Report (`05_generate_report.py`) — examples/section + full Excel
- [x] `pipeline_common.py` shared helpers
- [x] Unit tests (`tests/`) — 41 passing, incl. Holznagel false-attribution regression
- [x] Retired old stages (`02_validated_corpus.py`, `03_wiki_extract.py`, `04_sentiment_synthesis.py`)

## Verified in this dev box

- All unit tests pass; all stages byte-compile.
- Boundaries mocked (no AWS/Snowflake/Bedrock/ONNX here).

## Still to do — live run in sbx sandbox

- [ ] End-to-end run with real Snowflake + Bedrock + `/assets` models.
- [ ] Confirm Q4: is `LLM_VALIDATION.CONTENT` the full document? If not, set
      `[wiki] content_source = chunk_concat` and add chunk reconstruction.
- [ ] Spot-check that mapped/unmapped attributions and grounded quotes look right.
