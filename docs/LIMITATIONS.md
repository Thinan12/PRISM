# Known limitations

- **Image OCR** needs `ANTHROPIC_API_KEY` (local models are not used for transcription) and has not been run against the live API in tests. Transcribed values are labelled as AI-transcribed.
- **Scanned PDFs** (no text layer) are not OCR'd; users are asked to upload page images instead.
- **Docker deployment and PostgreSQL** were not run on the development machine (no Docker). Migrations were verified on SQLite and rendered for PostgreSQL offline.
- **Header detection** is heuristic. Unusual layouts (transposed tables, headers in the first column) may need manual correction; the Phase 2 Blueprint editor is where users will fix these.
- **Ambiguous dates** (`01/02/2025`) default to day-first unless the column proves otherwise. The decision is recorded in the column notes and the workbook README.
- **Numeric durations without a unit** in the header are assumed to be seconds (recorded as a note).
- **Rate limiting** is in-memory per API process; with several uvicorn workers the effective limit is multiplied.
- **Source-sheet copies** in the workbook keep values and common formatting (fonts, fills, merges, widths, number formats) but not original formulas, charts or conditional formats. Sheets above 2M cells are not copied.
- **Re-uploading the same file** creates a new source; the SHA-256 is stored but duplicate uploads are not yet flagged.
- **Performance:** 100k rows ingest in about 12–19 s (detection and typing run in Python). That is acceptable as a background job; vectorised typing is a planned optimisation.
- **Local AI quality and speed:** `qwen2.5:7b` on a CPU-only machine takes about 3–5 minutes per Blueprint (≈11 output tokens/s) and its suggestions are weaker than Claude's (it drops the English half of bilingual names, rates almost everything 1.0 confidence, and has invented Lao words — PRISM now keeps ungrounded Lao names out). A GPU and a larger model (27B-class) are recommended for production.
- **AI Blueprint path** with Claude is tested with a mocked Anthropic client (valid output, retry after invalid output, persistently invalid output, refusal, network error, no key). It has **not** been run against the live API because no credentials were available during development; the rule-based path is fully exercised end to end. Example values sent to the AI can be disabled with `PRISM_AI_SEND_VALUES=false`; phone numbers are always masked.
- **Blueprint operations** are limited to value mapping, duplicate removal, column exclusion and type casting. Filling missing values and derived columns are deliberately not offered (no fabricated values).
- **KPI formulas in Excel**: distinct counts use the Lookups sheet (category columns only); filtered min/max and distinct counts on other columns show the PRISM value without a live formula.
- **Dashboard plan** is stored in the Blueprint but is rendered only from Phase 3.
- **Phases 3–5** (analytics and natural-language questions, Google Sheets sync, enterprise administration, sharing, scheduling, PWA) are not implemented yet. See [PROGRESS.md](../PROGRESS.md).
