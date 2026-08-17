# Optical CT Dosimetry

A research system for 3-D optical CT dosimetry of radiochromic gel dosimeters.  A Raspberry Pi streams camera images over HTTP; a PyQt6 desktop app on the control machine drives the stepper motor and lamp, captures projections, reconstructs a 3-D attenuation volume with filtered back-projection (FBP), and extracts a depth dose profile.

![App icon](optical_ct_app_icon.png)

---

## System architecture

```
Raspberry Pi                          Control machine (macOS)
─────────────────────────────         ──────────────────────────────────────
picamera2                             oct_app.py  (PyQt6)
  └─ camera_server.py  (Flask)  ◄──►    camera HTTP client
                                        stepper motor  ◄──► USB serial
                                        lamp           ◄──► FT232H USB adapter
```

The Raspberry Pi and control machine must be on the same network.  The Pi's IP address is configured in the app at runtime.

The app includes a live camera preview (refreshed every 2 s from the Pi) and a lamp toggle button.  During a scan the lamp is controlled automatically: off for dark frames, on for flat and projection captures, and left on when the scan finishes so the dosimeter remains visible.

---

## Repository contents

| File / directory | Purpose |
|---|---|
| `oct_app.py` | Main application — GUI, scan workers, FBP reconstruction, depth dose |
| `camera_server.py` | Flask HTTP server running on the Raspberry Pi; wraps `picamera2` |
| `camera-server.service` | systemd unit file to auto-start the camera server on the Pi |
| `launch_oct.sh` | Convenience launcher for the desktop app (activates conda env) |
| `lamp_control.py` | CLI utility to toggle the lamp via FT232H; used for manual testing |
| `motor_control.py` | CLI utility to jog the stepper motor; used for manual testing |
| `live_preview.py` | Standalone camera preview script |
| `capture_calibration_shots.py` | Captures ChArUco calibration images for lens correction |
| `charuco_board.py` | Generates the printable ChArUco calibration board |
| `compute_calibration_settings.py` | Computes lens distortion coefficients from calibration images |
| `test_calibration_settings.py` | Verifies computed calibration on a test image |
| `camera_calibration_charuco.json` | Saved camera intrinsics and distortion coefficients |
| `pipeline_timer.py` | Lightweight timing utility used during development |
| `raw_test.py` | Sanity-check script for raw picamera2 capture |
| `generate_user_guide.py` | Generates `optical_ct_user_guide.pdf` |
| `optical_ct_user_guide.pdf` | Printed user guide for first-time users |
| `charuco_5x5_25mm_A4.pdf` | Printable ChArUco calibration board (A4, 25 mm squares) |

---

## Setup

### Raspberry Pi (camera server)

1. Install dependencies:
   ```bash
   pip3 install flask picamera2
   ```

2. Copy the systemd service file and enable it:
   ```bash
   sudo cp camera-server.service /etc/systemd/system/
   sudo systemctl enable camera-server
   sudo systemctl start camera-server
   ```
   The server listens on port 5000 and starts automatically on boot.

### Control machine (macOS)

1. Create and activate the conda environment:
   ```bash
   conda create -n oct python=3.11
   conda activate oct
   ```

2. Install Python dependencies:
   ```bash
   pip install PyQt6 numpy opencv-python scikit-image matplotlib \
               pandas openpyxl requests pyserial
   pip install Adafruit-Blinka adafruit-circuitpython-busdevice pyftdi
   ```

3. Launch the app:
   ```bash
   ./launch_oct.sh
   # or directly:
   conda activate oct && python oct_app.py
   ```

---

## Camera calibration

Lens distortion is corrected at capture time using a pre-computed calibration stored in `camera_calibration_charuco.json`.  To recalibrate:

1. Print `charuco_5x5_25mm_A4.pdf` on A4 paper and attach it flat to a rigid backing.
2. Capture calibration images from multiple angles:
   ```bash
   python capture_calibration_shots.py
   ```
3. Compute the distortion coefficients:
   ```bash
   python compute_calibration_settings.py
   ```
4. Verify the result:
   ```bash
   python test_calibration_settings.py
   ```

---

## Scan output layout

Each scan is stored under `scans/<name>/`:

