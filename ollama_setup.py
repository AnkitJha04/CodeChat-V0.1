import os
import sys
import time
import subprocess
import requests
import urllib.request


OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"

REQUIRED_MODEL = "llama3.1"


def check_ollama_running():
    """
    Check whether Ollama API is running.
    """

    try:
        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=2
        )

        return response.status_code == 200

    except requests.RequestException:
        return False


def get_ollama_version():
    """
    Get Ollama version from the local Ollama server.
    """

    try:
        response = requests.get(
            f"{OLLAMA_URL}/api/version",
            timeout=2
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("version")

    except Exception:
        pass

    return None


def get_installed_models():
    """
    Return list of models installed in Ollama.
    """

    try:
        response = requests.get(
            f"{OLLAMA_URL}/api/tags",
            timeout=5
        )

        if response.status_code != 200:
            return []

        data = response.json()

        return [
            model["name"]
            for model in data.get("models", [])
        ]

    except Exception:
        return []


def model_installed(model_name):
    """
    Check whether required model is installed.
    """

    models = get_installed_models()

    for model in models:

        if model.startswith(model_name):
            return True

    return False


def find_ollama_executable():

    possible_paths = [

        os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
        ),

        r"C:\Program Files\Ollama\ollama.exe",

        os.path.expandvars(
            r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe"
        )
    ]

    for path in possible_paths:

        if os.path.exists(path):
            return path

    return None


def download_ollama():

    installer_path = os.path.join(
        os.environ.get("TEMP", "."),
        "OllamaSetup.exe"
    )

    print("Downloading Ollama...")

    urllib.request.urlretrieve(
        OLLAMA_INSTALLER_URL,
        installer_path
    )

    return installer_path


def install_ollama():

    installer = download_ollama()

    print("Installing Ollama...")

    subprocess.run(
        [installer],
        check=True
    )

    return True


def start_ollama():

    ollama_path = find_ollama_executable()

    if not ollama_path:
        return False

    try:

        subprocess.Popen(
            [ollama_path, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        return True

    except Exception:

        return False


def wait_for_ollama(timeout=30):

    start_time = time.time()

    while time.time() - start_time < timeout:

        if check_ollama_running():
            return True

        time.sleep(1)

    return False


def pull_model(model_name=REQUIRED_MODEL):

    ollama_path = find_ollama_executable()

    if not ollama_path:
        return False

    print(f"Downloading model: {model_name}")

    try:

        subprocess.run(
            [
                ollama_path,
                "pull",
                model_name
            ],
            check=True
        )

        return True

    except subprocess.CalledProcessError:

        return False


def setup_ollama():

    print("=" * 50)
    print("CodeChat - Ollama System Check")
    print("=" * 50)

    # ------------------------------------------------
    # 1. Check if Ollama API already works
    # ------------------------------------------------

    if check_ollama_running():

        print("✓ Ollama is running")

    else:

        print("Ollama is not running.")

        # --------------------------------------------
        # 2. Check whether executable exists
        # --------------------------------------------

        ollama_path = find_ollama_executable()

        if not ollama_path:

            print("Ollama is not installed.")
            print("Downloading Ollama...")

            try:

                install_ollama()

            except Exception as e:

                print("Ollama installation failed:")
                print(e)

                return False

        # --------------------------------------------
        # 3. Start Ollama
        # --------------------------------------------

        print("Starting Ollama...")

        start_ollama()

        # --------------------------------------------
        # 4. Wait for Ollama API
        # --------------------------------------------

        if not wait_for_ollama():

            print("Ollama failed to start.")

            return False

        print("✓ Ollama is running")

    # ------------------------------------------------
    # 5. Check version
    # ------------------------------------------------

    version = get_ollama_version()

    if version:

        print(f"✓ Ollama version: {version}")

    else:

        print("⚠ Could not determine Ollama version")

    # ------------------------------------------------
    # 6. Check model
    # ------------------------------------------------

    if model_installed(REQUIRED_MODEL):

        print(f"✓ {REQUIRED_MODEL} is installed")

    else:

        print(f"{REQUIRED_MODEL} is missing.")
        print("Downloading model...")

        if not pull_model(REQUIRED_MODEL):

            print("Failed to download model.")

            return False

        print(f"✓ {REQUIRED_MODEL} installed")

    print("=" * 50)
    print("Ollama setup complete.")
    print("=" * 50)

    return True