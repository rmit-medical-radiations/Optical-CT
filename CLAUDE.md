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
   vial wall ring survives. That is the test for whether pre/post image
   registration still needs to be built.

### Known open problem: pre/post registration

There is still **no spatial registration** between the pre and post scans. The
dosimeter is removed, irradiated elsewhere over days, and reseated, and any
translation or rotation offset in how it sits in the holder is uncorrected. A
sub-pixel offset at the vial wall (the highest-contrast edge in the frame)
leaves a bipolar residual that survives the subtraction and drives the streak
artefacts.

Pairing by angle (added 2026-08-17) removes one cause of gross mismatch, and
masking low-count pixels should stop the wall from dominating, but neither is a
substitute for registering the two image stacks.

## Decisions

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
