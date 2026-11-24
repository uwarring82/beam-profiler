"""Single-plane intensity templates on native, dark-corrected ROI pixels."""
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter
from scipy.optimize import least_squares


MODELS = {
    "gaussian": "Gaussian",
    "super_gaussian": "Super-Gaussian",
    "hg10": "HG₁₀ (two lobes)",
    "hg11": "HG₁₁ (four lobes)",
    "lg01": "LG (p = 0, |ℓ| = 1)",
}


def intensity(model, u, v, order=1.):
    """Unit-peak intensity, with u and v in units of the envelope radii."""
    q = u*u + v*v
    if model == "super_gaussian":
        return np.exp(-2 * q**order)
    envelope = np.exp(-2 * q)
    if model == "gaussian":
        return envelope
    if model == "hg10":
        return (2 * np.e) * u*u * envelope
    if model == "hg11":
        return (2 * np.e)**2 * u*u * v*v * envelope
    if model == "lg01":
        return (2 * np.e) * q * envelope
    raise ValueError("Unknown intensity model.")


def _template(model, x, y, params):
    cx, cy, wu = params[:3]
    dx, dy = x - cx, y - cy
    if model == "lg01":
        return intensity(model, dx / wu, dy / wu)
    wv, theta = params[3:5]
    c, s = np.cos(theta), np.sin(theta)
    return intensity(model, (c*dx + s*dy) / wu, (-s*dx + c*dy) / wv,
                     params[5] if model == "super_gaussian" else 1.)


def _seed(data, metrics):
    """Centroid and second moments in an iterated window about the brightest region.

    Whole-ROI moments include every isolated noise pixel above threshold. On a
    large sensor these dominate the second moments of a small spot. Iterate a
    window of +/-1.5 D4sigma per axis, as in ISO 11146, starting at the peak.
    """
    signal = data - metrics["background_dn"]
    signal[signal < 3 * metrics["noise_dn"]] = 0
    height, width = signal.shape
    py, px = np.unravel_index(np.argmax(uniform_filter(signal, 3)), signal.shape)
    box = (max(px - 4, 0), max(py - 4, 0), min(px + 5, width), min(py + 5, height))
    for _ in range(40):
        bx0, by0, bx1, by1 = box
        sub = signal[by0:by1, bx0:bx1]
        total = sub.sum()
        if total <= 0:
            return None
        xs, ys = np.arange(bx0, bx1), np.arange(by0, by1)
        cx, cy = sub.sum(axis=0) @ xs / total, sub.sum(axis=1) @ ys / total
        vx = sub.sum(axis=0) @ (xs - cx)**2 / total
        vy = sub.sum(axis=1) @ (ys - cy)**2 / total
        cov = (ys - cy) @ sub @ (xs - cx) / total
        hx, hy = 6 * np.sqrt(vx) + 2, 6 * np.sqrt(vy) + 2
        new = (max(int(np.floor(cx - hx)), 0), max(int(np.floor(cy - hy)), 0),
               min(int(np.ceil(cx + hx)) + 1, width), min(int(np.ceil(cy + hy)) + 1, height))
        if new == box:
            break
        box = new
    eigenvalues, eigenvectors = np.linalg.eigh([[vx, cov], [cov, vy]])
    minor, major = 2 * np.sqrt(np.maximum(eigenvalues, .25))
    x0, y0 = metrics["roi"][:2]
    return {"cx": x0 + cx, "cy": y0 + cy, "major": major, "minor": minor,
            "angle": np.arctan2(eigenvectors[1, 1], eigenvectors[0, 1]),
            "diameter_x": 4 * np.sqrt(vx), "diameter_y": 4 * np.sqrt(vy)}


def _sample_axis(start, stop, center, diameter):
    # Midpoints of native-pixel intervals. Resolve a small spot even when it
    # occupies only a few pixels of a megapixel sensor. Include the full ROI.
    lo, hi = np.clip([np.floor(center - 1.5*diameter), np.ceil(center + 1.5*diameter) + 1],
                     start, stop).astype(int)
    edges = [start]
    for end, limit in [(lo, 12), (hi, 84), (stop, 12)]:
        length = end - edges[-1]
        if length:
            edges.extend(np.linspace(edges[-1], end, min(length, limit) + 1, dtype=int)[1:])
    widths = np.diff(edges)
    return np.asarray(edges[:-1]) + (widths - 1) // 2, widths


