"""
arduino_flash.py
Compiles and uploads a sketch to the Arduino Nano using arduino-cli as a
subprocess.

Speed optimization: each sketch is compiled only ONCE and cached in its
own "build" subfolder. Subsequent uploads of the same (unchanged) sketch
skip compilation entirely and go straight to avrdude upload, which only
takes a few seconds instead of the 20-60+ seconds a full compile can take
on a Raspberry Pi 3.

Prerequisites on the Raspberry Pi (one-time setup, see setup_arduino_cli.md):
    - arduino-cli installed
    - core "arduino:avr" installed (`arduino-cli core install arduino:avr`)
"""

import os
import subprocess


DEFAULT_FQBN = "arduino:avr:nano:cpu=atmega328"


class FlashResult:
    def __init__(self, success: bool, message: str, log: str = ""):
        self.success = success
        self.message = message
        self.log = log


def _build_dir(sketch_dir: str) -> str:
    return os.path.join(sketch_dir, "build")


def is_compiled(sketch_dir: str) -> bool:
    bdir = _build_dir(sketch_dir)
    if not os.path.isdir(bdir):
        return False
    return any(f.endswith(".hex") for f in os.listdir(bdir))


def compile_sketch(sketch_dir: str, fqbn: str = DEFAULT_FQBN) -> FlashResult:
    bdir = _build_dir(sketch_dir)
    try:
        result = subprocess.run(
            ["arduino-cli", "compile", "--fqbn", fqbn, "--output-dir", bdir, sketch_dir],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            return FlashResult(False, "Compilation error", result.stdout + result.stderr)
        return FlashResult(True, "Compilation successful", result.stdout)
    except FileNotFoundError:
        return FlashResult(False, "arduino-cli not found. See setup_arduino_cli.md", "")
    except subprocess.TimeoutExpired:
        return FlashResult(False, "Timeout during compilation", "")


def upload_sketch(sketch_dir: str, port: str, fqbn: str = DEFAULT_FQBN) -> FlashResult:
    bdir = _build_dir(sketch_dir)
    try:
        result = subprocess.run(
            ["arduino-cli", "upload", "-p", port, "--fqbn", fqbn, "--input-dir", bdir, sketch_dir],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return FlashResult(False, "Upload error", result.stdout + result.stderr)
        return FlashResult(True, "Upload successful", result.stdout)
    except FileNotFoundError:
        return FlashResult(False, "arduino-cli not found. See setup_arduino_cli.md", "")
    except subprocess.TimeoutExpired:
        return FlashResult(False, "Timeout during upload", "")


def ensure_compiled(sketch_dir: str, fqbn: str = DEFAULT_FQBN) -> FlashResult:
    """Compiles only if not already compiled and cached."""
    if is_compiled(sketch_dir):
        return FlashResult(True, "Already compiled (cached)", "")
    return compile_sketch(sketch_dir, fqbn)


def flash_sketch(sketch_dir: str, port: str, fqbn: str = DEFAULT_FQBN) -> FlashResult:
    """Compiles (if needed) then uploads. Returns at the first error encountered."""
    compile_result = ensure_compiled(sketch_dir, fqbn)
    if not compile_result.success:
        return compile_result
    return upload_sketch(sketch_dir, port, fqbn)


def prewarm_all(sketch_dirs: dict, fqbn: str = DEFAULT_FQBN) -> dict:
    """
    Pre-compiles every sketch in sketch_dirs (test_key -> path), so that
    the first "Start" of each test only needs a quick upload instead of a
    full compile. Meant to be called once in a background thread right
    after the app starts.
    """
    results = {}
    for key, sdir in sketch_dirs.items():
        results[key] = ensure_compiled(sdir, fqbn)
    return results
