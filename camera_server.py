from flask import Flask, send_file
import time
import io
from picamera2 import Picamera2

RESOLUTION=(1024,1024)

app = Flask(__name__)

picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration({"size": RESOLUTION}, buffer_count=1))
picam2.start()

@app.route('/capture', methods=["GET"])
def capture():
    stream = io.BytesIO()
    picam2.capture_file(stream, format='png')
    stream.seek(0)
    return send_file(stream, mimetype='image/png')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
