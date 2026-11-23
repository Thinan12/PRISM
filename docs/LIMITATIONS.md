# Known limitations

- **Image OCR** needs `ANTHROPIC_API_KEY` and has not been run against the live API in tests. Transcribed values are labelled as AI-transcribed.
- **Scanned PDFs** (no text layer) are not OCR'd; users are asked to upload page images instead.
- **Docker deployment and PostgreSQL** were not run on the development machine (no Docker). Migrations were verified on SQLite and rendered for PostgreSQL offline.
- **Header detection** is heuristic. Unusual layouts (transposed tables, headers in the first column) may need manual correction; the Phase 2 Blueprint editor is where users will fix these.
- **Ambiguous dates** (`01/02/2025`) default to day-first unless the column proves otherwise. The decision is recorded in the column notes and the workbook README.
- **Numeric durations without a unit** in the header are assumed to be seconds (recorded as a note).
- **Rate limiting** is in-memory per API process; with several uvicorn workers the effective limit is multiplied.
- **Source-sheet copies** in the workbook keep values and common formatting (fonts, fills, merges, widths, number formats) but not original formulas, charts or conditional formats. Sheets above 2M cells are not copied.
- **Re-uploading the same file** creates a new source; the SHA-256 is stored but duplicate uploads are not yet flagged.
- **Performance:** 100k rows ingest in about 12–19 s (detection and typing run in Python). That is acceptable as a background job; vectorised typing is a planned optimisation.
- **Phases 2–5** (Blueprint, analytics and natural-language questions, Google Sheets sync, enterprise administration, sharing, scheduling, PWA) are not implemented yet. See [PROGRESS.md](../PROGRESS.md).
