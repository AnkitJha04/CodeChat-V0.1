import os
import ollama
import numpy as np
import pickle
import zipfile
import concurrent.futures
import requests
import json
import tempfile
import threading


def _is_cuda_failure(exc):
    """Return True for Ollama GPU/CUDA runner failures that are often recoverable by CPU fallback."""
    text = str(exc).lower()
    return any(token in text for token in (
        "cuda error", "cuda", "llama runner process has terminated",
        "out of memory", "cublas", "gpu memory"
    ))

def _friendly_ollama_error(exc, operation="AI"):
    """Turn noisy Ollama runner failures into a useful, non-crashing CodeChat message."""
    text = str(exc).strip()
    if _is_cuda_failure(exc):
        return (
            "⚠️ CodeChat AI is temporarily unavailable because Ollama's GPU/CUDA runner failed.\n\n"
            "CodeChat kept Team Chat running normally and automatically attempted a CPU fallback. "
            "If the problem continues, close other GPU-heavy apps or restart Ollama/CodeChat.\n\n"
            f"Technical detail: {text}"
        )
    if "connection" in text.lower() or "11434" in text:
        return (
            "⚠️ Ollama is not responding right now.\n\n"
            "Please make sure Ollama is running, then try again. "
            "Your Team Chat remains available.\n\n"
            f"Technical detail: {text}"
        )
    return f"⚠️ {operation} failed safely: {text}"

def _ollama_embeddings_with_fallback(model, prompt):
    """Try normal GPU inference first, then retry on CPU for CUDA runner failures."""
    try:
        return ollama.embeddings(model=model, prompt=prompt)
    except Exception as first_error:
        if not _is_cuda_failure(first_error):
            raise
        # Ollama accepts num_gpu in model options. CPU fallback lets CodeChat
        # recover from a broken/overloaded CUDA runner without killing the app.
        try:
            return ollama.embeddings(model=model, prompt=prompt, options={"num_gpu": 0})
        except Exception as cpu_error:
            raise RuntimeError(_friendly_ollama_error(cpu_error, "Embedding")) from cpu_error

def _ollama_chat_with_fallback(model, messages, options=None):
    """Try normal GPU inference first, then retry the same request on CPU."""
    base_options = dict(options or {})
    try:
        return ollama.chat(model=model, messages=messages, options=base_options, stream=False)
    except Exception as first_error:
        if not _is_cuda_failure(first_error):
            raise
        cpu_options = dict(base_options)
        cpu_options["num_gpu"] = 0
        try:
            return ollama.chat(model=model, messages=messages, options=cpu_options, stream=False)
        except Exception as cpu_error:
            raise RuntimeError(_friendly_ollama_error(cpu_error, "AI response")) from cpu_error

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False

