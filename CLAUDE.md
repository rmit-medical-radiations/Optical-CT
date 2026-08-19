# Optical CT project notes

Working notes for the optical CT dosimetry app. This file is the decision log:
what was decided, when, and why. Code structure lives in `README.md`; operating
instructions live in `optical_ct_user_guide.pdf`.

## Current status / next steps

As at 2026-08-19.

The projection pipeline was reworked after the sanity check for
`scan_20260701_101800` came out wrong (see "2026-08-17" below). The changes are
in, tested against synthetic data, but **not yet validated on real hardware**.

Next steps, in order:

1. **Keep the pre-to-post interval short.** Seven weeks of dye ageing produced a
   uniform darkening several times the beam signal on `scan_20260702_101800`.
   It is subtracted by scale rather than by symmetry, so an axis-centred beam
   survives, but a background that is not there cannot bias anything.
2. **Check the squareness of the dosimeter base and the mount seat.** Mechanical,
   not software. Worth doing for scan quality, though note it did not turn out
   to be what hid the dose. See the tilt finding of 2026-08-19.
3. Deploy the updated `camera_server.py` to the Pi and confirm
   `/capture?depth=16` returns a 16-bit PNG. The app falls back to 8-bit
   silently if the server is not updated, so this is easy to miss.
4. Re-run the subtraction on any scan that still has `pre/` and `post/`. That
   gets the current signed encoding, the rotation correction, the edge masking
   and a recorded post-scan date. Scans where only `subtracted/` survives can
   still be reconstructed, but stay on whatever encoding they were written with.
5. Force a new attenuation volume when reconstructing anything scanned before
   2026-08-17. Cached `attenuation_volume.npy` files are stale, and legacy
   scans now decode 256x larger than they used to (correctly, see below).
6. Re-run the sanity check on `scan_20260701_101800` and see how much of the
   edge ring survives.
7. Get a real Δφ for `scan_20260701_101800` by re-running the subtraction on its
   existing `pre/` and `post/` folders. The measurement and correction now run
   automatically, so this also repairs that scan. Check `rotation_offset.json`
   afterwards, above all `match_separation_sigma`: that scan had bubbles in the
   pre-scan, and whether they survived to the post scan decides whether the
   correction is trustworthy. If the specks and stars in its sanity check are
   bubbles that stayed put, correcting the rotation should make them cancel and
   they should largely disappear. If they persist, they changed between
   sessions and no rotation correction can remove them.
8. Check the two rotation thresholds against real scans (see the calibration
   note below). One real scan has now exercised the separation gate, where it
   correctly refused at 1.9 sigma; the accept side is still synthetic only.

### Known open problem: pre/post registration

The dosimeter is removed, irradiated elsewhere over days, and reseated, and how
it goes back matters: a sub-pixel offset at the dosimeter edge (the highest-contrast
edge in the frame) leaves a bipolar residual that survives the subtraction and
drives the streak artefacts.

As of 2026-08-17 the **rotational** component is measured and corrected
automatically (see below).

**Rotation was assumed to be the only degree of freedom that matters here**, on
the basis that the mount fixes the dosimeter in every other respect. A real scan
disproved that on 2026-08-19, though not in the way first written up: the
dosimeter is a tight fit and is seated consistently, but it **leans**, by 1.78
degrees in one session and 0.23 degrees in the next. Translation is therefore real, is measured as
`residual_lateral_px`, and is still **not corrected**. It is logged rather than
raised at the operator, who cannot act on it, and it is the usual explanation
when no rotation fits.

What remains uncorrected:

- **Sub-step rotation.** The correction is quantised to the projection step, so
  it can be up to half a step out (1 degree at the usual 2 degree steps). For the
  dosimeter edge this is negligible, since only the dosimeter's eccentricity matters, about
  0.5 mm. A bubble 15 mm off-axis still smears a few pixels. If that turns out to
  matter, the fix is to subtract in the volume domain instead: reconstruct pre and
  post separately, rotate the pre volume by Δφ about the Y axis, then subtract.
  FBP is linear so this is equivalent, handles any angle exactly, and costs a
  second reconstruction.

## Decisions

### 2026-08-19: a stale subtracted/ reconstructs silently and badly

A real run of the new code (app 1.0.306) on `scan_20260702_101800` reported a
38.5 mm irradiated region at 2.88:1, nothing like the 8.8 mm the same data gave
during development. The cause: `subtracted/` had been written at 15:11, before
edge masking existed, and the reconstruction at 16:38 simply read it. Nothing in
the output said so.

