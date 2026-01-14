import cv2
import cv2.aruco as aruco
from PIL import Image

# Board parameters
squaresX, squaresY = 5, 5
square_size_mm = 25.0
marker_size_mm = 18.75

dictionary = aruco.getPredefinedDictionary(aruco.DICT_5X5_50)
board = aruco.CharucoBoard(
    (squaresX, squaresY),
    square_size_mm,
    marker_size_mm,
    dictionary
)

# Render at 300 DPI
dpi = 300
mm_to_inch = 1 / 25.4

width_px = int(squaresX * square_size_mm * mm_to_inch * dpi)
height_px = int(squaresY * square_size_mm * mm_to_inch * dpi)

img = board.generateImage((width_px, height_px))

name = f"charuco_{squaresX}x{squaresY}_{int(square_size_mm)}mm"
cv2.imwrite(f"{name}.png", img)

# Save PDF
Image.fromarray(img).save(
    f"{name}_A4.pdf",
    "PDF",
    resolution=dpi
)

print(f"saved board to {name}")
