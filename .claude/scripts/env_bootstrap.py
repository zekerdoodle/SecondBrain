import os
import sys
import subprocess
import venv
import platform

# Configuration
VENV_NAME = "gemini_google_tools"
REQUIREMENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")

def get_venv_dir():
    """
    Determines the OS-specific path for the virtual environment.
    """
    system = platform.system()
    
    if system == "Windows":
        base_dir = os.path.join(os.environ.get("LOCALAPPDATA"), "Gemini", "venvs")
    else:
        # Linux / MacOS
        base_dir = os.path.join(os.path.expanduser("~"), ".gemini", "venvs")
    
    return os.path.join(base_dir, VENV_NAME)

def get_python_executable(venv_dir):
    """
    Returns the path to the python executable within the venv.
    """
    system = platform.system()
    if system == "Windows":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        return os.path.join(venv_dir, "bin", "python")

def ensure_venv():
    """
    Checks for venv, creates if missing, installs requirements.
    Returns the path to the python executable.
    """
    venv_dir = get_venv_dir()
    python_exe = get_python_executable(venv_dir)
    
    # 1. Create Venv if missing
    if not os.path.exists(python_exe):
        print(f"[BOOTSTRAP] Creating venv at: {venv_dir}")
        os.makedirs(os.path.dirname(venv_dir), exist_ok=True)
        venv.create(venv_dir, with_pip=True)
    
    # 2. Install Requirements (Check via a sentinel file or just pip install -q)
    # We'll rely on pip's internal caching to make this fast if already installed
    print(f"[BOOTSTRAP] Ensuring requirements from {REQUIREMENTS_FILE}...")
    try:
        subprocess.check_call([python_exe, "-m", "pip", "install", "-q", "-r", REQUIREMENTS_FILE])
    except subprocess.CalledProcessError as e:
        print(f"[BOOTSTRAP] Error installing requirements: {e}")
        # Proceeding anyway as it might be a transient net issue and packages might exist
        
    return python_exe

if __name__ == "__main__":
    # When run directly, just print the python path so other scripts can capture it
    print(ensure_venv())
