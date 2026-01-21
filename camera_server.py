from flask import Flask, send_file, Response
import io
from picamera2 import Picamera2
import cv2

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
    raw={
        "format": "SRGGB10",
        "size": (2028, 1520)
    },
    buffer_count=1
)
picam2.configure(config)
picam2.start()

# disable all auto behaviour and lock exposure
picam2.set_controls({
    "AeEnable": False,
    "AwbEnable": False,

    # Choose these based on flat-field (no clipping)
    "ExposureTime": 20000,     # microseconds (example)
    "AnalogueGain": 1.0,

    # Keep timing stable (important for LED arrays)
    "FrameDurationLimits": (20000, 20000),
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
      const intervalMs = 200; // adjust (e.g. 100–1000)
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
    # Capture RAW frame
    request = picam2.capture_request()
    raw = request.make_array("raw")
    request.release()

    # SRGGB12 assumed:
    # R G
    # G B
    G1 = raw[0::2, 1::2]
    G2 = raw[1::2, 0::2]
    green = 0.5 * (G1 + G2)

    # center crop to square
    h, w = green.shape
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    green_sq = green[y0:y0+side, x0:x0+side]

    # then average to 512×512
    green_512 = block_average(green_sq, 512, 512)

    # Normalize for display (not OD!)
    green_512 -= green_512.min()
    green_512 /= green_512.max() + 1e-6
    green_512_8 = (green_512 * 255).astype(np.uint8)

    # Encode as PNG
    success, png = cv2.imencode(".png", green_512_8)
    if not success:
        return "Encode failed", 500

    stream = io.BytesIO(png.tobytes())
    resp = send_file(stream, mimetype="image/png")

    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


if __name__ == '__main__':
    # threaded=True helps if multiple browser requests overlap
    app.run(host='0.0.0.0', port=8000, threaded=True)
