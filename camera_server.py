from flask import Flask, send_file, Response
import io
from picamera2 import Picamera2

RESOLUTION = (1024, 1024)
app = Flask(__name__)

picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration({"size": RESOLUTION}, buffer_count=1))
picam2.start()

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
      const intervalMs = 300; // adjust (e.g. 100–1000)
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
    stream = io.BytesIO()
    picam2.capture_file(stream, format='png')
    stream.seek(0)
    resp = send_file(stream, mimetype='image/png')

    # Strongly discourage caching
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

if __name__ == '__main__':
    # threaded=True helps if multiple browser requests overlap
    app.run(host='0.0.0.0', port=8000, threaded=True)
    