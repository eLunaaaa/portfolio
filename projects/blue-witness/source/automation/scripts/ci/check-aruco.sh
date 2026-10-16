#!/usr/bin/env bash
set -euo pipefail
cd 'aruco_BLE_(IN_windowsPC)'
python3 -m compileall -q .
python3 check_setup.py
python3 -m unittest -v test_ble_protocol test_tracking_offline test_tracking_ble_bridge test_ble_sender
