"""Step 23 with the travel clearance over the nozzle's path - see step_path_clearance.py (Studio v29, 2026-10-18).
Called by Studio v29 in place of the approved step 23 script, which runs unchanged inside."""
import sys

from step_path_clearance import main

if __name__ == "__main__":
    sys.exit(main(["step23"] + sys.argv[1:]))
