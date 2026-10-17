"""The install stick (raspberry_pi/make_install_stick.sh + install_from_stick.sh): valid bash, a Pi 5 / Pi 4 menu, the
servo signal line for each model, the safety checks before anything is installed, and the same OS as the stick."""
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAKE = os.path.join(ROOT, "raspberry_pi", "make_install_stick.sh")
INSTALL = os.path.join(ROOT, "raspberry_pi", "install_from_stick.sh")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class StickScriptTests(unittest.TestCase):
    @unittest.skipIf(shutil.which("bash") is None, "no bash here")
    def test_valid_bash(self):
        for p in (MAKE, INSTALL):
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_menu_and_servo_signal_per_model(self):
        s = read(INSTALL)
        self.assertIn("1) Raspberry Pi 5", s)
        self.assertIn("2) Raspberry Pi 4", s)
        self.assertIn('"dtoverlay=pwm-pio,gpio=18"', s)                 # Pi 5
        self.assertIn('"dtoverlay=pwm,pin=18,func=2"', s)               # Pi 4
        self.assertIn('sudo cp "$CONFIG" "$CONFIG.before-henna"', s)    # config.txt backed up first

    def test_checks_come_before_any_change(self):
        s = read(INSTALL)
        first_change = s.index('sudo apt-get "${APTOPT[@]}" update')
        for check in ("sha256sum -c --quiet SHA256SUMS", 'val os_codename', 'val python', '[ -e "$REPO" ] && stop',
                      "grep -qw aes /proc/cpuinfo"):
            self.assertLess(s.index(check), first_change, check)

    def test_stick_has_what_the_installer_needs(self):
        m, i = read(MAKE), read(INSTALL)
        for item in ("henna-program.bundle", "lib/librealsense.tar.gz", "requirements.lock", "target.txt",
                     "models/hand_landmarker.task", "SHA256SUMS", "99-realsense-libusb.rules"):
            self.assertIn(item.split("/")[-1], m, item)
            self.assertIn(item.split("/")[-1], i, item)
        self.assertIn('cp "$HERE/install_from_stick.sh" "$DEST/install.sh"', m)
        self.assertIn("--no-index --find-links", i)                     # Python packages only from the stick
        self.assertIn('deb [trusted=yes] file:$DEBS ./', i)            # system packages only from the stick's copy
        self.assertIn('sudo chmod -R a+rX "$DEBS"', i)                # readable by apt's own user _apt
        self.assertIn("Dir::Etc::SourceParts=/dev/null", i)            # ... and not from the internet
        self.assertNotIn("sudo apt-get update" + chr(10), i)           # no plain internet update
        self.assertIn("dpkg-scanpackages", m)
        self.assertIn("wanted.txt", m)
        self.assertIn("git clone --branch raspberry-pi", i)


if __name__ == "__main__":
    unittest.main()
