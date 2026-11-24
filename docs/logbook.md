# Development logbook

Consequential changes and how they were validated. Newest first.

## 2026-11-24 · Fibre launch showcase

- Added a fibre-launch showcase: the fibre mode and the output coupler alone match the LBO cavity, with focus offset and distance to the input coupler as the two settings. The search reports the 1% focus and distance tolerances and the clear-aperture transmission.
- Validated against a brute-force grid over both settings, exact waist-to-waist imaging onto a round target, the Gaussian aperture integral and a deliberately short focus travel that reports its limits. With the illustrative 6.6 µm mode field diameter and 11 mm coupler, the overlap rises from 10.91% (collimated, 600 mm) to 99.92% at +175.8 µm and 735.4 mm. A ±6.1 µm focus error or a ±23.4 mm distance error costs 1%. Browser checks at 1280 px and 390 px; no console errors.
- Review changes: per-value datasheet confirmation, kept in layout links; validation that blocks the search and the link and names the field; the result labelled ideal, unclipped overlap beside the aperture transmission, with active limits, the unconstrained overlap and one-sided tolerances at a stop; a locked coupler keeps its focus. The search now re-optimises the distance at each focus value and starts from the imaging estimate: for a 3 µm mode field diameter behind a 6 mm coupler with ±2 mm travel it reaches 99.92% at 867.7 mm, where the first version stopped at 99.65% (regression test).
- The fibre and the output coupler are the students' entry points: they sit first on the page, grouped as fibre, output coupler and bench, with datasheet hints. Example values are marked until replaced.

## 2026-11-24 · WP 1 · Student entrance

- Added the [student entrance](../start/index.html) and five task routes. A touch-emulated lens drag at 390 px changed the overlap with the marker and readout in view.

## 2026-11-24 · Work programme for a student entrance page

- Added [docs/work-programme-entrance.md](work-programme-entrance.md): a layered "Start here" page for students with beam tasks, built as a loop rather than a ladder, with an installation track for macOS, Linux and Windows. Clean, focused design is its first requirement; the goal is students who do the job properly.
- Camera access is verified on macOS only. Linux and Windows cards wait for a validation session on the lab computers.

## 2026-11-24 · 0.5.0 · Transverse intensity fits and encircled energy

- Added frozen-frame fits for Gaussian, super-Gaussian, HG₁₀, HG₁₁ and circular LG ring intensities. Comparison uses full-ROI RMS. The dashboard and inspection sheet show the signed residual, and the ZIP contains the native float32 residual and fitted parameters.
- Added live D50 and D86.5 circular-aperture diameters and four non-Gaussian simulator sources. Frozen replay retains its frame and dark-reference provenance during reanalysis. Restoring a replay snapshot checks the frame against the recording.
- Validated against independent noisy images of known geometry, a small off-center ring on a large sensor, and encircled-energy integrals. Simulator browser checks covered comparison, single-template refitting, mobile layout, export and resume. Physical-camera validation of these additions is pending.
- Fit seeds and the dense sampling window come from an iterated ±1.5 D4σ window about the brightest region, not whole-ROI moments. On a full 1440 × 1080 frame at the default 3σ threshold, isolated noise pixels inflated whole-ROI D4σ to about 480 px for an 8 px-radius spot, which biased the fit, or stopped a 3 px spot from converging. Whole-ROI D4σ itself is unchanged and remains threshold-sensitive on large ROIs.

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
