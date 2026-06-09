"""py2app build script for Sisoul.app menu-bar tray.

Usage:
    cd tools/menubar
    /Users/as/sisoul-dev/.venv/bin/python setup.py py2app
    open dist/Sisoul.app
"""
from setuptools import setup

APP = ["sisoul_tray.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    # LSUIElement=1 → menu-bar only app (no dock icon)
    "plist": {
        "CFBundleName": "Sisoul",
        "CFBundleDisplayName": "Sisoul Tray",
        "CFBundleIdentifier": "com.sisoul.tray",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "Apache-2.0 sisoul maintainers",
        "NSHighResolutionCapable": True,
    },
    "packages": ["rumps"],
    "includes": [
        "json",
        "logging",
        "subprocess",
        "shutil",
        "signal",
        "threading",
        "webbrowser",
        "urllib.request",
        "urllib.error",
        "httpx",
        "httpcore",
        "h11",
        "certifi",
        "idna",
        "sniffio",
        "anyio",
        "objc",
        "Foundation",
        "AppKit",
        "PyObjCTools",
    ],
    # 不要 site-packages 全量打入 (venv 里 PyInstaller / numpy / trio 等会污染)
    "site_packages": False,
    "excludes": [
        "PyInstaller",
        "numpy",
        "PIL",
        "pandas",
        "scipy",
        "matplotlib",
        "pytest",
        "ipython",
        "jupyter",
        "trio",
        "PySide2",
        "PyQt5",
        "tkinter",
        "test",
        "tests",
        "_pytest",
    ],
    "optimize": 1,
    # iconfile 可选; 没有就用文字 "S"
    # "iconfile": "Sisoul.icns",
}

setup(
    app=APP,
    name="Sisoul",
    version="0.1.0",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
