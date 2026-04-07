services:
  - type: worker
    name: flight-monitor
    runtime: python
    buildCommand: pip install -r requirements.txt && playwright install chromium && playwright install-deps chromium
    startCommand: python flight_monitor.py