Isolated by reconstructing the same scan three ways:

| projections | region | elongation |
|---|---|---|
| plain, as the app used | 40.2 mm | 2.92:1 |
| edge-masked | 9.1 mm | 1.38:1 |
| edge-masked and tilt-registered | 8.8 mm | 1.35:1 |

**Edge masking is the step that matters**; the tilt registration adds almost
nothing on top, consistent with the earlier finding. Reconstruction reads
whatever is in `subtracted/`, so `encoding.json` now records `edge_masked`,
`rotation_correction_deg` and `app_version`, and the reconstruction warns when
the projections predate edge masking rather than producing a quietly wrong
answer.

**Bug found in the same run.** `fills_dosimeter` reported false for that 38.5 mm
region inside a 44 mm dosimeter. The dosimeter's width was being measured from
the *beam centroid* rather than from its own centre, which inflates it whenever
the beam is off centre: here to 74 mm, so 38.5 mm sat under the 60% threshold.
Measured about the dosimeter's centre it now correctly reports true, which is
the one warning that should have fired.

### 2026-08-19: the beam was under a rotationally symmetric background

Asked whether the depth dose could be recovered from `scan_20260702_101800`.
It can, probably, and the reason it was hidden is not what anything above
suggested.

**There is real signal.** ΔA measures +0.0039 OD in air, so lamp drift between
sessions is only 0.4%, against **+0.0976 OD through the dosimeter**. It darkened
by about 0.094 OD net.

**Almost all of it is rotationally symmetric.** The reconstruction falls
smoothly from 0.00067 at the centre to 0.00008 at the edge with no depth
structure at all, and its magnitude matches spreading 0.094 OD through 44 mm
(0.00020 predicted, 0.00026 measured). Seven weeks between pre and post scans is
ample for the dye to age all over, and that is what every attempt to locate the
beam had been measuring.

**Under it there is a beam.** Subtracting the azimuthally averaged radial
profile leaves a localised excess that holds position across all 48 depth
slices: 0.8 mm RMS scatter, where a random location in a 44 mm dosimeter would
scatter about 10 mm. Its amplitude declines smoothly with depth.

Confirmed independently in the projection domain, with no reconstruction
involved: the ΔA centroid traces a clean off-axis sinusoid, fit residual 1.5 px.
The fitted amplitude of 6.06 mm is a lower bound on the feature's radius, since
the centroid mixes it with the centred background.

A further argument that it is dose: ΔA shows only what *changed*. A casting
inclusion, a bubble, any static inhomogeneity cancels. Something had to darken
in one place, at a fixed position through the full depth, between July and
August. The alternative I cannot exclude from one scan is a region that aged
differently from its surroundings.

**What did not help.** Registering the tilt improved edge alignment from 11.05
to 0.38 px RMS and changed the dose result not at all. Edge masking helped but
was not sufficient. Worth knowing before attributing future failures to either.

Now in the pipeline: the radial background is removed before the centroid search
*and* before the depth profile is extracted, slice by slice against the same
background. On the real volume the region found went from spanning 33 to 40 mm
with the depth peak pinned at the shallowest slice, to 8.9 mm at 1.17:1 and
8.7 mm off axis, with the depth dose falling from 0.54 to 0.09 across the
dosimeter.

**Beam size is reported, not gated.** The standing figure of 1 cm turned out to
be unreliable, and both plausibility gates rested on it: a real beam wider than
assumed would have been flagged as an artefact. They were advisory, so no result
was ever corrupted, but the warning could have led someone to discard a good
scan. Beam diameter is now entered per scan and recorded for comparison. The one
size claim still made is independent of it: a region spanning most of the
dosimeter is the dosimeter.

**The background must be separated by scale, not by symmetry.** The first
version subtracted the plain azimuthal mean, which removes the background but
also erases any beam centred on the rotation axis, since such a beam is itself
rotationally symmetric. Silently deleting a centred irradiation is not
acceptable: this dosimeter happened to be irradiated off axis, the next may not
be.

Symmetry cannot separate them, so scale does: the background varies over the
width of the dosimeter, a beam over its own width. The radial profile is now
median-smoothed with a window 1.5x the beam diameter before subtraction,
mirrored about r = 0 since it is even in radius. A beam-scale bump then survives
at any radius, including zero.

Things tried and rejected on the way, all measured rather than argued:

