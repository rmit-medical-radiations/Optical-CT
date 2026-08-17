# Optical CT project notes

Working notes for the optical CT dosimetry app. This file is the decision log:
what was decided, when, and why. Code structure lives in `README.md`; operating
instructions live in `optical_ct_user_guide.pdf`.

## Current status / next steps

As at 2026-08-17.

The projection pipeline was reworked after the sanity check for
`scan_20260701_101800` came out wrong (see "2026-08-17" below). The changes are
in, tested against synthetic data, but **not yet validated on real hardware**.

Next steps, in order:

1. Deploy the updated `camera_server.py` to the Pi and confirm
   `/capture?depth=16` returns a 16-bit PNG. The app falls back to 8-bit
   silently if the server is not updated, so this is easy to miss.
2. Re-run the subtraction on any scan that still has `pre/` and `post/`, to get
   the signed v2 encoding. Scans where only `subtracted/` survives can still be
   reconstructed, but stay on the legacy unsigned encoding.
3. Force a new attenuation volume when reconstructing anything scanned before
   2026-08-17. Cached `attenuation_volume.npy` files are stale, and legacy
   scans now decode 256x larger than they used to (correctly, see below).
4. Re-run the sanity check on `scan_20260701_101800` and see how much of the
   vial wall ring survives.
5. Get a real Δφ for `scan_20260701_101800` by re-running the subtraction on its
   existing `pre/` and `post/` folders. The measurement and correction now run
   automatically, so this also repairs that scan. Check `rotation_offset.json`
   afterwards: `confident` tells you whether the dosimeter was only turned, or
   moved as well.
6. Check the two rotation thresholds against real scans (see the calibration
   note below). They have only ever been exercised on synthetic data.

### Known open problem: pre/post registration

There is still **no correction** for how the dosimeter sits in the holder
between sessions. It is removed, irradiated elsewhere over days, and reseated,
and any translation or rotation offset is uncorrected. A sub-pixel offset at the
vial wall (the highest-contrast edge in the frame) leaves a bipolar residual
that survives the subtraction and drives the streak artefacts.

As of 2026-08-17 the **rotational** component is measured and corrected
automatically (see below). What remains uncorrected:

- **Translation.** A dosimeter set down sideways of where it was cannot be fixed
  by re-pairing frames. It is now *detected* (`residual_lateral_px`) and the
  operator is told the scan may be affected, but not corrected. It is correctable
  in principle by shifting the projections column-wise before subtracting, which
  is the obvious next piece of work if real scans show it happening.
- **Tilt.** Neither detected nor corrected. A tilted dosimeter would show up as a
  lateral offset that varies with depth, so the current single-band measurement
  would see only a partial signal.
- **Sub-step rotation.** The correction is quantised to the projection step, so
  it can be up to half a step out (1 degree at the usual 2 degree steps). For the
  vial wall this is negligible, since only the vial's eccentricity matters, about
  0.5 mm. A bubble 15 mm off-axis still smears a few pixels. If that turns out to
  matter, the fix is to subtract in the volume domain instead: reconstruct pre and
  post separately, rotate the pre volume by Δφ about the Y axis, then subtract.
  FBP is linear so this is equivalent, handles any angle exactly, and costs a
  second reconstruction.

## Decisions

### 2026-08-17: measure the pre/post rotational offset automatically

The operator for `scan_20260701_101800` did not check the dosimeter's rotational
orientation when reseating it, and separately set a different crop. The crop
turned out to be harmless (it is a reconstruction-time parameter applied to
full-frame data after subtraction, so it is fully reversible by
re-reconstructing). The rotation is the real damage, and it explains the wall
ring and the paired bright / clipped-to-zero sinusoids in the sinogram.

**The check runs inside the app, not as a script.** The first version of this
was a standalone command-line diagnostic, which was the wrong shape: the person
running the scanner is not computer-literate and will not run a Python script.
Nobody can see a 30 degree error by eye once the vial is in the holder, and the
consequence only shows up days later in a reconstruction that nobody at the
scanner is looking at. So it runs automatically after the post scan and raises a
plain-language dialog (`ScanWorker.alert`, new signal) while the operator is
still standing there. It is never fatal: the images are already saved, and a
scan with a known offset is much more useful than one with an unknown offset.

**The app corrects it, it does not just report it.** An earlier version measured
Δφ and told the operator to pass it on to whoever analysed the scan. There is no
such person: the operator is the only one who will ever look at it. A warning
nobody can act on is not a feature. So the correction is applied automatically
by pairing pre frame `i` with post frame `i + shift`.

**Method, and why the obvious one fails.** The first implementation used the
horizontal centroid of attenuation, which traces a sinusoid against angle for an
off-axis sample; reseating shifts its phase by exactly Δφ. That worked on clean
synthetic data and then failed the moment the test included dose: with dose
present only in the post frames and off-axis, it returned 37.6 degrees for a
34 degree truth. A centroid is a first moment, so any added absorbance drags it.

The working method matches **whole projection profiles** between the two stacks
and takes the frame shift minimising the squared difference. Sharp
high-contrast structure dominates that comparison, so a weak added dose barely
moves it. Recovers Δφ exactly (to the step) in every synthetic case tried,
including with off-axis dose and at 12 counts RMS noise. The corrected structure
residual matches the never-rotated baseline to 5 significant figures, so the
correction is not merely an improvement, it is a full recovery.

