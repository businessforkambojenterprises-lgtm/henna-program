"""Run Henna Studio on port 8766 for side-by-side testing (the main page keeps 8765)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webui_server as base  # noqa: E402

base.PORT = 8766
base.ALLOWED_HOSTS = {"127.0.0.1:8766", "localhost:8766"}
import webui_server_v12 as studio  # noqa: E402

studio.main()
