import os, glob
import numpy as np
import cv2
from os.path import expanduser
from skimage.transform import iradon
import shutil
import numpy as np
import matplotlib.pyplot as plt
from pipeline_timer import PipelineTimer


SAMPLE_TOP = 280                    # pixels from the image top edge
SAMPLE_CENTRE_OF_ROTATION = 995     # pixels from the image left edge
SAMPLE_EXTENT = 700                 # pixels in height and width


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

def crop_sample_region(imgs, dark, flat):
    """
    imgs: (A, H, W)
    dark: (H, W)
    flat: (H, W)

    Returns:
        imgs_cropped: (A, 700, 700)
        dark_cropped: (700, 700)
        flat_cropped: (700, 700)
    """

    half = SAMPLE_EXTENT // 2

    # Vertical crop
    y0 = SAMPLE_TOP
    y1 = SAMPLE_TOP + SAMPLE_EXTENT

    # Horizontal crop centred on rotation axis
    cx = SAMPLE_CENTRE_OF_ROTATION
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

def depth_dose_from_central_axis(
    mu_vol: np.ndarray,
    mm_per_slice_y: float,
    roi_radius_px: int = 10,
):
    Y, Z, X = mu_vol.shape
    zc, xc = Z // 2, X // 2
    r = int(roi_radius_px)

    zL, zR = max(0, zc - r), min(Z, zc + r + 1)
    xL, xR = max(0, xc - r), min(X, xc + r + 1)

    od_depth = mu_vol[:, zL:zR, xL:xR].mean(axis=(1, 2))

    depth_mm = np.arange(Y) * mm_per_slice_y
    rel_dose = od_depth / (float(np.max(od_depth)) + 1e-12)

    return depth_mm, rel_dose, od_depth

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

def save_depth_dose_plot(depth_mm, rel_dose, output_path="depth_dose.png",
                         title="Depth dose (relative)"):
    plt.figure()
    plt.plot(depth_mm, rel_dose)
    plt.xlabel("Depth (mm)")
    plt.ylabel("Relative Dose (normalised)")
    plt.title(title)
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"saved the depth dose plot in {output_path}")

####################################################

# Setup
BASE_DIR = f"{expanduser('~')}/OCT"
IMAGE_DIR = f"{BASE_DIR}/images"
CONFIG_DIR = f"{BASE_DIR}/config"
RECONSTRUCT_DIR = f"{BASE_DIR}/reconstruct"

if os.path.exists(RECONSTRUCT_DIR):
    shutil.rmtree(RECONSTRUCT_DIR)
os.makedirs(RECONSTRUCT_DIR)

t = PipelineTimer()

# Load PNG projections, apply dark/flat, compute line integrals
with t.step("Load PNG stack"):
    imgs, files = load_png_stack(IMAGE_DIR, pattern="*.png")  # (A,H,W)
    dark = np.load(os.path.join(CONFIG_DIR, "dark.npy")).astype(np.float32)
    flat = np.load(os.path.join(CONFIG_DIR, "flat.npy")).astype(np.float32)

    # Crop images to focus on the sample
    imgs, dark, flat = crop_sample_region(imgs, dark, flat)

with t.step("Calculate line integrals"):
    P = line_integrals_from_png(imgs, dark, flat)  # (A,H,W)
    angles_deg = np.arange(P.shape[0], dtype=np.float32) * 2.0

# Reconstruct attenuation volume (slice-by-slice FBP)
with t.step("Reconstruct attenuation volume"):
    mu_vol = recon_volume_fbp(P, angles_deg)
    np.save(os.path.join(RECONSTRUCT_DIR, "attenuation_volume.npy"), mu_vol)

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
            os.path.join(RECONSTRUCT_DIR, f"profile_depth_{y:04d}.png"),
            title=f"Dose profile (depth index {y})"
        )

# Depth-dose along Y (dose beam direction)
with t.step("Compute depth dose"):
    mm_per_slice_y = 0.01  # from calibration image
    depth_mm, rel_dose, _ = depth_dose_from_central_axis(
        mu_vol,
        mm_per_slice_y=mm_per_slice_y,
        roi_radius_px=10,
    )
    save_depth_dose_plot(depth_mm, rel_dose, os.path.join(RECONSTRUCT_DIR, "depth_dose.png"))

t.report()
