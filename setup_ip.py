#!/usr/bin/env python3

import subprocess
import re
import sys

# Settings
PI_IP = "192.168.7.2"
HOST_IP = "192.168.7.1"
SUBNET = "192.168.7.0/24"

def run(cmd, capture_output=True):
    result = subprocess.run(cmd, shell=True, capture_output=capture_output, text=True)
    if result.returncode != 0 and not capture_output:
        print(f"[ERROR] Command failed: {cmd}")
    return result.stdout.strip()

def find_usb_interface():
    output = run("ip link show")
    pattern = re.compile(r"\d+: (\S+):.*?link/ether ([0-9a-f:]{17})", re.MULTILINE)

    for match in pattern.finditer(output):
        iface, mac = match.groups()
        if iface.startswith("enx") or iface.startswith("usb"):
            # Optional: exclude Wi-Fi adapters (common for built-in chips)
            if "wlan" not in iface and "eth" not in iface:
                return iface
    return None

def configure_interface(iface):
    print(f"[INFO] Found USB gadget interface: {iface}")

    # Flush existing IPs
    print("[INFO] Flushing existing IPs...")
    run(f"sudo ip addr flush dev {iface}")

    # Assign IP
    print(f"[INFO] Assigning {HOST_IP}/24 to {iface}...")
    run(f"sudo ip addr add {HOST_IP}/24 dev {iface}")

    # Bring interface up
    print("[INFO] Bringing interface up...")
    run(f"sudo ip link set {iface} up")

    # Add route (if needed)
    routes = run("ip route")
    if SUBNET not in routes:
        print("[INFO] Adding route...")
        run(f"sudo ip route add {SUBNET} dev {iface}")
    else:
        print("[INFO] Route already exists.")

    print(f"Ready to ping Pi at {PI_IP}!")

def main():
    iface = find_usb_interface()
    if not iface:
        print("Could not find USB gadget interface. Is the Pi connected and powered?")
        sys.exit(1)
    configure_interface(iface)

if __name__ == "__main__":
    main()