```
scans/
└── scan_20260506_143450/
    ├── pre/                  # Raw intensity projections (pre-irradiation)
    ├── post/                 # Raw intensity projections (post-irradiation)
    ├── subtracted/           # ΔA = A_post − A_pre projections (uint16 PNG) + encoding.json
    ├── calibration/          # Dark and flat frames captured at scan time
    ├── reconstruct/          # FBP volume, crop preview, sanity-check figure
    ├── depth-dose/           # Depth dose plot, Excel table, recon config (includes dose centroid offset)
    ├── dose-profiles/        # Per-slice radial dose profile PNGs + dose_profiles.xlsx
    └── scan_meta.json        # Acquisition parameters (step size, date, etc.)
```

---

## Physics notes

Optical density change between pre- and post-irradiation scans:

```
ΔA = A_post − A_pre = log(I_pre / I_post)
```

The flat-field term cancels exactly, making the measurement independent of lamp intensity drift between sessions.

After the post scan and before subtracting, the app measures how far the dosimeter was rotated about the vertical axis between the two sessions, corrects for it, and tells the operator what it did. The dosimeter is removed and reseated between sessions, so this happens; left uncorrected it stops static structure (above all the vial wall) from cancelling, and the residual dominates the reconstruction.

Δφ comes from matching whole projection profiles between the two stacks and taking the frame shift that fits best. Matching full profiles rather than a summary statistic is what makes it usable on a real scan: the post frames contain dose that the pre frames do not, and any single moment of the profile (a centroid above all) is pulled off by that extra absorbance. The correction is applied by pairing pre frame `i` with post frame `i + shift`, so every pair is two frames that were actually captured, never an interpolation. It therefore resolves Δφ only to a whole step, at most half a step out.

Two things must hold before the app trusts the answer, both recorded in `rotation_offset.json`:

- One rotation must fit clearly better than the rest (`match_separation_sigma`). A sample perfectly centred on the axis carries no rotational information and fails this.
- No sideways offset may be left over (`residual_lateral_px`). A dosimeter moved as well as turned cannot be fixed by re-pairing frames; the app still applies the rotation, but says the scan may be affected rather than reporting success.

The centroid sinusoid is still fitted, as `delta_phi_phase_deg`, for a sub-step cross-check. It is informational and never overrides the profile match.

Both thresholds (`ROTATION_MATCH_MIN_SIGMA`, `ROTATION_MAX_LATERAL_PX`) are calibrated on synthetic scans and may want adjusting against real data.

Pre and post frames are paired by the rotation angle in the filename, not by sort order, so two scans that used a different starting angle or step size cannot be silently mismatched.  Pixels where either frame reads below `MIN_VALID_COUNTS` (10 counts) are masked to ΔA = 0: down there the log ratio is quantisation noise, and a frame reading zero would otherwise produce a spurious ~14 OD spike at the vial wall.

ΔA projections are encoded as 16-bit PNG in offset binary, `value = (ΔA + 1) × 65535 / 5`, covering −1 to +4 OD.  Negative ΔA is kept rather than clipped, because clipping at zero rectifies noise and gives zero-dose regions a positive DC offset after FBP.  The encoding is recorded in `subtracted/encoding.json`; scans without that file are read with the older unsigned `OD_SCALE = 65535 / 4` scheme.

Camera frames are transported and stored as 16-bit (`counts × 256`), preserving the sub-count precision that frame stacking buys.  The camera server still serves 8-bit unless asked for `?depth=16`.

Reconstruction uses `skimage.transform.iradon` with a Hann filter.  The volume has dimensions `(depth_slices, extent_px, extent_px)` with calibrated scales:

- Lateral: 43 mm / 454 px ≈ 0.095 mm/px
- Depth: 0.1 mm/slice

The dose centroid is auto-detected from the brightest 20 % of slices in the sample ROI using a weighted centroid above the 50th-percentile threshold.  This handles beams displaced up to ~6 mm from the geometric axis.  The centroid offset is stored in `depth-dose/recon_config.json` as `dose_centroid_x_mm` and `dose_centroid_z_mm`:

- **X** — left/right displacement as seen in the camera image (negative = left of axis)
- **Z** — front/back displacement along the camera line of sight (negative = closer to the camera than the rotation axis)

---

## User guide

A printable step-by-step guide for first-time users is at `optical_ct_user_guide.pdf`.  To regenerate it after changes:

```bash
python generate_user_guide.py
```
