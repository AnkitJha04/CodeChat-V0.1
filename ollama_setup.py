import os
import sys
import time
import socket
import subprocess
import requests
import urllib.request

OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
REQUIRED_MODEL = "llama3.1"
EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_PROCESS = None
OLLAMA_PORT = None


def get_ollama_url():
    return os.environ.get("CODECHAT_OLLAMA_URL", "http://127.0.0.1:11434")


def check_ollama_running():
    try:
        response = requests.get(f"{get_ollama_url()}/api/tags", timeout=2)
        return response.status_code == 200
    except requests.RequestException:
        return False


def get_ollama_version():
    try:
        response = requests.get(f"{get_ollama_url()}/api/version", timeout=2)
        if response.status_code == 200:
            return response.json().get("version")
    except Exception:
        pass
    return None


def get_installed_models():
    try:
        response = requests.get(f"{get_ollama_url()}/api/tags", timeout=5)
        if response.status_code != 200:
            return []
        return [m["name"] for m in response.json().get("models", [])]
    except Exception:
        return []


def model_installed(model_name):
    return any(m == model_name or m.startswith(model_name + ":") for m in get_installed_models())


def find_ollama_executable():
    possible_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
        r"C:\Program Files\Ollama\ollama.exe",
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe")
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return path
    return None


def download_ollama():
    installer_path = os.path.join(os.environ.get("TEMP", "."), "OllamaSetup.exe")
    urllib.request.urlretrieve(OLLAMA_INSTALLER_URL, installer_path)
    return installer_path


def install_ollama():
    subprocess.run([download_ollama()], check=True)
    return True


def _free_port():
    for port in range(11435, 11536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_ollama():
    global OLLAMA_PROCESS, OLLAMA_PORT
    ollama_path = find_ollama_executable()
    if not ollama_path:
        return False
    if OLLAMA_PROCESS is not None and OLLAMA_PROCESS.poll() is None and check_ollama_running():
        return True

    OLLAMA_PORT = _free_port()
    host = f"127.0.0.1:{OLLAMA_PORT}"
    url = f"http://{host}"
    env = os.environ.copy()
    env["OLLAMA_HOST"] = host
    env["CODECHAT_OLLAMA_URL"] = url
    env["OLLAMA_NUM_PARALLEL"] = "1"
    env["OLLAMA_MAX_LOADED_MODELS"] = "1"
    env["OLLAMA_KEEP_ALIVE"] = "0"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        OLLAMA_PROCESS = subprocess.Popen(
            [ollama_path, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags, env=env
        )
        os.environ.update({"OLLAMA_HOST": host, "CODECHAT_OLLAMA_URL": url})
        return True
    except Exception:
        OLLAMA_PROCESS = None
        return False


def wait_for_ollama(timeout=45):
    start = time.time()
    while time.time() - start < timeout:
        if check_ollama_running():
            return True
        if OLLAMA_PROCESS is not None and OLLAMA_PROCESS.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def pull_model(model_name):
    ollama_path = find_ollama_executable()
    if not ollama_path:
        return False
    env = os.environ.copy()
    env["OLLAMA_HOST"] = os.environ.get("OLLAMA_HOST", f"127.0.0.1:{OLLAMA_PORT}")
    try:
        subprocess.run([ollama_path, "pull", model_name], check=True, env=env)
        return True
    except subprocess.CalledProcessError:
        return False


def setup_ollama():
    print("=" * 55)
    print("CodeChat - ISOLATED Ollama Session")
    print("=" * 55)

    # Never attach CodeChat to an already-running global Ollama daemon.
    # Every CodeChat launch gets a fresh Ollama server process on its own port.
    if not find_ollama_executable():
        try:
            install_ollama()
        except Exception as e:
            print(f"Ollama installation failed: {e}")
            return False

    if not start_ollama() or not wait_for_ollama():
        print("Ollama failed to start an isolated session.")
        return False

    print(f"✓ Isolated Ollama: {get_ollama_url()}")
    for model in (REQUIRED_MODEL, EMBEDDING_MODEL):
        if not model_installed(model):
            print(f"Downloading model: {model}")
            if not pull_model(model):
                print(f"Failed to download {model}.")
                stop_ollama()
                return False
        print(f"✓ {model} ready")

    print("✓ Ollama session ready; no previous chat context is reused.")
    return True


def stop_ollama():
    global OLLAMA_PROCESS, OLLAMA_PORT
    proc = OLLAMA_PROCESS
    OLLAMA_PROCESS = None
    OLLAMA_PORT = None
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
