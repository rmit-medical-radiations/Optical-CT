import cv2
import cv2.aruco as aruco
import numpy as np

# =========================
# Board parameters
# =========================
squaresX = 7
squaresY = 10
square_size_mm = 25.0
marker_size_mm = 18.75

dictionary = aruco.Dictionary_get(aruco.DICT_5X5_50)
board = aruco.CharucoBoard_create(
    squaresX,
    squaresY,
    square_size_mm,
    marker_size_mm,
    dictionary
)

# =========================
# Render at 300 DPI (A4)
# =========================
dpi = 300
mm_to_inch = 1 / 25.4

board_width_mm = squaresX * square_size_mm
board_height_mm = squaresY * square_size_mm

img_width_px = int(board_width_mm * mm_to_inch * dpi)
img_height_px = int(board_height_mm * mm_to_inch * dpi)

img = board.draw((img_width_px, img_height_px))

# =========================
# Save outputs
# =========================
cv2.imwrite("charuco_7x10_25mm.png", img)

# Optional: save as PDF via OpenCV + PIL
from PIL import Image

pil_img = Image.fromarray(img)
pil_img.save(
    "charuco_7x10_25mm_A4.pdf",
    "PDF",
    resolution=dpi
)

print("Generated:")
print(" - charuco_7x10_25mm.png")
print(" - charuco_7x10_25mm_A4.pdf")