- Low-order polynomial fits in radius. Unstable near the axis, where the inner
  bins hold few pixels: order 4 over-subtracted an on-axis beam by 46%, order 6
  kept only 53% of it.
- Two-pass source masking, estimating the background with the candidate beam
  excluded. Standard practice elsewhere, but it cannot bootstrap here: pass one
  removes an on-axis beam, so pass two has nothing to exclude. Measured 0%
  survival.
- Narrow smoothing removes background well but eats a centred beam (22% kept at
  an 8 mm window); wide smoothing keeps the beam but leaves background behind
  (813% at 20 mm, meaning the residual is mostly background). 1.5x the beam
  diameter is where those cross.

End to end on synthetic volumes with a dome background four times the beam:
recovered at 100% amplitude on axis, 101% at 2 and 5 mm off, 100% at 10 mm,
falling to 74% at 15 mm where the beam starts to meet the dosimeter edge.
Located to better than 1 mm in every case out to 10 mm.

A region found within half a beam-width of the axis is flagged, since that is
where any residual suppression acts: shape reliable, absolute level a lower
bound.

**Shorten the pre-to-post interval anyway.** Seven weeks of ageing produced a
uniform signal several times the beam. Subtracting it works, but a background
that is not there cannot bias anything.

### 2026-08-19: the dosimeter is tilted, not laterally offset

Corrects the 2026-08-18 conclusion that the dosimeter "sat at a different
eccentricity". Challenged on the grounds that it is a tight fit in the mount,
which is right: a tight fit rules out lateral slop but not tilt.

Measuring the edge centre against **height** rather than at one band settles it.
Restricting to bands where the measured width is actually the dosimeter (43 to
47 mm; below about 60% of frame height the detector finds the mount instead, at
a nominal 130 mm):

| | tilt | direction | linear fit |
|---|---|---|---|
| pre (1 Jul) | 1.78 deg | 180 deg +/- 5 | R^2 0.928 |
| post (19 Aug) | 0.23 deg | 59 deg +/- 5 | R^2 0.984 |

The offset grows linearly with height, 0.30 mm to 2.00 mm across the pre scan,
with a rock-steady phase and 0.3 to 0.4 px fit residuals. At the **bottom** of
the measured range the two scans agree closely, 0.30 mm against 0.27 mm, so the
dosimeter is seated the same and it is the lean that differs. That is tilt about
a pivot near the base, not slop.

Two further observations that probably explain the mechanism:

- The measured width grows steadily with height, about 44.3 mm to 45.1 mm over
  48 mm, so the dosimeter is slightly tapered, presumably from the casting
  mould. A tapered cylinder in a tight mount wedges at whatever depth it
  reaches, which is a route to a different lean each time.
- The tilt *direction* rotated about 121 degrees between sessions, and the
  rotation estimator's rejected best guess was -132 degrees. If the base is not
  square to the axis, reseating the dosimeter rotated carries the tilt direction
  round with it, and the net tilt is the vector sum of the base error and any
  seat error, so its magnitude changes too. That fits 1.78 and 0.23 degrees at
  roughly opposed orientations.

**This is a mechanical fix, not a software one.** Check the squareness of the
dosimeter base and of the mount seat. It is also a second, independent reason to
insert the dosimeter at the same rotational orientation every time, using the
marker dot: it makes the tilt reproducible even if the base is not square, so
the edge cancels rather than having to be masked.

### 2026-08-19: mask each projection to the dosimeter

Outside the dosimeter ΔA is zero by construction, since those rays miss it, so
zeroing there is exact. At the edge it is **not**: the dosimeter is solid
urethane dyed throughout, so material near the surface darkens like any other
and there is no inert wall as there would be with a liquid in a container. What
dominates the edge is an optical artefact, rays grazing the curved surface and
being refracted away, which carries no usable dose information and is the
sharpest, highest-contrast feature in the frame. Masking it therefore discards a
thin annulus of real dosimeter: a trade worth making while the beam is small and
near the axis, and one to revisit if dose is ever expected near the surface.

On `scan_20260702_101800` that edge residual reached +/-1.08 OD against a bulk
signal of about 0.1, and no rotation
correction can remove it when the dosimeter sat at a different distance from
the axis in each session.

Setting those columns to zero is a support constraint, not cosmetic smoothing.
Measured on that scan: the reconstructed wall ring fell 55x (0.00330 to
0.00006) while the interior was unchanged (0.00028 to 0.00027).

