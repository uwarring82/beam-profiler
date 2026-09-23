# Measurement uncertainty

The current measurement cards represent **one camera frame**, not an average of frames. Each available error bar is the known subtotal of a **standard uncertainty budget (k = 1)** for that single-frame result. It is not a confidence interval, a certified accuracy specification, or a complete instrument uncertainty.

Pixel-pitch and optical-magnification uncertainties are **not known for this setup yet**. They default to `null`, visibly “not specified.” The software never invents a percentage tolerance or interprets missing data as exact. Even after those are supplied, residual systematic effects remain explicitly unquantified.

## Repeatability from actual measurements

The default window contains the last 60 consecutive acceptable analyzed frames (adjustable from 20 to 600); estimates appear after 20 frames. For the vector of frame measurements qᵢ, the temporal sample covariance is

```
q̄ = Σ qᵢ / N
C_A = Σ (qᵢ − q̄)(qᵢ − q̄)ᵀ / (N − 1)
s_j = sqrt(C_A[j,j])
```

This estimates the observed spread of individual frames under approximately stable conditions. It includes beam motion, source fluctuations, detector noise and varying background. It does not isolate a camera noise model. Finite windows, serial correlation and drift limit how well this spread represents future frames; the sample covariance is not an unbiased population covariance estimator under arbitrary correlation.

**The live error bar uses s, not s/√N.** The latter pertains to a mean of independent, stationary observations. Window means are available for inspection, but their standard uncertainties are deliberately `null`. Increasing the window improves characterization; it must not automatically shrink a single-frame error bar. No effective sample size or independent-frame assumption is invented.

Lag-one correlation is calculated in acquisition order. Values exceeding `2/√N` in magnitude receive a correlation diagnostic. The difference between the first and last thirds of a window is also compared with s; a difference greater than s flags change/drift. These are screening diagnostics, not formal acceptance tests or corrections, and variable processing intervals further limit the interpretation of lag correlation. A flagged spread remains descriptive and includes the change across the window.

The window resets on camera changes, exposure/gain application, analysis/ROI changes, uncertainty settings, dark-reference changes, acquisition errors, and resume after a freeze. Invalid, saturated or truncated frames break the window. No estimates persist from the previously valid scene. Rescanning the browser, polling, re-rendering a palette, or reanalyzing a frozen frame never adds independent samples. A manual **Reset window** is also available after changing optics or alignment.

No resolved temporal variation is labeled unresolved: `s = 0` is not evidence of a perfectly known measurement. With fewer than 20 samples, covariance and uncertainty values are withheld.

## Physical scale calibration

For a width w in pixels, `d = w p / M`, where p is effective pixel pitch and M is optical magnification. Enter absolute **standard** uncertainties u(p) in µm and u(M) as a dimensionless magnification, plus their correlation ρ. The default ρ = 0 is an explicit assumption of independent scale calibrations.

```
u_rel(scale)² = [u(p)/p]² + [u(M)/M]² − 2ρ u(p)u(M)/(pM)
u_cal(d) = |d| u_rel(scale)
```

The software uses first-order covariance propagation and restricts each calibration standard uncertainty to at most 10% of its nominal value. Broader or non-positive scale distributions need a different model. If only one contribution is specified, only that contribution enters the known subtotal; the other stays unknown. This subtotal is not claimed as a mathematical lower bound when omitted contributions may correlate.

Changing a nominal pitch or magnification clears its entered uncertainty. Connecting a camera clears the pitch uncertainty so a different sensor cannot inherit it. The uncertainty is not automatically inferred from the number of decimal places in a manufacturer's nominal pitch. Effective pitch needs reconsideration if the camera uses binning/decimation or the optics change.

The physical scale is shared by X, Y, major and minor diameters. Its covariance contribution is therefore

```
C_cal = v vᵀ u_rel(scale)²
```

where v contains the current physical diameter in the corresponding entries and zero elsewhere. This preserves fully correlated scale errors between diameters. The isotropic scale cancels in minor/major ellipticity, and it does not affect centroids reported in pixels, angle, or DN diagnostics. It does not model anisotropic pixels, distortion or image-axis misalignment.

Assuming the calibration inputs are independent of the measured temporal fluctuations,

```
C_known = C_A + C_cal
u_known,j = sqrt(C_known[j,j])
```

The budget table separates observed frame spread, known scale uncertainty and the known subtotal. A deliberately exact input can be entered as zero; blank always means unknown. Display rounding uses two significant digits in the uncertainty and aligns the current result to the same decimal place. Exports keep full precision.

## Orientation and non-linear quantities

Each frame's ellipticity is computed from the full image covariance. Its temporal uncertainty is evaluated from the resulting ratio time series, preserving the actual relationship between major and minor widths; independent width errors are not incorrectly propagated through this ratio.

Ellipse orientation is axial (180° periodic). Angles are centered around a doubled-angle circular mean before covariance is computed. Thus +89° and −89° differ by 2°, not 178°. Angle estimates are withheld when the mean major/minor separation is no larger than three sample standard deviations of that separation (or 0.1% of the major diameter), or when the doubled-angle resultant is below 0.8. These are operational identifiability guards, not a specified confidence interval. Unresolved angle covariance rows and columns are exported as `null`.

## Still outside this budget

- The error of the finite, fixed 8-frame dark reference. A shared reference error is not recoverable from repeated beam frames alone.
- Bias or uncertainty from threshold choice, positive clipping, ROI selection and imperfect background subtraction.
- Pixel-response nonuniformity, nonlinearity, optical aberration/distortion and axis alignment.
- Camera gain/response calibration needed to express intensity as physical optical power.
- Unobserved changes outside the sampled time interval.

These exclusions are part of the UI and exported assumptions. Saturation fraction is a quality diagnostic rather than a binomial uncertainty estimate: neighboring camera pixels are not assumed to be independent trials. The native preview and profiles continue to show the current frame; they are not silently changed into window averages.

## Audit and export

Every snapshot JSON includes the estimator identifier, target (`single_frame`), k, sample count, window timestamps/duration, input calibration uncertainties, missing contributions, per-value diagnostics, and temporal/calibration/known covariance matrices with explicit field order. `uncertainty-samples.csv` contains the exact timestamped frame measurements used in that snapshot. Freeze locks both the image and its uncertainty window. Export never combines a frozen frame with later statistics.

These conventions use the distinction between individual-observation spread and uncertainty of a mean in [NIST Type A guidance](https://physics.nist.gov/cuu/Uncertainty/typea.html), and first-order covariance propagation in [NIST's law of propagation of uncertainty](https://physics.nist.gov/cuu/Uncertainty/combination.html). See also the [NIST autocorrelation definition](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35c.htm). The screening choices, rolling window and completeness rules above are application-specific; they are not a claim of compliance with a metrology standard.