def _fit_candidate(model, x, y, values, weights, m, seed):
    # Eliminate the linear peak and pedestal analytically at every solver step.
    weight_sum = weights.sum()
    mean = np.sum(weights * values) / weight_sum
    centered = values - mean
    sqrt_weights = np.sqrt(weights)

    def linear(shape):
        shape_mean = np.sum(weights * shape) / weight_sum
        variation = shape - shape_mean
        denominator = np.sum(weights * variation**2)
        peak = max(0., np.sum(weights * centered * variation) / max(denominator, 1e-30))
        return peak, mean - peak * shape_mean

    def residual(params):
        shape = _template(model, x, y, params)
        peak, offset = linear(shape)
        return ((peak * shape + offset - values) * sqrt_weights).ravel()

    x0, y0, x1, y1 = m["roi"]
    upper_radius = 2 * max(x1 - x0, y1 - y0)
    low = [x0, y0, 1.]
    high = [x1 - 1, y1 - 1, upper_radius]
    # Envelope-radius seeds in pixels: half the D4sigma diameter.
    major, minor = seed["major"], seed["minor"]
    center = [seed["cx"], seed["cy"]]
    angle = seed["angle"]
    if model == "lg01":
        starts = [[*center, np.sqrt(major * minor / 2)]]
    else:
        low += [1., -np.inf]
        high += [upper_radius, np.inf]
        starts = [[*center, major, minor, angle]]
        if model == "hg10":
            # Lobe direction can lie along either moment axis.
            starts = [[*center, major / np.sqrt(3), minor, angle],
                      [*center, minor / np.sqrt(3), major, angle + np.pi/2]]
        elif model == "hg11":
            # A square four-lobe pattern has no preferred covariance axis.
            starts = [[*center, major / np.sqrt(3), minor / np.sqrt(3), angle + delta]
                      for delta in [0., np.pi/4]]
        elif model == "super_gaussian":
            low.append(1.)
            high.append(10.)
            starts[0].append(2.)

    best = None
    for start in starts:
        initial = np.clip(start, low, high)
        scales = np.maximum(initial, 1.)
        scales[:2] = max(major, 1.)
        if len(scales) >= 5:
            scales[4] = 1.  # radians
        result = least_squares(residual, initial, bounds=(low, high), x_scale=scales,
                               ftol=1e-6, max_nfev=150)
        if result.success and (best is None or result.cost < best.cost):
            best = result
    if best is None:
        return None
    peak, offset = linear(_template(model, x, y, best.x))
    if peak <= 0:
        return None
    return best.x, peak, offset


@dataclass
class FitResult:
    summary: dict
    residual: np.ndarray  # float32 DN, ROI origin given in summary


def fit_frame(pixels, dark, metrics, requested="compare"):
    """Fit one template or compare all on the same ROI of one frame."""
    if not isinstance(requested, str) or (requested != "compare" and requested not in MODELS):
        raise ValueError("Choose a supported intensity model.")
    if not metrics["valid"] or metrics["warnings"]:
        raise ValueError("Resolve the frame quality warnings before fitting. " + " ".join(metrics["warnings"]))

    x0, y0, x1, y1 = metrics["roi"]
    data = pixels[y0:y1, x0:x1].astype(float)
    if dark is not None:
        data -= dark[y0:y1, x0:x1]
    seed = _seed(data, metrics)
    if seed is None:
        raise ValueError("No beam signal found for the fit seed. Inspect the ROI and image.")
    xs, wx = _sample_axis(x0, x1, seed["cx"], seed["diameter_x"])
    ys, wy = _sample_axis(y0, y1, seed["cy"], seed["diameter_y"])
    sample = data[np.ix_(ys-y0, xs-x0)]
    weights = wy[:, None] * wx
    variance = float(data.var())
    candidates, failed = [], []
    best_rms, best_residual = np.inf, None
    for model in MODELS if requested == "compare" else [requested]:
        fitted = _fit_candidate(model, xs, ys[:, None], sample, weights, metrics, seed)
        if fitted is None:
            failed.append(model)
            continue
        params, peak, offset = fitted
        # Score every native pixel, including the background and dark-subtracted
        # negative values. Evaluate in strips to bound temporary array sizes.
        residual = np.empty(data.shape, dtype=np.float32)
        x = np.arange(x0, x1)
        for start in range(0, len(data), 128):
            stop = min(start + 128, len(data))
            y = np.arange(y0 + start, y0 + stop)[:, None]
            prediction = peak * _template(model, x, y, params) + offset
            residual[start:stop] = data[start:stop] - prediction
        mse = float(np.mean(np.square(residual, dtype=np.float64)))
        rms = np.sqrt(mse)
        if rms < best_rms:
            best_residual, best_rms = residual, rms
        cx, cy, wu = params[:3]
        angle, wv = None, wu
        if model != "lg01":
            wv, angle = params[3:5]
            if model != "hg10" and wv > wu:
                wu, wv, angle = wv, wu, angle + np.pi/2
            angle = float((np.degrees(angle) + 90) % 180 - 90)
        candidates.append({
            "model": model, "label": MODELS[model], "center_x_px": float(cx), "center_y_px": float(cy),
            "radius_u_px": float(wu), "radius_v_px": float(wv), "angle_deg": angle,
            "angle_resolved": bool(angle is not None and (model in ("hg10", "hg11") or wu-wv >= .01*wu)),
            "order": float(params[5]) if model == "super_gaussian" else None,
            "peak_above_offset_dn": float(peak), "offset_dn": float(offset),
            "rmse_dn": float(rms), "r_squared": 1. - mse / variance if variance > 0 else None,
        })
    if not candidates:
        raise ValueError("The fit did not converge. Inspect the ROI and image.")
    candidates.sort(key=lambda row: row["rmse_dn"])
    return FitResult({"requested": requested, "roi": list(metrics["roi"]), "unit": metrics["unit"],
                      "length_per_px": metrics["scale"], "sample_pixels": sample.size,
                      "residual_definition": "dark-corrected ROI pixels minus model including constant offset, DN",
                      "roi_pixels": data.size, "candidates": candidates, "failed_models": failed},
                     best_residual)