The centroid fit is kept as `delta_phi_phase_deg` for a sub-step cross-check,
but it is informational and explicitly never allowed to veto the profile match.
An earlier iteration did let it veto, and it rejected every correct answer.

**Correction is quantised to a whole step** so that every pair is two frames
that were actually captured. Interpolating between adjacent projections is not
the same as rotating the sample, and 2 degree steps are fine enough that it is
not worth the approximation.

**Two independent conditions gate trust**, which is a distinction worth keeping:
`rotation_known` (one shift fits clearly better than the rest) decides whether
to apply a correction, and `confident` (that, plus no leftover lateral offset)
decides what to tell the operator. A dosimeter moved as well as turned still
benefits from the rotation correction, so it is applied, but the operator is
told the scan may still be affected rather than being told it is fine.

Thresholds `ROTATION_MATCH_MIN_SIGMA` (2.5) and `ROTATION_MAX_LATERAL_PX` (2.0)
are calibrated on synthetic scans only. The separation margin between a genuine
match (4.1 sigma) and a centred sample carrying no information (2.4 sigma) is
not large, so revisit this against real data.

The dose signal itself survives an affected scan. The post scan is internally
self-consistent (one continuous rotation at fixed geometry), so the dose
reconstructs correctly in its own frame; the rotation residual is additive
contamination on top, not a distortion of the dose.

### 2026-08-17: projection encoding and pairing rework

Triggered by a sanity-check figure where the vial wall was the brightest
feature in a ΔA reconstruction. It should have cancelled entirely, since it is
identical in both scans.

**Frames are stored 16-bit, as counts x 256** (`COUNT_SUBDIV`). The camera
server was already averaging the frame stack in float32 and then rounding to
uint8, which threw away the roughly 1.5 bits of extra precision the averaging
had just bought for a stack of 8. That precision matters most where
transmission is low and `-log(I/I0)` is steepest, which is exactly at the vial
wall. `?depth=16` is opt-in on the server so an un-updated Pi keeps working;
`counts_from_raw()` accepts either depth so old scans keep reading correctly.

**Pixels below 10 counts are masked** (`MIN_VALID_COUNTS`). At 3 counts, one
count is a 33% change, roughly 0.3 OD per quantisation step, which is far
coarser than the dose signal. Worse, the old code clamped a zero-reading pixel
to 1e-6, so `log(I_pre/1e-6)` produced about 14 OD, pinned at the 4 OD ceiling.
A single black pixel became a maximum-value sinogram pixel. 10 counts is a
judgement call, not a measured threshold; the subtraction logs the masked
percentage so it can be revisited if it turns out to be too aggressive.

**ΔA is kept signed**, encoded offset-binary as `(ΔA + 1) * 65535/5` spanning
-1 to +4 OD. Clipping at zero rectifies noise, so a zero-dose region picks up a
positive DC offset after FBP instead of reconstructing to zero. Kept as uint16
PNG rather than moving to float `.npy` so the projections stay viewable in an
image viewer. The format is recorded in `subtracted/encoding.json`; a scan
without that sidecar is read as v1 (unsigned, `OD_SCALE = 65535/4`), so old
scans still reconstruct.

**Pre and post frames are paired by rotation angle**, parsed out of the
filename, not by sort order. The angle was already in the filename and was
never read back. Two sessions that used a different starting angle or step size
would previously pair by index and produce a silently wrong result. Unpaired
angles are now listed in the log and skipped.

**Fixed: `load_png_stack` was truncating the 16-bit ΔA encoding to 8 bits.** It
read the subtracted PNGs with `cv2.IMREAD_GRAYSCALE`, which down-converts
16-bit input unless `IMREAD_ANYDEPTH` or `IMREAD_UNCHANGED` is used. Every
reconstruction to date therefore ran on ΔA quantised to 256 levels over the
0 to 4 OD range, and with absolute mu values 256x too small. This is why
reconstructions from before this date will not reproduce.

**Fixed: sanity-check sagittal panel drew the ROI in the wrong place.** The
panel's lateral axis origin is the dose centroid, but the ROI band and centre
line were then offset by the centroid's own displacement from the geometric
axis, so they landed at twice the offset. The white dotted line, which was
drawn at 0, was in fact marking the centroid rather than the geometric axis it
was meant to show.

### Earlier: subtraction happens in the OD domain

`ΔA = A_post - A_pre` where `A = -log(I/I0)`. The flat field cancels exactly,
since `ΔA = log(I_pre/I_post)`, which makes the measurement independent of lamp
intensity drift between sessions. This is the reason pre and post scans can be
days apart.

Note that the dark pedestal does **not** cancel the same way, and is currently
not subtracted in the ΔA path at all: `line_integrals()` does
`(imgs - dark)/(flat - dark)`, but `_compute_subtracted()` does not use it.
Worth revisiting if the residual wall signal persists after registration.

### Earlier: axis detection uses column means, not HoughCircles

`find_axis_from_nozzle()` takes the top 20% of rows (the nozzle holder region),
excludes the outer 15% of columns each side (frame borders), and finds the
minimum of the Gaussian-smoothed per-column mean. HoughCircles was unreliable
and a centroid fallback was misled by the dark frame borders. Measured axis for
this setup is x is about 995 on a 2028 px wide image.