**It changed the dose verdict**, which is why it was worth doing rather than
being merely tidy. Over the guarded sample region the centroid moved from
16.2 mm off axis with a 33.4 mm-wide region (rejected) to 6.0 mm off axis with
an 8.3 mm-wide region (accepted). The two reconstructions differ inside the dosimeter
by 13% of the bulk signal.

**But that region is still not a beam.** Principal widths 9.7 x 6.4 mm,
elongation 1.51, spanning 1.5 to 11.8 mm from the axis, and its depth profile
peaks at the shallowest depth analysed and declines from there with no plateau
or peak. Width alone cannot tell a compact beam from an elongated smear of the
right size, so an elongation gate was added: the beam is specified by a
diameter, synthetic beams measure 1.00 to 1.01, and the measure recovers true
shape faithfully (a 1.4:1 ellipse reads 1.40). The cutoff is 1.5.

Note this scan fails that gate at 1.51, by a hundredth. The gate is calibrated
from beam physics rather than tuned to reject this scan, but it is not what
rules this scan out in any robust sense; the depth behaviour is far more
damning than the shape.

The wall-contrast cutoff for detection was set at 0.75 of the backlit field on
intuition and rejected 15 of 180 real pre frames whose wall columns were
perfectly sensible; measured contrast runs 0.57 to 0.75, so it is now 0.90.
Detection is 360/360 on this scan, and a synthetic blank frame is still
rejected.

### 2026-08-19: first real post-scan through the new pipeline

`scan_20260702_101800`: pre captured 1 Jul (8-bit, old code), post captured
19 Aug (16-bit, updated Pi). Seven weeks apart. The mixed bit depth worked, so
`counts_from_raw` earns its keep.

**The rotation gate refused, correctly.** `confident: false`, separation 1.9
sigma against the 2.5 threshold, `match_margin` 0.023 against the 0.108 floor
seen for correct answers in the sweep. Its best guess was -132 degrees and it
declined to apply it. First real exercise of that gate and it did the right
thing rather than applying a wrong correction.

**Why it refused, measured from the projections directly.** Finding the dosimeter
walls in all 180 frame pairs:

| | pre (1 Jul) | post (19 Aug) |
|---|---|---|
| wall centre | 984.1 px | 987.1 px |
| wall spacing | 470.1 px | 472.2 px (scale 1.004) |
| centre swing over a rotation | 27.0 px | 8.0 px |

The dosimeter orbited the axis at about 1.3 mm in July and 0.4 mm in August. Spacing
is unchanged so it is not magnification. **No rotation can map one onto the
other**, because rotating changes the phase of that swing and never its
amplitude. So the mount does *not* fix everything but rotation, contrary to
what was assumed on 2026-08-18, and the wall can never cancel on this pair.

**Three defects this exposed, all now fixed:**

1. The depth dose baseline averaged the first and last 50 slices. The first
   slice ran 1139% above the column median (end-face artefact), which sat
   inside that window, lifted the baseline, and clipped **77.5% of the curve to
   exactly zero**. That reads as an absence of dose rather than as clipping.
   Baseline is now a low percentile; the same scan drops to 10.2% zero.
2. Slice selection took the brightest 20% of slices with no edge guard, so the
   end face won and the "dose" centroid sat on it. With a guard band the peak
   moves from 0.0 mm depth to 34.8 mm.
3. The beam plausibility check compared *areas* with a 3x margin, which quietly
   allows a 1.7x wider region, and inferred width from area. Area only gives a
   width for a single blob: on this scan the region was 4479 scattered pixels
   spanning 41 mm of dosimeter, which the area measure read as 7.2 mm and passed as a
   beam. Width now comes from the spread of the weight about its centroid, and
   the comparison is by diameter. Synthetic 3, 5, 8 and 10 mm beams still
   locate within 0.15 mm and pass; this scan now reads 33.9 mm and is rejected.

**No recoverable beam in this scan.** The 99th percentile inside the dosimeter is
roughly flat with depth, and the brightest region is a scatter across the whole
interior rather than anything compact. That is a statement about this
reconstruction, whose dominant signal is the wall residual, not about whether
the dosimeter holds dose.

**The edge.** With the seating differing between sessions, no rotation or frame
re-pairing makes it cancel. Masking it in projection space does; see the entry
above.

### 2026-08-19: one scan, one name, both dates

