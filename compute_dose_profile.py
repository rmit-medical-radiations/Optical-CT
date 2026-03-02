import os, glob
import numpy as np
import cv2
from os.path import expanduser
from skimage.transform import iradon
import shutil
import numpy as np
import matplotlib.pyplot as plt


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

def line_integrals_from_png(imgs, dark, flat, eps=1.0):
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

def depth_dose_curve_from_volume(
    mu_vol: np.ndarray,
    mm_per_pixel: float,
    roi_radius_px: int = 10,
    axis_depth: str = "z",
):
    """
    mu_vol: (Y, Z, X)
    Returns depth_mm, rel_dose, od_profile
    """
    Y, Z, X = mu_vol.shape
    y0 = Y // 2
    z0 = Z // 2
    x0 = X // 2

    plane = mu_vol[y0]  # (Z, X)
    r = int(roi_radius_px)

    if axis_depth.lower() == "z":
        xL = max(0, x0 - r)
        xR = min(X, x0 + r + 1)
        od_profile = plane[:, xL:xR].mean(axis=1)           # (Z,)
        depth_mm = (np.arange(Z) - z0) * mm_per_pixel

    elif axis_depth.lower() == "x":
        zL = max(0, z0 - r)
        zR = min(Z, z0 + r + 1)
        od_profile = plane[zL:zR, :].mean(axis=0)           # (X,)
        depth_mm = (np.arange(X) - x0) * mm_per_pixel

    else:
        raise ValueError("axis_depth must be 'z' or 'x'")

    rel_dose = od_profile / (float(np.max(od_profile)) + 1e-12)
    return depth_mm, rel_dose, od_profile


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

# Load PNG projections, apply dark/flat, compute line integrals
imgs, files = load_png_stack(IMAGE_DIR, pattern="*.png")  # (A,H,W)
dark = np.load(os.path.join(CONFIG_DIR, "dark.npy")).astype(np.float32)
flat = np.load(os.path.join(CONFIG_DIR, "flat.npy")).astype(np.float32)

# Crop images to focus on the sample
imgs, dark, flat = crop_sample_region(imgs, dark, flat)

P = line_integrals_from_png(imgs, dark, flat)  # (A,H,W)
angles_deg = np.arange(P.shape[0], dtype=np.float32) * 2.0

# Reconstruct attenuation volume (slice-by-slice FBP)
mu_vol = recon_volume_fbp(P, angles_deg)
np.save(os.path.join(RECONSTRUCT_DIR, "attenuation_volume.npy"), mu_vol)

# Compute the depth-dose from a small ROI around the centre
mm_per_pixel = 43 / 454
depth_mm, rel_dose, od = depth_dose_curve_from_volume(
    mu_vol,
    mm_per_pixel=mm_per_pixel,
    roi_radius_px=10,
    axis_depth="z",  # or "x"
)

# Save the plot to a file
save_depth_dose_plot(depth_mm, rel_dose, os.path.join(RECONSTRUCT_DIR, "depth_dose.png"))
