import glob
import json
import cv2
import numpy as np
import cv2.aruco as aruco
from os.path import expanduser

# -----------------------------
# USER SETTINGS
# -----------------------------
IMAGE_DIR = f"{expanduser('~')}/Downloads/calibration_images"
IMAGE_GLOB = f"{IMAGE_DIR}/*.png"
squaresX, squaresY = 5, 5
square_size_mm = 25.0
marker_size_mm = 18.75
DICT = aruco.DICT_5X5_50

# -----------------------------
# Build board + detector
# -----------------------------
dictionary = aruco.getPredefinedDictionary(DICT)
board = aruco.CharucoBoard((squaresX, squaresY), square_size_mm, marker_size_mm, dictionary)

# Newer OpenCV has an ArUcoDetector; fall back if not present
use_detector_class = hasattr(aruco, "ArucoDetector")
if use_detector_class:
    params = aruco.DetectorParameters()
    detector = aruco.ArucoDetector(dictionary, params)
else:
    params = aruco.DetectorParameters_create()

# -----------------------------
# Collect charuco observations
# -----------------------------
all_charuco_corners = []
all_charuco_ids = []
image_size = None
used_files = []

def detect_charuco(gray):
    if use_detector_class:
        corners, ids, rejected = detector.detectMarkers(gray)
    else:
        corners, ids, rejected = aruco.detectMarkers(gray, dictionary, parameters=params)

    if ids is None or len(ids) < 2:
        return None, None, corners, ids

    # refine corner positions (helps)
    aruco.refineDetectedMarkers(gray, board, corners, ids, rejected)

    # interpolate Charuco corners
    n, ch_corners, ch_ids = aruco.interpolateCornersCharuco(
        markerCorners=corners,
        markerIds=ids,
        image=gray,
        board=board
    )
    if ch_ids is None or len(ch_ids) < 12:
        return None, None, corners, ids

    return ch_corners, ch_ids, corners, ids

for fname in sorted(glob.glob(IMAGE_GLOB)):
    img = cv2.imread(fname, cv2.IMREAD_COLOR)
    if img is None:
        continue
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if image_size is None:
        image_size = (gray.shape[1], gray.shape[0])  # (w,h)

    ch_corners, ch_ids, _, _ = detect_charuco(gray)
    if ch_ids is None:
        continue

    all_charuco_corners.append(ch_corners)
    all_charuco_ids.append(ch_ids)
    used_files.append(fname)

print(f"Images found: {len(glob.glob(IMAGE_GLOB))}")
print(f"Images used (>=12 Charuco corners): {len(used_files)}")

if len(used_files) < 10:
    raise RuntimeError("Too few usable images. Improve lighting/sharpness or board size.")

# -----------------------------
# Calibrate (intrinsics + distortion + per-image extrinsics)
# -----------------------------
# Returns: rms, K, dist, rvecs, tvecs
rms, K, dist, rvecs, tvecs = aruco.calibrateCameraCharuco(
    charucoCorners=all_charuco_corners,
    charucoIds=all_charuco_ids,
    board=board,
    imageSize=image_size,
    cameraMatrix=None,
    distCoeffs=None
)

print("\n=== Calibration results ===")
print("RMS reprojection error:", rms)
print("Camera matrix K:\n", K)
print("Distortion coeffs:", dist.ravel())

# -----------------------------
# (Optional) compute mean reprojection error in pixels
# -----------------------------
total_err = 0.0
total_pts = 0

for i in range(len(all_charuco_corners)):
    ch_c = all_charuco_corners[i]
    ch_id = all_charuco_ids[i]

    obj_pts = board.getChessboardCorners()[ch_id.flatten(), :]  # Nx3 (in board frame, mm)
    obj_pts = obj_pts.reshape(-1, 1, 3).astype(np.float32)

    img_pts = ch_c.reshape(-1, 1, 2).astype(np.float32)

    proj_pts, _ = cv2.projectPoints(obj_pts, rvecs[i], tvecs[i], K, dist)
    err = cv2.norm(img_pts, proj_pts, cv2.NORM_L2)

    total_err += err**2
    total_pts += len(obj_pts)

mean_err = np.sqrt(total_err / total_pts)
print("Mean reprojection error (px):", float(mean_err))

# -----------------------------
# Save to JSON
# -----------------------------
out = {
    "image_size": {"width": image_size[0], "height": image_size[1]},
    "board": {
        "squaresX": squaresX,
        "squaresY": squaresY,
        "square_size_mm": square_size_mm,
        "marker_size_mm": marker_size_mm,
        "dictionary": int(DICT),
    },
    "K": K.tolist(),
    "dist": dist.ravel().tolist(),
    "rms": float(rms),
    "mean_reprojection_error_px": float(mean_err),
    "views": [
        {
            "file": used_files[i],
            "rvec": np.array(rvecs[i]).ravel().tolist(),
            "tvec_mm": np.array(tvecs[i]).ravel().tolist(),
        }
        for i in range(len(used_files))
    ],
}

with open("camera_calibration_charuco.json", "w") as f:
    json.dump(out, f, indent=2)

print("\nSaved: camera_calibration_charuco.json")
