import os
import json
import cv2
import numpy as np
from os.path import expanduser
import shutil

# -----------------------------
# USER SETTINGS
# -----------------------------
BASE_DIR = f"{expanduser('~')}/Downloads"
CALIB_JSON = "camera_calibration_charuco.json"
INPUT_DIR = f"{BASE_DIR}/calibration_images"
OUTPUT_DIR = f"{BASE_DIR}/undistorted_calibration_images"

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff")

# -----------------------------
# Load calibration
# -----------------------------
with open(CALIB_JSON, "r") as f:
    calib = json.load(f)

K = np.array(calib["K"], dtype=np.float64)
dist = np.array(calib["dist"], dtype=np.float64)

width = calib["image_size"]["width"]
height = calib["image_size"]["height"]
size = (width, height)

print("Loaded calibration:")
print("  Image size:", size)
print("  K:\n", K)
print("  dist:", dist)

# -----------------------------
# Prepare undistortion maps
# -----------------------------
# IMPORTANT for CT: keep geometry unchanged
newK = K.copy()

map1, map2 = cv2.initUndistortRectifyMap(
    cameraMatrix=K,
    distCoeffs=dist,
    R=None,
    newCameraMatrix=newK,
    size=size,
    m1type=cv2.CV_32FC1
)

# -----------------------------
# Ensure output directory exists
# -----------------------------
if os.path.exists(OUTPUT_DIR):
    shutil.rmtree(OUTPUT_DIR)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# -----------------------------
# Process images
# -----------------------------
files = sorted(
    f for f in os.listdir(INPUT_DIR)
    if f.lower().endswith(IMAGE_EXTENSIONS)
)

if not files:
    raise RuntimeError("No images found in input directory.")

print(f"Processing {len(files)} images...")

for fname in files:
    in_path = os.path.join(INPUT_DIR, fname)
    out_path = os.path.join(OUTPUT_DIR, fname)

    img = cv2.imread(in_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"WARNING: failed to read {fname}, skipping")
        continue

    h, w = img.shape[:2]
    if (w, h) != size:
        print(
            f"WARNING: {fname} has size {(w,h)}, "
            f"expected {size}. Skipping."
        )
        continue

    undistorted = cv2.remap(
        img,
        map1,
        map2,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT
    )

    cv2.imwrite(out_path, undistorted)

print("Done. Undistorted images written to:", OUTPUT_DIR)