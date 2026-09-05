"""Build the Windows GUI executable using the existing title-bar cat icon."""

from pathlib import Path

import PyInstaller.__main__


def main():
    root = Path(__file__).resolve().parent
    arguments = [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "BeatKeys",
        "--distpath", str(root / "dist"),
        "--workpath", str(root / "build"),
        "--specpath", str(root / "build"),
        "--icon", str(root / "assets" / "beatkeys-icon.png"),
        "--add-data", f"{root / 'assets' / 'beatkeys-icon.png'};assets",
        "--add-data", f"{root / 'assets' / 'about-cat.png'};assets",
    ]
    # Old recognition experiments share this venv but are not runtime dependencies.
    for module in ("torch", "transformers", "scipy", "matplotlib", "pandas"):
        arguments.extend(["--exclude-module", module])
    arguments.append(str(root / "gui.py"))
    PyInstaller.__main__.run(arguments)


if __name__ == "__main__":
    main()
