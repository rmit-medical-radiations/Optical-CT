import os, glob
import numpy as np
import cv2
from os.path import expanduser
from skimage.transform import iradon
import shutil
import numpy as np
import matplotlib.pyplot as plt
from pipeline_timer import PipelineTimer
import nibabel as nib
from pathlib import Path
import argparse


def positive_int(value):
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"Expected integer ≥ 1, got {value}")
    return ivalue

def replace_extension(path, new_ext):
    return str(Path(path).with_suffix(new_ext))

def load_png_stack(proj_dir, pattern="*.png"):
    files = sorted(glob.glob(os.path.join(proj_dir, pattern)))
    if not files:
        raise FileNotFoundError("No PNG projections found")
    imgs = []
    for f in files:
        im = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if im is None:
            raise RuntimeError(f"Failed to read {f}")
        imgs.append(im.astype(np.float32))
    return np.stack(imgs, axis=0), files  # (A,H,W)

# standard dark/flat correction + Beer–Lambert transform used for CT projection preprocessing
def line_integrals_from_png(imgs, dark, flat, eps=1e-6):
    # Beer–Lambert: P = -log((I-D)/(F-D))
    num = np.clip(imgs - dark, eps, None)
    den = np.clip(flat - dark, eps, None)
    T = np.clip(num / den, 1e-3, 1.0)
    return -np.log(T)

def recon_volume_fbp(P, angles_deg, filter_name="hann", circle=True, output_size=None):
    A, H, W = P.shape
    if output_size is None:
        output_size = W
    vol = np.zeros((H, output_size, output_size), dtype=np.float32)
    for y in range(H):
        sino = P[:, y, :].T  # (detector=W, angles=A)
        vol[y] = iradon(sino, theta=angles_deg, filter_name=filter_name,
                        circle=circle, output_size=output_size).astype(np.float32)
    return vol

def crop_reconstruction_window(imgs, dark, flat, cx, top, extent):
    """
    imgs: (A, H, W)
    dark: (H, W)
    flat: (H, W)

    Returns:
        imgs_cropped: (A, 700, 700)
        dark_cropped: (700, 700)
        flat_cropped: (700, 700)
    """

    half = extent // 2

    # Vertical crop
    y0 = top
    y1 = y0 + extent

    # Horizontal crop centred on rotation axis
    x0 = int(cx - half)
    x1 = int(cx + half)

    # Bounds check
    H, W = dark.shape
    if y0 < 0 or x0 < 0 or y1 > H or x1 > W:
        raise ValueError("Crop exceeds image bounds")

    imgs_c = imgs[:, y0:y1, x0:x1]
    dark_c = dark[y0:y1, x0:x1]
    flat_c = flat[y0:y1, x0:x1]

    return imgs_c, dark_c, flat_c

