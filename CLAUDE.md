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
5. Get a real Δφ for `scan_20260701_101800`. The estimator now runs
   automatically on new post scans, but that scan predates it, so its number
   has to come from re-running the subtraction on the existing `pre/` and
   `post/` folders. The value decides which of the two correction routes below
   is worth building.

### Known open problem: pre/post registration

There is still **no correction** for how the dosimeter sits in the holder
between sessions. It is removed, irradiated elsewhere over days, and reseated,
and any translation or rotation offset is uncorrected. A sub-pixel offset at the
vial wall (the highest-contrast edge in the frame) leaves a bipolar residual
that survives the subtraction and drives the streak artefacts.

As of 2026-08-17 the rotational component is at least **measured** and reported
(see below), but not corrected. Two ways to correct it, once a real Δφ has been
measured on a scan:

1. If Δφ is near a multiple of the step, re-pair post frame `i + shift` with pre
   frame `i`. `rotation_offset.json` already reports the shift to use. Cheap,
   and the leftover error is under half a step. For the wall that is negligible
   (only the vial's eccentricity matters, about 0.5 mm), but a bubble 15 mm
   off-axis still smears a few pixels.
2. If Δφ is arbitrary, subtract in the volume domain: reconstruct pre and post
   separately, rotate the pre volume by Δφ about the Y axis, then subtract. FBP
   is linear so this is equivalent, and it handles any angle exactly. Costs a
   second reconstruction.

Translation and tilt are still unhandled by either. The cross-correlation check
in the estimator will disagree with the phase fit when the difference is not a
pure rotation, which is the signal that one of those is present.

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

**Method.** The horizontal centroid of attenuation in each projection traces a
sinusoid against angle when the sample is not perfectly centred on the axis.
Reseating by Δφ shifts that sinusoid's phase by exactly Δφ, so fitting
`c0 + a sin θ + b cos θ` to both stacks and differencing the phases recovers it.
A circular cross-correlation of the same two series gives an independent
estimate. Recovers a known offset to better than 0.1 degrees on synthetic data,
including at 12 counts RMS noise.

**Caveat that matters:** the method needs the sample to be slightly off-axis. A
perfectly centred sample produces no sinusoid and no recoverable phase, so the
result carries a `confident` flag and `notes`. Do not silently trust
`delta_phi_deg` without checking it.

The dose signal itself should still be recoverable from an affected scan. The
post scan is internally self-consistent (one continuous rotation at fixed
geometry), so the dose reconstructs correctly in its own frame; the rotation
residual is additive contamination on top, not a distortion of the dose.

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
