from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

source = Path(os.environ['HENNA_SOURCE_DIR']).resolve()
here = Path(SPECPATH)
project_modules = {p.stem for p in source.glob('*.py')}
project_modules.add('henna_preview')
hidden = sorted(project_modules)
hidden += ['henna_preview.' + p.stem for p in (source / 'henna_preview').glob('*.py') if p.stem != '__init__']
datas, binaries = [], []
for package in ('mediapipe', 'pyrealsense2'):
    package_data, package_bins, package_imports = collect_all(
        package, filter_submodules=lambda name: '.test' not in name)
    datas += package_data
    binaries += package_bins
    hidden += package_imports
for package in ('mediapipe', 'pyrealsense2', 'pyserial', 'opencv-contrib-python'):
    datas += copy_metadata(package)

a = Analysis(
    [str(here / 'henna_desktop.py')], pathex=[str(source)],
    binaries=binaries, datas=datas, hiddenimports=hidden,
    hookspath=[], runtime_hooks=[], excludes=['pytest', 'IPython'],
    hooksconfig={'matplotlib': {'backends': ['Agg']}},
    noarchive=False,
)
# Project source is shipped unchanged in the persistent app directory. Remove its
# frozen duplicates so __file__, relative calibration paths and outputs stay there.
a.pure = [entry for entry in a.pure if entry[0].split('.')[0] not in project_modules]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='HennaStudio',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
    version=str(here / 'version_info.txt'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='HennaStudio')
