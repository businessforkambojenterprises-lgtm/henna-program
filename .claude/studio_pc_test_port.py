"""Run Studio PC (home + Admin) on port 8766 for page checks while the operator's Studio keeps 8765. The Admin PIN
is a throw-away one in a temporary folder - never the PC's real outputs/admin_pin.json."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import webui_server as base  # noqa: E402

base.PORT = 8766
base.ALLOWED_HOSTS = {"127.0.0.1:8766", "localhost:8766"}
import webui_server_pc as pc  # noqa: E402
from henna_preview.admin_lock import AdminLock  # noqa: E402

pc.ADMIN = AdminLock(os.path.join(tempfile.mkdtemp(prefix="pc_admin_test_"), "admin_pin.json"))
pc.studio.main()