# this function analyses the full cropped (square) reconstruction image
def dose_profile_from_volume(
    mu_vol: np.ndarray,
    mm_per_pixel_xz: float,
    depth_y: int | None = None,
    roi_radius_px: int = 10,
    lateral_axis: str = "z",   # "z" or "x"
):
    """
    Lateral dose profile across the sample cross-section at a chosen depth (Y slice).

    mu_vol: (Y, Z, X)
    depth_y: which Y slice to use (default mid-slice)
    roi_radius_px: half-width of averaging band perpendicular to the profile direction
    lateral_axis:
        "z" -> profile vs Z (front-back)
        "x" -> profile vs X (left-right)

    Returns:
        pos_mm: position along the lateral axis (mm), centred at 0
        rel_dose: normalized profile (max=1)
        od_profile: raw OD profile
    """
    Y, Z, X = mu_vol.shape
    y0 = (Y // 2) if depth_y is None else int(depth_y)

    plane = mu_vol[y0]  # (Z, X)
    zc, xc = Z // 2, X // 2
    r = int(roi_radius_px)

    if lateral_axis.lower() == "z":
        # For each z, average a small band around the central x
        xL, xR = max(0, xc - r), min(X, xc + r + 1)
        od_profile = plane[:, xL:xR].mean(axis=1)  # (Z,)
        pos_mm = (np.arange(Z) - zc) * mm_per_pixel_xz

    elif lateral_axis.lower() == "x":
        # For each x, average a small band around the central z
        zL, zR = max(0, zc - r), min(Z, zc + r + 1)
        od_profile = plane[zL:zR, :].mean(axis=0)  # (X,)
        pos_mm = (np.arange(X) - xc) * mm_per_pixel_xz

    else:
        raise ValueError("lateral_axis must be 'z' or 'x'")

    rel_dose = od_profile / (float(np.max(od_profile)) + 1e-12)
    return pos_mm, rel_dose, od_profile

# this function constrains analysis to the ROI (i.e. the sample)
def depth_dose_from_central_axis(
    mu_vol: np.ndarray,
    mm_per_slice_y: float,
    sample_top_px: int,
    sample_height_px: int,
    roi_radius_px: int = 10,
    edge_baseline_px: int = 50,
    invert: bool = True,
):
    """
    Compute relative depth-dose along Y within the sample extent.

    invert=True is often needed if higher dose appears as lower reconstructed values.
    """

    Y, Z, X = mu_vol.shape
    zc, xc = Z // 2, X // 2
    r = int(roi_radius_px)

    zL, zR = max(0, zc - r), min(Z, zc + r + 1)
    xL, xR = max(0, xc - r), min(X, xc + r + 1)

    y0 = int(sample_top_px)
    y1 = min(Y, y0 + int(sample_height_px))

    od_depth = mu_vol[y0:y1, zL:zR, xL:xR].mean(axis=(1, 2))

    # number of depth pixels near the sample edges (top and bottom) used to estimate the baseline optical density.
    n = min(edge_baseline_px, len(od_depth) // 4)
    baseline = np.mean(np.concatenate([od_depth[:n], od_depth[-n:]]))

    if invert:
        dose_signal = baseline - od_depth
    else:
        dose_signal = od_depth - baseline

    # clamp small negatives
    dose_signal = np.maximum(dose_signal, 0)

    rel_dose = dose_signal / (np.max(dose_signal) + 1e-12)
    depth_mm = np.arange(len(od_depth)) * mm_per_slice_y

    return depth_mm, rel_dose, dose_signal, od_depth

def save_dose_profile_plot(pos_mm, rel_dose, output_path, title="Dose profile"):
    plt.figure()
    plt.plot(pos_mm, rel_dose)
    plt.xlabel("Position (mm)")
    plt.ylabel("Relative Dose")
    plt.title(title)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()

def exponential_moving_average(values, alpha=0.3):
    """
    Applies exponential moving average smoothing.
    
    Args:
        values: list or array of values (e.g. loss history)
        alpha: smoothing factor (0 < alpha ≤ 1), smaller = more smoothing

    Returns:
        List of smoothed values
    """
    smoothed = []
    for i, val in enumerate(values):
        if i == 0:
            smoothed.append(val)
        else:
            smoothed.append(alpha * val + (1 - alpha) * smoothed[-1])
    return smoothed

def save_depth_dose_plot(depth_mm, rel_dose, output_path="depth_dose.png",
                         title="Depth dose (relative)"):
    smoothed_rel_dose = exponential_moving_average(rel_dose, alpha=0.1)

    plt.figure(figsize=(8, 3))
    plt.plot(depth_mm, smoothed_rel_dose)
    plt.xlabel("Depth (mm) from top of sample")
    plt.ylabel("Relative Dose (normalised)")
    plt.title(title)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"saved the depth dose plot in {output_path}")

def export_orthogonal_mid_slices(mu_vol: np.ndarray, out_dir: str, prefix: str = "atten"):
    """
    Saves three mid-slices:
      Y-mid: mu_vol[Y//2, :, :]  (Z,X)  horizontal cross-section
      Z-mid: mu_vol[:, Z//2, :]  (Y,X)  vertical
      X-mid: mu_vol[:, :, X//2]  (Y,Z)  vertical

    Uses robust intensity scaling (1st–99th percentile).
    """
    os.makedirs(out_dir, exist_ok=True)
    Y, Z, X = mu_vol.shape
    y0, z0, x0 = Y // 2, Z // 2, X // 2

    vmin, vmax = np.percentile(mu_vol, (1, 99))

    def save(img2d, name):
        plt.figure()
        plt.imshow(img2d, cmap="gray", vmin=vmin, vmax=vmax)
        plt.axis("off")
        plt.tight_layout(pad=0)
        plt.savefig(os.path.join(out_dir, f"{prefix}_{name}.png"), dpi=300)
        plt.close()

    save(mu_vol[y0, :, :], "slice_Ymid_ZX")
    save(mu_vol[:, z0, :], "slice_Zmid_YX")
    save(mu_vol[:, :, x0], "slice_Xmid_YZ")

def get_or_create_attenuation_volume(
    path: str,
    projections: np.ndarray,
    angles_deg: np.ndarray,
):
    """
    Load an existing attenuation volume if present, otherwise reconstruct it.

    path: path to attenuation_volume.npy
    projections: P array (A,H,W)
    angles_deg: projection angles

    Returns
    -------
    mu_vol : np.ndarray
    """

    if os.path.exists(path):
        print(f"Loading existing: {path}")
        return np.load(path)

    print("Reconstructing...")
    mu_vol = recon_volume_fbp(projections, angles_deg)

    # save as .npy for next steps
    np.save(path, mu_vol)

    # save for visualisation
    export_orthogonal_mid_slices(mu_vol, out_dir=RECONSTRUCT_DIR, prefix="mu")
    print(f"Saved attenuation volume to: {path}")

    return mu_vol


####################################################

parser = argparse.ArgumentParser()
parser.add_argument('--scan_name', type=str, help='Name of this scan.', required=True)
parser.add_argument('--depth_sample_top', type=positive_int, default=50, help='Number of pixels from the top of the reconstruction window to the sample ROI.', required=False)
parser.add_argument('--depth_sample_height', type=positive_int, default=450, help='Vertical height of the sample ROI in pixels.', required=False)
parser.add_argument('--crop_centre_x', type=positive_int, default=995, help='Pixels from the left edge of the uncropped projection to the centre of rotation.', required=False)
parser.add_argument('--crop_top', type=positive_int, default=50, help='Number of pixels to crop from top of raw image.', required=False)
parser.add_argument('--crop_extent', type=positive_int, default=700, help='Reconstruction window height and width in pixels.', required=False)
args = parser.parse_args()


# Setup
BASE_DIR = f"{expanduser('~')}/OCT"
SCAN_DIR = f"{BASE_DIR}/scans/{args.scan_name}"
IMAGE_DIR = f"{SCAN_DIR}/images"
RECONSTRUCT_DIR = f"{SCAN_DIR}/reconstruct"
DOSE_PROFILES_DIR = f"{SCAN_DIR}/dose-profiles"
DEPTH_DOSE_DIR = f"{SCAN_DIR}/depth-dose"

CONFIG_DIR = f"{BASE_DIR}/config"

if not os.path.exists(RECONSTRUCT_DIR):
    os.makedirs(RECONSTRUCT_DIR)

if os.path.exists(DOSE_PROFILES_DIR):
    shutil.rmtree(DOSE_PROFILES_DIR)
os.makedirs(DOSE_PROFILES_DIR)

if os.path.exists(DEPTH_DOSE_DIR):
    shutil.rmtree(DEPTH_DOSE_DIR)
os.makedirs(DEPTH_DOSE_DIR)

t = PipelineTimer()

# Load PNG projections, apply dark/flat, compute line integrals
with t.step("Load PNG stack"):
    imgs, files = load_png_stack(IMAGE_DIR, pattern="*.png")  # (A,H,W)
    dark = np.load(os.path.join(CONFIG_DIR, "dark.npy")).astype(np.float32)
    flat = np.load(os.path.join(CONFIG_DIR, "flat.npy")).astype(np.float32)

    # Crop images to focus on the sample
    imgs, dark, flat = crop_reconstruction_window(imgs, dark, flat, cx=args.crop_centre_x, top=args.crop_top, extent=args.crop_extent)

    # Visualise crop
    example_img = imgs[0]
    img_norm = cv2.normalize(example_img, None, 0, 255, cv2.NORM_MINMAX)
    img_uint8 = img_norm.astype(np.uint8)
    cv2.imwrite(os.path.join(RECONSTRUCT_DIR, "example_cropped_projection.png"), img_uint8)

with t.step("Calculate line integrals"):
    P = line_integrals_from_png(imgs, dark, flat)  # (A,H,W)
    angles_deg = np.arange(P.shape[0], dtype=np.float32) * 2.0

# Reconstruct attenuation volume (slice-by-slice FBP)
with t.step("Attenuation volume"):
    attenuation_path = os.path.join(RECONSTRUCT_DIR, "attenuation_volume.npy")
    mu_vol = get_or_create_attenuation_volume(
        attenuation_path,
        P,
        angles_deg,
    )

# Compute the dose profiles at each slice
mm_per_pixel_xz = 43 / 454
Y = mu_vol.shape[0]

with t.step("Compute dose profiles"):
    for y in range(Y):
        pos_mm, rel_dose, _ = dose_profile_from_volume(
            mu_vol,
            mm_per_pixel_xz=mm_per_pixel_xz,
            depth_y=y,
            roi_radius_px=10,
            lateral_axis="z",
        )

        save_dose_profile_plot(
            pos_mm,
            rel_dose,
            os.path.join(DOSE_PROFILES_DIR, f"profile_depth_{y:04d}.png"),
            title=f"Dose profile (depth index {y})"
        )

# Depth-dose along Y (dose beam direction)
with t.step("Compute depth dose"):
    mm_per_slice_y = 0.1  # from calibration image
    depth_mm, rel_dose, dose_signal, od_depth = depth_dose_from_central_axis(
        mu_vol,
        mm_per_slice_y=mm_per_slice_y,
        sample_top_px=args.depth_sample_top,
        sample_height_px=args.depth_sample_height,
        roi_radius_px=10,
        invert=False
    )
    save_depth_dose_plot(depth_mm, rel_dose, os.path.join(DEPTH_DOSE_DIR, "depth_dose.png"))

t.report()
