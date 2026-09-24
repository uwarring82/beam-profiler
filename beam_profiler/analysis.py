"""Intensity moments on native pixels, independent of preview rendering.

D4σ is four standard deviations. It equals the 1/e² diameter for an ideal
Gaussian only. Thresholding, background and finite ROI affect second moments;
these are practical estimates, not a certified ISO 11146 measurement system.
"""
import numpy as np


def analyze(pixels, maximum, *, pixel_pitch_um=None, magnification=1., roi=None,
            subtract_border=True, noise_sigma=3., dark=None):
    height, width = pixels.shape
    x0, y0, x1, y1 = roi or (0, 0, width, height)
    raw = pixels[y0:y1, x0:x1]
    values = raw.astype(np.float64)
    if dark is not None:
        values -= dark[y0:y1, x0:x1]
    edge = np.concatenate((values[0], values[-1], values[1:-1, 0], values[1:-1, -1]))
    background = float(np.median(edge)) if subtract_border else 0.
    # The upper quantile also handles black-clipped, quantized camera noise,
    # where more than half the edge can be exactly zero and MAD alone vanishes.
    noise = float(max(1.4826 * np.median(np.abs(edge - np.median(edge))),
                      np.percentile(edge, 84.13) - np.median(edge)))
    signal = np.maximum(values - background, 0)
    signal[signal < noise_sigma * noise] = 0
    px, py = signal.sum(axis=0), signal.sum(axis=1)
    total = float(px.sum())
    scale = pixel_pitch_um / magnification if pixel_pitch_um else 1.
    warnings = []
    saturated = float(np.count_nonzero(raw >= maximum * .998) / raw.size * 100)
    if saturated > 0:
        warnings.append("Saturated pixels: reduce exposure or gain before measuring.")
    valid = total > 0 and np.count_nonzero(signal) >= 20 and total / max(float(signal.max()), 1) >= 8
    if noise > 0 and float(signal.max()) < 8 * noise:
        valid = False
    # Isolated hot pixels can exceed the peak SNR threshold across a dark frame.
    # Require a small spatially coherent core before presenting a beam width.
    above = signal > max(noise * noise_sigma, float(signal.max()) * .02)
    coherent = np.ones((max(0, above.shape[0]-2), max(0, above.shape[1]-2)), dtype=bool)
    for dy in range(3):
        for dx in range(3):
            coherent &= above[dy:dy+coherent.shape[0], dx:dx+coherent.shape[1]]
    if np.count_nonzero(coherent) < 4:
        valid = False
    metrics = {"valid": bool(valid), "unit": "µm" if pixel_pitch_um else "px",
               "scale": scale, "background_dn": background, "noise_dn": noise,
               "peak_dn": int(raw.max()), "maximum_dn": maximum,
               "peak_percent": float(raw.max() / maximum * 100), "saturated_percent": saturated,
               "roi": [x0, y0, x1, y1], "signal_sum_dn": total,
               "centroid_x_px": None, "centroid_y_px": None,
               "diameter_x": None, "diameter_y": None, "major": None, "minor": None,
               "angle_deg": None, "ellipticity": None}
    # Sensor noise varies pixel to pixel; beam wings, stray light and fringes vary
    # smoothly along the border. A spread far above the neighbour-difference noise
    # means the border is not dark, so the background and threshold remove beam signal.
    sides = (values[0], values[-1], values[:, 0], values[:, -1])
    white = float(np.std(np.concatenate([np.diff(side) for side in sides]))) / np.sqrt(2)
    if noise > 4 * white:
        warnings.append("ROI border is not dark (beam wings, stray light or fringes): background "
                        "subtraction and threshold remove beam signal, so widths are underestimated. "
                        "Fit the whole beam inside the ROI with a dark margin.")
    if valid:
        xs, ys = np.arange(x0, x1), np.arange(y0, y1)
        cx, cy = float(px @ xs / total), float(py @ ys / total)
        dx, dy = xs - cx, ys - cy
        vx, vy = float(px @ (dx * dx) / total), float(py @ (dy * dy) / total)
        cov = float(dy @ signal @ dx / total)
        eigenvalues, eigenvectors = np.linalg.eigh([[vx, cov], [cov, vy]])
        minor, major = 4 * np.sqrt(np.maximum(eigenvalues, 0)) * scale
        angle = float(np.degrees(np.arctan2(eigenvectors[1, 1], eigenvectors[0, 1])))
        angle = (angle + 90) % 180 - 90
        metrics.update(centroid_x_px=cx, centroid_y_px=cy,
                       diameter_x=float(4 * np.sqrt(vx) * scale),
                       diameter_y=float(4 * np.sqrt(vy) * scale),
                       major=float(major), minor=float(minor), angle_deg=angle,
                       ellipticity=float(minor / major) if major else None)
        boundary = float(px[:2].sum() + px[-2:].sum() + py[:2].sum() + py[-2:].sum())
        exceeds_roi = (cx - 2*np.sqrt(vx) < x0 or cx + 2*np.sqrt(vx) >= x1 or
                       cy - 2*np.sqrt(vy) < y0 or cy + 2*np.sqrt(vy) >= y1)
        # A beam cut by the ROI shrinks its own second moments, so the
        # moment-based test above can pass; also check each profile edge.
        edge_level = max(px[0], px[-1]) / max(float(px.max()), 1e-12), max(py[0], py[-1]) / max(float(py.max()), 1e-12)
        if boundary / total > .01 or exceeds_roi or max(edge_level) > .01:
            warnings.append("Signal reaches the ROI boundary; beam widths may be truncated.")
    else:
        warnings.append("No clear beam signal. Adjust exposure, background or the analysis region.")
    metrics["warnings"] = warnings
    # Integrated profiles, not central line cuts. Full arrays are exported.
    return metrics, px, py
