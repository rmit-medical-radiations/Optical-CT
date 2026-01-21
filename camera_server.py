from flask import Flask, send_file, Response
import io
from picamera2 import Picamera2
import cv2
import numpy as np

# This server runs on the RPi Zero.

def block_average(img, out_h=512, out_w=512):
    h, w = img.shape
    bh = h // out_h
    bw = w // out_w
    img = img[:out_h*bh, :out_w*bw]
    return img.reshape(out_h, bh, out_w, bw).mean(axis=(1, 3))


app = Flask(__name__)

picam2 = Picamera2()
config = picam2.create_still_configuration(
    main={
        "format": "RGB888",
        "size": (512, 512),
    },
    buffer_count=2,
)
picam2.configure(config)
picam2.start()

picam2.set_controls({
    "AeEnable": False,
    "AwbEnable": False,

    # Tune exposure using a flat-field image
    "ExposureTime": 20000,     # example
    "AnalogueGain": 1.0,

    # Keep frame timing stable
    "FrameDurationLimits": (20000, 20000),

    # Minimise ISP alterations
    "Sharpness": 0.0,
    "Saturation": 0.0,
    "Contrast": 1.0,
})


@app.route("/")
def index():
    return Response("""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Live Capture</title>
    <style>
      body { font-family: sans-serif; margin: 16px; }
      img { max-width: 100%; height: auto; border: 1px solid #ccc; }
    </style>
  </head>
  <body>
    <h3>Live Capture</h3>
    <img id="frame" src="/capture" />
    <script>
      const img = document.getElementById('frame');
      const intervalMs = 500; // adjust (e.g. 100–1000)
      function refresh() {
        // cache-buster to force a new fetch each time
        img.src = "/capture?t=" + Date.now();
      }
      setInterval(refresh, intervalMs);
    </script>
  </body>
</html>
""", mimetype="text/html")


@app.route('/capture', methods=["GET"])
def capture():
    # Capture ISP-processed frame directly to memory
    stream = io.BytesIO()
    picam2.capture_file(stream, format="png")
    stream.seek(0)

    # Decode PNG to numpy array
    img_array = np.frombuffer(stream.getvalue(), dtype=np.uint8)
    image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if image is None:
        return "Decode failed", 500

    # Convert to grayscale (use luminance)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Optional: normalise for display (NOT OD)
    gray = gray.astype(np.float32)
    gray -= gray.min()
    gray /= (gray.max() + 1e-6)
    gray_8 = (gray * 255).astype(np.uint8)

    # Encode back to PNG
    success, png = cv2.imencode(".png", gray_8)
    if not success:
        return "Encode failed", 500

    out = io.BytesIO(png.tobytes())
    resp = send_file(out, mimetype="image/png")

    # Strongly discourage caching
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


if __name__ == '__main__':
    # threaded=True helps if multiple browser requests overlap
    app.run(host='0.0.0.0', port=8000, threaded=True)
