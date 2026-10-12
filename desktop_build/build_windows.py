"""Build the portable app without editing the validated source project.

Run with the project Python environment and PyInstaller available on PYTHONPATH.
Each build requires a new empty output directory; existing data is never replaced.
"""
import argparse
import csv
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def verify_build_inputs(project):
    for relative in ('models/hand_landmarker.task', 'models/MODEL_INFO.txt',
                     'webui_server_v13.py', 'webui/studio_v13.html',
                     'henna_preview/grbl_scan_link.py', 'webui_server_v14.py',
                     'webui/studio_v14.html', 'henna_preview/grbl_program_link.py',
                     'webui_server_v15.py', 'henna_preview/grbl_program_link_v15.py',
                     'webui_server_v16.py', 'webui/studio_v16.html', 'webui_server_v17.py',
                     'webui/studio_v17.html', 'step23_design_hop_program.py', 'henna_preview/hop_path.py',
                     'henna_preview/program_reread_v2.py', 'webui_server_v18.py', 'webui/studio_v18.html',
                     'henna_preview/stroke_font.py', 'webui_server_v19.py', 'webui/studio_v19.html',
                     'henna_preview/font_strokes.py', 'webui_server_v20.py', 'webui/studio_v20.html',
                     'henna_preview/grbl_program_link_v20.py', 'webui_server_v21.py',
                     'henna_preview/grbl_program_link_v21.py', 'henna_preview/name_files_v21.py',
                     'webui_server_v22.py', 'henna_preview/grbl_program_link_v22.py'):
        if not (project / relative).is_file():
            raise ValueError('Required build input missing: ' + relative +
                             '. See models/MODEL_INFO.txt for the approved hand model.')
    info = (project / 'models/MODEL_INFO.txt').read_text(encoding='utf-8')
    match = re.search(r'SHA-256:\s*([0-9a-fA-F]{64})', info)
    actual = hashlib.sha256((project / 'models/hand_landmarker.task').read_bytes()).hexdigest()
    if match is None or actual != match.group(1).lower():
        raise ValueError('Hand model SHA-256 does not match models/MODEL_INFO.txt')


def prepare_app(project, destination):
    verify_build_inputs(project)
    destination.mkdir(parents=True, exist_ok=False)
    for path in project.glob('*.py'):
        copy_file(path, destination / path.name)
    for folder, extensions in {
        'henna_preview': {'.py'}, 'webui': {'.html', '.css', '.js', '.png', '.svg', '.ico'},
        'models': {'.task', '.txt'}, 'designs': {'.svg', '.png'},
    }.items():
        for path in (project / folder).rglob('*'):
            if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts:
                copy_file(path, destination / path.relative_to(project))
    for name in ('current_calibration.json', 'practice_params.json'):
        copy_file(project / name, destination / name)
    calibration = json.loads((project / 'current_calibration.json').read_text(encoding='utf-8'))
    for key in ('xy_calibration', 'z_calibration', 'depth_scale'):
        relative = Path(calibration[key])
        resolved = (project / relative).resolve()
        if relative.is_absolute() or not resolved.is_relative_to(project):
            raise ValueError('Expected calibration file inside project: ' + str(relative))
        copy_file(resolved, destination / relative)
    log_name = 'validation_log_S2026-10-05-A.csv'
    with (project / log_name).open(encoding='utf-8', newline='') as stream:
        header = next(csv.reader(stream))
    with (destination / log_name).open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream, lineterminator='\n').writerow(header)
    (destination / 'outputs' / 'webui').mkdir(parents=True)
    return calibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    project, output, work = (value.resolve() for value in (args.project, args.output, args.work))
    if output.exists():
        raise ValueError('Use a new output directory; refusing to replace ' + str(output))
    here = Path(__file__).resolve().parent
    work.mkdir(parents=True, exist_ok=True)
    app = work / 'source_snapshot'
    prepare_app(project, app)
    manifest = {
        'application': 'Henna Studio', 'studio_version': 22, 'package_version': '0.22.0.1',
        'platform': 'Windows x64', 'python': sys.version, 'motion_ready': False,
        'source_commit': subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_git_status': subprocess.check_output(['git', '-C', str(project), 'status', '--porcelain'], text=True),
        'packages': {name: metadata.version(name) for name in (
            'pyinstaller', 'pyinstaller-hooks-contrib', 'numpy', 'matplotlib', 'pillow',
            'mediapipe', 'opencv-contrib-python', 'pyrealsense2', 'pyserial', 'pywebview', 'pythonnet')},
        'app_files': {str(p.relative_to(app)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(app.rglob('*')) if p.is_file()},
        'excluded_history': 'Old recordings, jobs, reports and generated G-code are not shipped.',
    }
    env = dict(os.environ, HENNA_SOURCE_DIR=str(app), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm',
               '--distpath', str(work / 'dist'), '--workpath', str(work / 'pyinstaller'),
               str(here / 'HennaStudio.spec')]
    with (work / 'build.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(work / 'dist' / 'HennaStudio', output)
    shutil.copytree(app, output / 'app')
    # Respect the portable app's writable paths without copying any old test runs.
    (output / 'BUILD_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    copy_file(here / 'START_HERE.txt', output / 'START_HERE.txt')
    for name in ('henna_desktop.py', 'HennaStudio.spec', 'version_info.txt', 'build_windows.py', 'BUILDING.txt', 'START_HERE.txt',
                 'henna.ico'):
        copy_file(here / name, output / 'build_source' / name)
    # Include dependency license texts beside the bundle's own runtime metadata.
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        for relative in distribution.files or ():
            if '.dist-info/' in str(relative).replace('\\', '/') and any(
                token in relative.name.upper() for token in ('LICENSE', 'COPYING', 'NOTICE')):
                path = Path(distribution.locate_file(relative))
                if path.is_file():
                    copy_file(path, output / 'licenses' / name / relative.name)
    print(output / 'HennaStudio.exe', flush=True)


if __name__ == '__main__':
    main()