Asked whether the Reconstruction panel should show the post-scan name after a
post session. It should not, because there is no post-scan name: the post
session reuses the pre scan's folder (`oct_app.py`, the "post" branch of the
scan-start handler sets `self._scan_dir = pre_scan_dir` and takes its name).
One dosimeter measurement is one folder holding `pre/`, `post/` and
`subtracted/`. A separate post folder would split the two stacks subtraction has
to read together, and renaming mid-measurement would orphan `scan_meta.json`.
The app already selects that scan in the Reconstruction panel when a post scan
finishes.

The question pointed at something real, though. The folder is named when the pre
scan is made, so after a post scan weeks later the dropdown shows only the
folder name, stamped with a date the operator has long forgotten, and the scan
she has just finished looks like an old one. Both dropdowns now label scans as
`name  (pre 1 Jul, post 19 Aug)`, with the year appended only when it is not the
current one. Nothing reads the combo text (everything uses `currentData`), so
relabelling is safe.

**The post scan date was not recorded anywhere.** `scan_meta.json` had
`pre_scan_date` and nothing for the post session. For a radiochromic dosimeter
the interval between irradiation and readout matters, since post-irradiation
darkening keeps developing, so that interval was unrecoverable once the session
ended. It is now written as soon as the post rotation completes, before the
rotation check or subtraction can fail. `update_scan_meta` merges rather than
overwrites, because the two sessions are days apart and each contributes part of
the record.

### 2026-08-18: axis of rotation is measured, not typed

Prompted by asking whether the operator should be able to set the crop at all,
since she set a different one between sessions.

**The crop cannot corrupt the subtraction.** It is a reconstruction-time
parameter: `pre/` and `post/` hold full frames, `crop_window` is applied to the
already-subtracted stack, and the scan worker's config contains no crop fields
at all. Two sessions cannot disagree about it because neither uses it. Set it
correctly and re-reconstruct; nothing is lost. Worth remembering before
redesigning anything around a crop-mismatch theory.

**But the per-scan crop restore had never worked.** The output directory is
written as `depth-dose` and was read in three places as `depth_dose`. So
selecting a scan silently left whatever was in the spinboxes, and re-running a
reconstruction could quietly use a different crop than the first run. The same
typo also meant the dose spreadsheet was never found and the "view dose" action
never enabled. Directory names are now module constants so they cannot drift
apart again.

**`crop_cx` is read-only by default.** It is the axis of rotation, which is a
measurement rather than a preference, and the app already detects it. Typing
the wrong value blurs a reconstruction without making it visibly wrong, which
is the worst kind of error to leave available. A "Set manually" tick unlocks it
for when detection fails, which the code already anticipates and logs.

The other three parameters stay editable, deliberately. `crop_top` and
`crop_extent` are set by dragging the green square and only need to bracket the
dosimeter. `sample_top` and `sample_height` are a genuine experimental choice the app
cannot infer.

**Changed settings are reported at reconstruct time.** Since settings are now
restored from the scan's own `recon_config.json`, any difference means they were
changed afterwards. That is allowed but never silent: it is logged, and a change
of axis specifically asks for confirmation, defaulting to no and reverting to
the recorded value if declined.

### 2026-08-18: the dose centroid was finding artefacts, not the beam

The operator reports the beam is under 1 cm across (the standing assumption has
always been 1 cm; `BEAM_DIAMETER_MM` keeps 10 mm as an upper bound). That makes
the beam under 2% of a reconstructed slice: 8,755 px of 490,000 at 10 mm on the
66 mm grid.

The centroid search thresholded at the **median of the whole slice**, keeping
half the pixels. Only about 3.6% of the surviving weight was beam, so the
centroid was effectively measuring the background. This is why the sanity check
for `scan_20260701_101800` put the dose centroid on a smooth, radially
symmetric blob 2.4 mm off axis: that blob is a cupping artefact, and the
centroid was tracking it rather than the beam.

Two changes, both needed:

1. **Exclude the dosimeter edge** (`dosimeter_interior_mask`). Whenever the wall fails to
   cancel it is the brightest thing in the slice, and being concentric with the
   rotation axis its centroid sits dead centre. That pulls a whole-slice
   centroid to the middle no matter how hard the map is thresholded, because
   the wall is both brighter and larger in area than a small beam. No dose is
   refraction rather than dose, so excluding it costs little. The wall radius is found
   as the strongest peak in the radial mean profile, searched outside the middle
   of the field so the central cupping cannot be mistaken for it.
2. **Threshold at half the peak above background**, the FWHM convention. An
   intermediate version sized the kept area from the assumed beam diameter,
   which worked only for beams near that size and still missed a 3 mm beam by
   3.6 mm, because a 10 mm assumption keeps 12x a 5 mm beam's area. A contrast
   ratio needs no size assumption at all: measured error is 0.07 to 0.15 mm for
   3, 5, 8 and 10 mm beams alike, with or without a strong wall ring.

