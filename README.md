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
| `scan_sample.py` | Standalone scan script (superseded by `oct_app.py`) |
| `compute_dose_profile.py` | Standalone FBP + dose profile script (superseded by `oct_app.py`) |
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
    ├── subtracted/           # ΔA = A_post − A_pre projections (uint16 PNG)
    ├── calibration/          # Dark and flat frames captured at scan time
    ├── reconstruct/          # FBP volume, crop preview, sanity-check figure
    ├── depth-dose/           # Depth dose plot, Excel table, recon config
    ├── dose-profiles/        # Per-slice radial dose profiles
    └── scan_meta.json        # Acquisition parameters (step size, date, etc.)
```

---

## Physics notes

Optical density change between pre- and post-irradiation scans:

```
ΔA = A_post − A_pre = log(I_pre / I_post)
```

The flat-field term cancels exactly, making the measurement independent of lamp intensity drift between sessions.  ΔA projections are encoded as 16-bit PNG with `OD_SCALE = 65535 / 4` (range 0–4 OD).

Reconstruction uses `skimage.transform.iradon` with a Hann filter.  The volume has dimensions `(depth_slices, extent_px, extent_px)` with calibrated scales:

- Lateral: 43 mm / 454 px ≈ 0.095 mm/px
- Depth: 0.1 mm/slice

---

## User guide

A printable step-by-step guide for first-time users is at `optical_ct_user_guide.pdf`.  To regenerate it after changes:

```bash
python generate_user_guide.py
```
