#!/usr/bin/env python3

import subprocess

INTERFACE = "usb0"
HOST_IP = "192.168.7.1"
PI_IP = "192.168.7.2"
SUBNET = "192.168.7.0/24"

def run(cmd):
    print(f"[cmd] {cmd}")
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"[ERROR] {result.stderr.strip()}")
    return result.stdout.strip()

def setup_usb_interface():
    print(f"[INFO] Configuring {INTERFACE}...")

    # Flush any existing IP addresses
    run(f"sudo ip addr flush dev {INTERFACE}")

    # Assign our IP
    run(f"sudo ip addr add {HOST_IP}/24 dev {INTERFACE}")

    # Bring interface up
    run(f"sudo ip link set {INTERFACE} up")

    # Add route if not present
    routes = run("ip route")
    if SUBNET not in routes:
        run(f"sudo ip route add {SUBNET} dev {INTERFACE}")
    else:
        print("[INFO] Route already exists.")

    # Test connectivity
    print("[INFO] Trying to ping the Raspberry Pi...")
    ping_result = run(f"ping -c 3 {PI_IP}")
    print(ping_result)

    print(f"USB network interface {INTERFACE} is ready.")

if __name__ == "__main__":
    setup_usb_interface()
    