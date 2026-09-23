# Development logbook

Consequential changes and how they were validated. Newest first.

## 2026-09-24 · 0.3.0 · Session log and replay

- Added a per-run event log (`sessions/session-…/log.jsonl`), a UI log panel and `session-log.jsonl` in exports.
- Added raw-frame recording (native TIFF, `frames.csv` manifest with SHA-256, dark references) and replay of recordings as a camera source.
- Validation: 65 automated tests, without hardware. They cover log contents on disk and in exports, manifest checksums, pixel-exact replay with original timestamps and dark references, loop resets, tamper detection and rejection of unknown replay IDs. A live server smoke test with the simulator covered record → stop → replay → PNG. Hardware recording with the Firefly is not yet validated.

## 2026-09-24 · 0.2.0 · Inspection PNG and plain-text details

- **Save PNG** inspection sheet (image, overlays, profiles, all settings and results as text; text and `measurement.json` embedded as PNG text chunks). Export ZIP adds `details.txt` and `inspection.png`.
- Validation: tests check that drawn text, PNG text chunks, `details.txt` and `measurement.json` agree. Visual check of a rendered simulator sheet.

## 2026-09-23 · 0.1.0 · First public release

- Review fixes: rejected exposure/gain input no longer clears the dark reference and window; robust request logging; clear `--restore-snapshot` errors.
- MIT license, CITATION.cff, codemeta.json, pyproject packaging, CI on Python 3.11 and 3.13.
- Hardware validation (before release): FLIR Firefly FFY-U3-16S2M-DL on Apple Silicon via Aravis 0.8.36. Checked discovery, opening, control bounds, 1440 × 1080 Mono16 acquisition and the live browser UI.