class CoreBrain:
    def __init__(self):
        self.chunks = []       
        self.embeddings = []   
        self.sources = []      
        self.local_history = [] 
        self.model = "llama3.1" 
        self.embed_file = "temp_vectors.npy"
        self.meta_file = "temp_metadata.pkl"
        self._lock = threading.RLock()
        # Team/AI context is optional but must always exist for both local and remote brains.
        self.team_mode = "single"
        self.workspace_context = {}

    def _ensure_runtime_state(self):
        # Defensive compatibility for old/frozen CoreBrain instances.
        # Never assume attributes introduced by a newer build already exist.
        if not hasattr(self, "workspace_context") or not isinstance(getattr(self, "workspace_context", None), dict):
            self.workspace_context = {}
        if not hasattr(self, "team_mode"):
            self.team_mode = "single"
        if not hasattr(self, "local_history") or self.local_history is None:
            self.local_history = []

    def _read_file(self, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if not content.strip(): return []
            
            if HAS_LANGCHAIN:
                ext = os.path.splitext(file_path)[1]
                lang_map = {'.py': Language.PYTHON, '.js': Language.JS, '.ts': Language.TS}
                lang = lang_map.get(ext, Language.PYTHON)
                splitter = RecursiveCharacterTextSplitter.from_language(
                    language=lang, chunk_size=1000, chunk_overlap=100
                )
                docs = splitter.create_documents([content])
                return [(d.page_content, file_path) for d in docs]
            
            return [(content, file_path)]
        except: return []

    def ingest_codebase(self, paths, callback_fn, append_mode=False):
        """Index one file in Single mode or one/more selected files in Append mode.
        The Brain is modified only after successful embedding, so failed uploads
        never destroy a previously working Brain.
        """
        if isinstance(paths, (str, os.PathLike)):
            paths = [str(paths)]
        paths = [str(x) for x in (paths or []) if os.path.isfile(str(x))]
        if not paths:
            return "No valid file selected."

        valid = {'.py', '.js', '.ts', '.c', '.cpp', '.java', '.md', '.txt', '.json', '.rs', '.go'}
        files = [x for x in paths if os.path.splitext(x)[1].lower() in valid]
        if not files:
            return "No supported files selected."

        callback_fn(f"📖 Reading {len(files)} selected file{'s' if len(files) != 1 else ''}...")
        data = []
        for file_path in files:
            data.extend(self._read_file(file_path))

        return self._embed_data(data, callback_fn, replace=not append_mode)

    def ingest_remote_data(self, file_data_list, callback_fn, append_mode=True):
        return self._embed_data(
            file_data_list,
            callback_fn,
            replace=not append_mode
        )

    def _embed_data(self, data_tuples, callback_fn, replace=False):
        data_tuples = [(str(c), str(p)) for c, p in data_tuples if str(c).strip()]
        total = len(data_tuples)
        if total == 0:
            return "No valid content found."

        callback_fn(f"🧠 Embedding {total} chunks...")
        new_chunks, new_sources, new_vecs = [], [], []
        failures = 0

        for i, (chunk, path) in enumerate(data_tuples):
            try:
                resp = _ollama_embeddings_with_fallback(self.model, chunk)
                vec = resp.get('embedding')
                if not vec:
                    raise RuntimeError("Ollama returned an empty embedding")
                new_chunks.append(chunk)
                new_sources.append(path)
                new_vecs.append(vec)
                if i == 0 or (i + 1) % 5 == 0 or i + 1 == total:
                    callback_fn(f"⚡ Processing: {int(((i + 1) / total) * 100)}%")
            except Exception as e:
                failures += 1
                callback_fn(f"⚠️ Skipped chunk {i + 1}/{total}: {e}")

        if not new_vecs:
            return "Error: No chunks could be embedded. Check Ollama and the model."

        new_np = np.asarray(new_vecs, dtype=np.float32)
        with self._lock:
            if replace:
                self.chunks = list(new_chunks)
                self.sources = list(new_sources)
                self.embeddings = new_np
                self.local_history = []
            else:
                self.chunks.extend(new_chunks)
                self.sources.extend(new_sources)
                if len(self.embeddings) == 0:
                    self.embeddings = new_np
                else:
                    self.embeddings = np.concatenate(
                        (np.asarray(self.embeddings, dtype=np.float32), new_np),
                        axis=0
                    )

        msg = f"Success: Indexed {len(new_vecs)} chunks from {len(set(new_sources))} file{'s' if len(set(new_sources)) != 1 else ''}."
        if failures:
            msg += f" Skipped {failures} failed chunks."
        return msg

    def retain_latest_file(self):
        """Single-mode invariant: retain only the most recently ingested source file."""
        with self._lock:
            if not self.sources or len(self.sources) != len(self.chunks):
                return "No Brain data to reduce."
            latest = self.sources[-1]
            keep = [i for i, src in enumerate(self.sources) if src == latest]
            if not keep:
                return "No latest file found."
            self.chunks = [self.chunks[i] for i in keep]
            self.sources = [self.sources[i] for i in keep]
            self.embeddings = np.asarray(self.embeddings, dtype=np.float32)[keep]
            self.local_history = []
            return f"Retained latest file: {os.path.basename(latest)}"

    def context_summary(self):
        self._ensure_runtime_state()
        with self._lock:
            files = []
            seen = set()
            for src in self.sources:
                name = os.path.basename(str(src))
                if name not in seen:
                    seen.add(name)
                    files.append(name)
            return {
                "brain_type": "local",
                "mode": getattr(self, "team_mode", "single"),
                "file_count": len(files),
                "files": files[-50:],
                "chunk_count": len(self.chunks),
                "team": dict(getattr(self, "workspace_context", {}) or {}),
            }

    def publish_files_to_server(self, paths, callback_fn, append_mode=False):
        """Authoritatively update the Team Brain from the Host.
        The server performs the embedding and returns a snapshot; the Host then
        replaces its local Brain with that authoritative snapshot.
        """
        if isinstance(paths, (str, os.PathLike)):
            paths = [str(paths)]
        paths = [str(x) for x in (paths or []) if os.path.isfile(str(x))]
        valid = {'.py', '.js', '.ts', '.c', '.cpp', '.h', '.hpp', '.java', '.md', '.txt', '.json', '.rs', '.go'}
        files_data = []
        for full in paths:
            if os.path.splitext(full)[1].lower() not in valid:
                continue
            try:
                with open(full, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if content.strip():
                    files_data.append({"text": content, "source": f"HostUpload/{os.path.basename(full)}"})
            except Exception as e:
                callback_fn(f"⚠️ Skipped {os.path.basename(full)}: {e}")

        if not files_data:
            return "No valid file content found."

        callback_fn(f"📤 Publishing {len(files_data)} file{'s' if len(files_data) != 1 else ''} to Team Brain...")
        try:
            token = os.environ.get("CODECHAT_HOST_TOKEN", "")
            res = requests.post(
                "http://127.0.0.1:8000/ingest",
                json={"chunks": files_data, "append_mode": bool(append_mode)},
                headers={"x-access-token": token},
                timeout=180
            )
            if res.status_code != 200:
                return f"Server Error: {res.text}"

            callback_fn("📥 Refreshing authoritative Team Brain...")
            snap = requests.get(
                "http://127.0.0.1:8000/download_brain",
                headers={"x-access-token": token},
                timeout=60
            )
            if snap.status_code != 200:
                return f"Server Error while downloading Team Brain: {snap.text}"

            fd, temp_path = tempfile.mkstemp(prefix="codechat_host_refresh_", suffix=".brain")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(snap.content)
                load_res = self.load_snapshot(temp_path)
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            if "Success" not in load_res:
                return f"Could not refresh local Team Brain: {load_res}"
            return "Success: Team Brain updated and synchronized."
        except Exception as e:
            return f"Connection Error: {e}"

    def save_snapshot(self, filepath):
        try:
            with self._lock:
                if len(self.embeddings) == 0 or len(self.chunks) != len(self.embeddings):
                    return "Error: Brain is empty or inconsistent."
                target = os.path.abspath(filepath)
                os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="codechat_snapshot_") as td:
                    emb = os.path.join(td, "vectors.npy")
                    meta = os.path.join(td, "metadata.pkl")
                    np.save(emb, np.asarray(self.embeddings, dtype=np.float32))
                    with open(meta, 'wb') as f:
                        pickle.dump({'chunks': list(self.chunks), 'sources': list(self.sources)}, f)
                    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
                        zf.write(emb, "temp_vectors.npy")
                        zf.write(meta, "temp_metadata.pkl")
                return "Success"
        except Exception as e:
            return str(e)

    def load_snapshot(self, filepath):
        try:
            target = os.path.abspath(filepath)
            if not os.path.exists(target): return "File not found"
            with tempfile.TemporaryDirectory(prefix="codechat_load_") as td:
                with zipfile.ZipFile(target, 'r') as zf:
                    names = set(zf.namelist())
                    if 'temp_vectors.npy' not in names or 'temp_metadata.pkl' not in names:
                        return "Invalid Brain file."
                    zf.extract('temp_vectors.npy', td)
                    zf.extract('temp_metadata.pkl', td)
                emb = np.load(os.path.join(td, 'temp_vectors.npy'), allow_pickle=False)
                with open(os.path.join(td, 'temp_metadata.pkl'), 'rb') as f:
                    d = pickle.load(f)
                chunks = d.get('chunks', []); sources = d.get('sources', [])
                if len(chunks) != len(sources) or len(chunks) != len(emb):
                    return "Invalid Brain file: chunks and embeddings are inconsistent."
                with self._lock:
                    self.chunks = chunks; self.sources = sources; self.embeddings = np.asarray(emb, dtype=np.float32)
                return "Success"
        except Exception as e: return str(e)

    def get_team_chat(self):
        try:
            res = requests.get("http://localhost:8000/team_activity", headers={"x-access-token": os.environ.get("CODECHAT_HOST_TOKEN", "")}, timeout=0.5)
            if res.status_code == 200: return res.json()['history']
            return []
        except: return []

    def get_team_events(self):
        try:
            res = requests.get(
                "http://localhost:8000/team_activity",
                headers={"x-access-token": os.environ.get("CODECHAT_HOST_TOKEN", "")},
                timeout=0.5
            )
            if res.status_code == 200:
                return res.json().get("events", [])
            return []
        except Exception:
            return []

    def get_connected_users(self):
        try:
            res = requests.get("http://localhost:8000/active_users", headers={"x-access-token": os.environ.get("CODECHAT_HOST_TOKEN", "")}, timeout=0.5)
            if res.status_code == 200: return res.json()['users']
            return []
        except: return []

    def ask_question(self, query, history=None, is_public=False):
        self._ensure_runtime_state()
        if not self.chunks or len(self.embeddings) == 0: 
            return "❌ Brain is empty. Please load code on Host and click Sync.", []

        active_history = history if history is not None else self.local_history

        try:
            q_vec = np.array(_ollama_embeddings_with_fallback(self.model, query)['embedding'])
            sims = np.dot(self.embeddings, q_vec)
            top_idx = np.argsort(sims)[-5:][::-1]
            ctx = "\n\n".join([self.chunks[i] for i in top_idx])
            srcs = [self.sources[i] for i in top_idx]
        except Exception as e: return _friendly_ollama_error(e, "Retrieval"), []

        info = self.context_summary()
        file_list = ", ".join(info["files"]) if info["files"] else "No indexed files"
        system_msg = (
            "You are CodeChat AI, the AI assistant inside CodeChat Pro Team Edition. "
            "You know that you are working inside a code/document workspace. "
            "Use the provided Context to answer the user's technical question. "
            "You may answer questions about your workspace only from the workspace metadata below "
            "and retrieved context; never invent filenames, counts, roles, or team facts. "
            f"\n\nWorkspace metadata: {info['file_count']} indexed files, "
            f"{info['chunk_count']} chunks. Files: {file_list}. "
            f"Team context: {info.get('team', {})}. "
            f"\n\nContext:\n{ctx}"
        )

        msgs = [{'role': 'system', 'content': system_msg}]
        msgs.extend(active_history[-4:]) 
        msgs.append({'role': 'user', 'content': query})

        try:
            res = _ollama_chat_with_fallback(
                self.model,
                msgs,
                options={'num_ctx': 4096}
            )
            ans = res['message']['content']
            
            active_history.append({'role': 'user', 'content': query})
            active_history.append({'role': 'assistant', 'content': ans})
            
            if is_public:
                try: requests.post("http://localhost:8000/host_log", json={"query": query, "answer": ans}, timeout=0.5)
                except: pass
            
            return ans, srcs
        except Exception as e: return _friendly_ollama_error(e, "AI response"), []

class RemoteBrain:
    def __init__(self, url, token):
        self.url = url.rstrip('/')
        self.token = token
        self.chunks = []
        self.sources = []
        self.team_mode = "single"
        self.brain_ready = False
        self.brain_version = 0
        self.role = "guest"
        self.member_id = None
        self.name = "Team Member"
        self.handle = ""

    @property
    def headers(self):
        return {"x-access-token": self.token}

    def _error_from_response(self, res):
        try:
            detail = res.json().get("detail", res.text)
        except Exception:
            detail = res.text
        return f"❌ Server Error ({res.status_code}): {detail}"

    def ask_question(self, query, history=None, is_public=False):
        try:
            state = self.get_team_state()
            if state and not state.get("brain_ready", False):
                return "🧠 Waiting for the Host to load a Team Brain.", []
            res = requests.post(
                f"{self.url}/query",
                json={"text": query, "public": is_public},
                headers=self.headers,
                timeout=60
            )
            if res.status_code == 200:
                d = res.json()
                return d.get("answer", "Error"), d.get("sources", [])
            return self._error_from_response(res), []
        except Exception as e:
            return f"❌ Connection Error: {e}", []

    def get_team_chat(self):
        try:
            res = requests.get(
                f"{self.url}/team_activity",
                headers=self.headers,
                timeout=2
            )
            if res.status_code == 200:
                return res.json().get("history", [])
            return []
        except Exception:
            return []

    def get_team_events(self):
        try:
            res = requests.get(
                f"{self.url}/team_activity",
                headers=self.headers,
                timeout=2
            )
            if res.status_code == 200:
                return res.json().get("events", [])
            return []
        except Exception:
            return []

    def get_connected_users(self):
        try:
            res = requests.get(
                f"{self.url}/active_users",
                headers=self.headers,
                timeout=2
            )
            if res.status_code == 200:
                return res.json().get("users", [])
            return []
        except Exception:
            return []

    def get_team_state(self):
        try:
            res = requests.get(
                f"{self.url}/team_state",
                headers=self.headers,
                timeout=2
            )
            if res.status_code == 200:
                data = res.json()
                self.team_mode = data.get("mode", "single")
                self.brain_ready = data.get("brain_ready", False)
                self.brain_version = data.get("brain_version", 0)
                self.chunks = [None] * data.get("chunks", 0)
                self.role = data.get("role", self.role)
                self.member_id = data.get("member_id", self.member_id)
                self.name = data.get("name", self.name)
                self.handle = data.get("handle", self.handle)
                return data
            return {"auth_error": res.status_code in (401, 403), "status_code": res.status_code}
        except Exception:
            return None

    def get_chat_conversations(self):
        try:
            res = requests.get(
                f"{self.url}/chat/conversations",
                headers=self.headers,
                timeout=3
            )
            if res.status_code == 200:
                return res.json()
            return {"enabled": False, "conversations": []}
        except Exception:
            return {"enabled": False, "conversations": []}

    def get_chat_messages(self, conversation_id, after_id=None):
        try:
            params = {"conversation_id": conversation_id}
            if after_id:
                params["after_id"] = after_id
            res = requests.get(
                f"{self.url}/chat/messages",
                params=params,
                headers=self.headers,
                timeout=3
            )
            if res.status_code == 200:
                return res.json()
            return {"messages": []}
        except Exception:
            return {"messages": []}

    def delete_chat_conversation(self, conversation_id):
        try:
            res = requests.delete(
                f"{self.url}/chat/conversations/{conversation_id}",
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def send_chat_message(self, text, conversation_id=None):
        try:
            payload = {"text": text}
            if conversation_id:
                payload["conversation_id"] = conversation_id
            res = requests.post(
                f"{self.url}/chat/messages",
                json=payload,
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def create_group(self, name, member_ids=None):
        try:
            res = requests.post(
                f"{self.url}/chat/groups",
                json={"name": name, "member_ids": member_ids or []},
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def add_group_member(self, group_id, member_id):
        try:
            res = requests.post(
                f"{self.url}/chat/groups/{group_id}/members",
                json={"member_id": member_id},
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def remove_group_member(self, group_id, member_id):
        try:
            res = requests.delete(
                f"{self.url}/chat/groups/{group_id}/members/{member_id}",
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def leave_group(self, group_id):
        try:
            res = requests.post(
                f"{self.url}/chat/groups/{group_id}/leave",
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def delete_group(self, group_id):
        try:
            res = requests.delete(
                f"{self.url}/chat/groups/{group_id}",
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def toggle_chat(self, enabled):
        try:
            res = requests.post(
                f"{self.url}/chat/settings",
                json={"enabled": bool(enabled)},
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def remove_member(self, member_id):
        try:
            res = requests.post(
                f"{self.url}/members/remove",
                json={"member_id": member_id},
                headers=self.headers,
                timeout=5
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def leave_team(self):
        try:
            res = requests.post(
                f"{self.url}/leave",
                headers=self.headers,
                timeout=3
            )
            if res.status_code == 200:
                return res.json()
            return {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": f"❌ Connection Error: {e}"}

    def ingest_codebase(self, paths, callback_fn, append_mode=True):
        if isinstance(paths, (str, os.PathLike)):
            paths = [str(paths)]
        paths = [str(x) for x in (paths or []) if os.path.isfile(str(x))]
        valid = {'.py', '.js', '.ts', '.c', '.cpp', '.java', '.md', '.txt', '.json', '.rs', '.go'}
        files = [x for x in paths if os.path.splitext(x)[1].lower() in valid]
        if not files:
            return "No supported files selected."

        callback_fn(f"📤 Preparing {len(files)} file{'s' if len(files) != 1 else ''}...")
        files_data = []
        for full in files:
            try:
                with open(full, 'r', encoding='utf-8', errors='ignore') as fo:
                    content = fo.read()
                if content.strip():
                    rel = os.path.basename(full)
                    files_data.append({"text": content, "source": f"RemoteUpload/{rel}"})
            except Exception as e:
                callback_fn(f"⚠️ Skipped {os.path.basename(full)}: {e}")

        if not files_data:
            return "No valid file content found."

        callback_fn(f"🚀 Uploading {len(files_data)} file{'s' if len(files_data) != 1 else ''} as one atomic Team Brain update...")
        try:
            res = requests.post(
                f"{self.url}/ingest",
                json={"chunks": files_data, "append_mode": bool(append_mode)},
                headers=self.headers,
                timeout=180
            )
            if res.status_code != 200:
                return self._error_from_response(res)
            self.get_team_state()
            return "✅ All selected files uploaded successfully"
        except Exception as e:
            return f"❌ Connection Lost: {e}"

    def save_snapshot(self, filepath):
        try:
            res = requests.get(
                f"{self.url}/download_brain",
                headers=self.headers,
                stream=True,
                timeout=60
            )
            if res.status_code == 200:
                with open(filepath, 'wb') as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                return "Success"
            return self._error_from_response(res)
        except Exception as e:
            return f"Download Failed: {e}"

    def load_snapshot(self, *args):
        return "❌ Permission Denied: Only Host can load Brains."