The peak is read at the 99.9th percentile rather than the maximum so a single
hot pixel (bubble, clipped projection) cannot define the threshold. Verified
against a synthetic hot pixel.

`BEAM_DIAMETER_MM` is now used only to **sanity-check** the result: if the
region found is bigger than a beam that size could be, the log says the centroid
has probably locked onto an artefact and the depth dose should be treated with
suspicion. Verified on a volume containing artefacts but no dose at all, where
it correctly reports the result as implausible instead of returning a confident
wrong answer.

Slice ranking also changed from the slice mean to a high percentile: with a beam
this small the mean of a slice is background, so ranking by mean picked the
slices with the most artefact rather than the most dose.

### 2026-08-18: harden the rotation match against bubbles

The operator reported bubbles in the pre-scan for `scan_20260701_101800`, which
is also the scan where she did not check the rotational alignment before the
post scan. Bubbles turn out to matter for the correction, in both directions.

**Bubbles that persist between sessions help.** They are high-contrast off-axis
features, exactly what the profile match locks onto: match separation rose from
4.1 to 5.6 sigma with six of them, and the corrected residual was unchanged.

**Bubbles that differ between sessions can break it.** With six bubbles in the
pre scan and six different ones in the post scan, squared-error matching chose a
rotation eight steps wrong and still reported itself confident. Switching to
absolute difference cut that to one step wrong. Across a 75-run sweep over
random bubble counts, offsets and noise, absolute difference got 74 right, and
the existing 2.5 sigma gate rejected the one failure (2.47 sigma) without
rejecting any correct answer (worst correct case 2.85 sigma). That margin is
thin, and it is the main thing to re-check against real scans.

A best-versus-runner-up margin was tried as an additional gate. It separated the
cases cleanly in a small experiment and then failed on the wider sweep (worst
correct answer 0.108, the single wrong answer 0.256), so it is recorded in
`rotation_offset.json` as a diagnostic but is deliberately **not** a gate. Worth
remembering as an example of a metric that looked good on five cases.

**Encoding widened to ±4 OD (v3).** A bubble present in the pre scan and gone by
the post scan gives a large negative ΔA. The v2 floor of −1 OD clipped it, and a
first attempt at −2 OD clipped it too once bubbles overlapped in projection.
Rather than guess a third time, the range is now sized from the bound the count
mask already imposes: pixels below `MIN_VALID_COUNTS` are discarded and the flat
field is at most 255 counts, so `|A| <= log(255/10) ~ 3.2` and ±4 cannot clip.
Because `encoding.json` stores explicit scale and offset numbers rather than
just a version, every older scan still decodes correctly.

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
Nobody can see a 30 degree error by eye once the dosimeter is in the holder, and the
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

**Trust rests on the rotation match alone** (`confident`), because the mount
rules out everything else. An intermediate version also gated on the leftover
lateral offset and raised a "Dosimeter moved" dialog, which was wrong for this
rig: the operator physically cannot cause a sideways shift, so blaming them for
one is a false alarm that would only teach them to dismiss dialogs.

Thresholds `ROTATION_MATCH_MIN_SIGMA` (2.5) and `ROTATION_MAX_LATERAL_PX` (2.0)
are calibrated on synthetic scans only. The separation margin between a genuine
match (4.1 sigma) and a rotationally symmetric sample carrying no information
(2.4 sigma) is not large, so revisit this against real data. Note the failure is
self-limiting: a sample with no rotational signature also has little that a
rotation could misalign.

The dose signal itself survives an affected scan. The post scan is internally
self-consistent (one continuous rotation at fixed geometry), so the dose
reconstructs correctly in its own frame; the rotation residual is additive
contamination on top, not a distortion of the dose.

### 2026-08-17: projection encoding and pairing rework

Triggered by a sanity-check figure where the dosimeter edge was the brightest
feature in a ΔA reconstruction. It should have cancelled entirely, since it is
identical in both scans.

**Frames are stored 16-bit, as counts x 256** (`COUNT_SUBDIV`). The camera
server was already averaging the frame stack in float32 and then rounding to
uint8, which threw away the roughly 1.5 bits of extra precision the averaging
had just bought for a stack of 8. That precision matters most where
transmission is low and `-log(I/I0)` is steepest, which is exactly at the dosimeter
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
