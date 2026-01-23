from picamera2 import Picamera2
import numpy as np

# Instantiate the camera
picam2 = Picamera2()

# Define the desired raw format and size based on 'libcamera-hello --list-cameras' output
# Example values for an IMX477 sensor:
raw_format = 'SBGGR10_CSI2P'
raw_size = (1332, 990) 

# Create the configuration, explicitly defining the raw stream
# A 'main' stream is often also required for general use or preview
config = picam2.create_still_configuration(
    raw={"format": raw_format, "size": raw_size}
)

# Configure the camera
picam2.configure(config)

# Start the camera
picam2.start()

# Optional: Capture a raw frame and view it as a numpy array
# Unpacked raw formats are stored as 16-bit values, using the bottom bits
raw_array = picam2.capture_array("raw").view(np.uint16) 

print(f"Captured raw array shape: {raw_array.shape}")
print(f"Captured raw array data type: {raw_array.dtype}")

# Stop the camera
picam2.stop()
