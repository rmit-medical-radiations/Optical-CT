import serial
import time

# Configure the serial port settings
ser = serial.Serial(
    # port='/dev/ttyUSB1',
    port='/dev/tty.usbserial-A9TKD8CR',
    baudrate=9600,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    xonxoff=False,
    rtscts=False,
    dsrdtr=False
)

time.sleep(2) # Wait for connection to establish

# Example: Send a command to set the position counter to zero (P=0)
# Commands must end with a carriage return (\r)
command = "P=0\r"
ser.write(command.encode('ascii'))

# Read response (optional, MDrive often sends sign-on or status messages)
response = ser.readline().decode('ascii')
print(f"MDrive Response: {response}")

ser.close()
