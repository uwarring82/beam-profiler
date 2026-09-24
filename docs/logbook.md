# Development logbook

Consequential changes and how they were validated. Newest first.

## 2026-09-24 · 0.4.0 · One-shot auto exposure

- Added software auto exposure aimed at a 75% peak in the analysis ROI (accepted 60–90%), at fixed gain. The camera's mean-brightness ExposureAuto stays disabled because it saturates small bright beams, which was the failure in the first real frame (30 ms, 5 dB, 6.3% saturated).
- The 20th-brightest pixel is used as the robust peak. Exposure is ÷4 while saturated and scaled linearly otherwise (step factor bounded to 0.1–10). At most 8 steps, capped at 1 s; the result reports when camera limits are reached. It clears the dark reference and window, and is logged.
- Validation: 79 tests on the simulator. They cover convergence from saturated, dim and good starts, hot pixels, both limit cases, the replay lock and invalid targets. Not yet validated on the Firefly.

## 2026-09-24 · 0.3.2 · First real beam frame: non-dark border detection

The first real beam frame was a 650 nm laser pointer through single-mode fibre (no collimator), ND 3.0, Firefly at 30 ms and 5 dB, frozen at 07:51:32 UTC. The diverging beam is larger than the 5.0 × 3.7 mm sensor, and 6.3% of pixels are saturated. The border median (7680 DN) and the border spread (5376 DN) were beam wings, not background and noise. Subtracting them and thresholding at 3 σ left only the core: D4σ 2.66 × 2.74 mm, against 4.1 × 3.6 mm with no threshold or border subtraction, which is itself only a lower bound. Only saturation was flagged.

- Added a border-darkness test: a border spread above 4× the neighbour-difference noise marks structured light. It gives 12× on this frame and 1.0–2.4 for simulator, Gaussian and black-clipped quantized noise.
- Validation: 72 tests; the warning fires on the recorded frame.

## 2026-09-24 · 0.3.1 · Fixes found by a simulator dashboard tour

A scripted tour drove the dashboard through its states with the simulator: offline, warm-up, live, saturated, empty ROI, truncating ROI, dark reference, frozen, recording, replay, PNG. It found two measurement-integrity gaps:

- **Truncation not flagged.** An ROI cutting the beam at about 0.8 of the 1/e² radius shrank the second moments (D4σ X 695 µm instead of about 833 µm) without a warning, so a normal uncertainty was shown. Added a profile-edge test: an integrated profile at an ROI edge above 1% of its peak now raises the truncation warning.
- **Dark reference containing the beam accepted silently.** Each dark reference (captured, restored or replayed) is now analyzed like a beam frame. If it contains a beam-like signal, every measurement using it carries a warning and uncertainty is withheld.
- Value cards turn amber whenever a quality warning applies.
- Validation: 68 tests, including regressions for both gaps; the tour was re-run and both states are now flagged.

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
