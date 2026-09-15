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
import re
import time


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
        return ollama.Client(host=os.environ.get("CODECHAT_OLLAMA_URL", "http://127.0.0.1:11434")).embeddings(model=model, prompt=prompt)
    except Exception as first_error:
        if not _is_cuda_failure(first_error):
            raise
        # Ollama accepts num_gpu in model options. CPU fallback lets CodeChat
        # recover from a broken/overloaded CUDA runner without killing the app.
        try:
            return ollama.Client(host=os.environ.get("CODECHAT_OLLAMA_URL", "http://127.0.0.1:11434")).embeddings(model=model, prompt=prompt, options={"num_gpu": 0})
        except Exception as cpu_error:
            raise RuntimeError(_friendly_ollama_error(cpu_error, "Embedding")) from cpu_error

def _ollama_chat_with_fallback(model, messages, options=None):
    """Try normal GPU inference first, then retry the same request on CPU."""
    base_options = dict(options or {})
    try:
        return ollama.Client(host=os.environ.get("CODECHAT_OLLAMA_URL", "http://127.0.0.1:11434")).chat(model=model, messages=messages, options=base_options, stream=False)
    except Exception as first_error:
        if not _is_cuda_failure(first_error):
            raise
        cpu_options = dict(base_options)
        cpu_options["num_gpu"] = 0
        try:
            return ollama.Client(host=os.environ.get("CODECHAT_OLLAMA_URL", "http://127.0.0.1:11434")).chat(model=model, messages=messages, options=cpu_options, stream=False)
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
        # Ordered unique source manifest. This is the authoritative file order:
        # the last entry is the most recently uploaded/replaced file.
        self.source_order = []
        self.local_history = [] 
        self.model = "llama3.1"
        self.embedding_model = "nomic-embed-text"
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
        if not hasattr(self, "source_order") or not isinstance(getattr(self, "source_order", None), list):
            seen = []
            for src in getattr(self, "sources", []) or []:
                if src not in seen:
                    seen.append(src)
            self.source_order = seen

    def _read_file(self, file_path):
        """Safely extract text from common formats; never decode arbitrary binary as UTF-8."""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            name = os.path.basename(file_path)
            text_exts = {'.py','.js','.ts','.jsx','.tsx','.c','.cc','.cpp','.h','.hpp','.java','.kt','.kts','.cs','.go','.rs','.rb','.php','.swift','.m','.mm','.sh','.bash','.zsh','.ps1','.sql','.html','.htm','.css','.scss','.sass','.xml','.yaml','.yml','.toml','.ini','.cfg','.conf','.env','.md','.markdown','.txt','.json','.jsonl','.csv','.tsv','.log','.tex','.rst','.graphql','.proto'}
            content = None
            if ext in text_exts or ext == '':
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            elif ext == '.pdf':
                from PyPDF2 import PdfReader
                content = '\n\n'.join((pg.extract_text() or '') for pg in PdfReader(file_path).pages)
            elif ext == '.docx':
                from docx import Document
                doc=Document(file_path); parts=[p.text for p in doc.paragraphs]
                parts += [' | '.join(c.text for c in row.cells) for table in doc.tables for row in table.rows]
                content='\n'.join(parts)
            elif ext in ('.xlsx','.xlsm'):
                from openpyxl import load_workbook
                wb=load_workbook(file_path,read_only=True,data_only=True); parts=[]
                for ws in wb.worksheets:
                    parts.append(f'[SHEET: {ws.title}]')
                    for row in ws.iter_rows(values_only=True):
                        vals=[str(v) for v in row if v is not None]
                        if vals: parts.append(' | '.join(vals))
                content='\n'.join(parts)
            elif ext == '.pptx':
                from pptx import Presentation
                prs=Presentation(file_path); parts=[]
                for n,slide in enumerate(prs.slides,1):
                    parts.append(f'[SLIDE {n}]')
                    for shape in slide.shapes:
                        if hasattr(shape,'text') and shape.text.strip(): parts.append(shape.text)
                content='\n'.join(parts)
            else:
                raw=open(file_path,'rb').read(5_000_000)
                printable=re.findall(rb'[ -~]{4,}',raw)
                strings='\n'.join(x.decode('ascii','ignore') for x in printable[:2000])
                content=f'[FILE METADATA]\nFilename: {name}\nExtension: {ext or "[none]"}\nSize: {os.path.getsize(file_path)} bytes'
                if strings.strip(): content += '\n\n[PRINTABLE STRINGS]\n' + strings
            content=str(content or '').strip()
            if not content: content=f'[FILE METADATA]\nFilename: {name}\nNo extractable text content found.'
            if HAS_LANGCHAIN:
                if ext in {'.py','.js','.ts'}:
                    lang_map={'.py':Language.PYTHON,'.js':Language.JS,'.ts':Language.TS}
                    splitter=RecursiveCharacterTextSplitter.from_language(language=lang_map[ext],chunk_size=1000,chunk_overlap=100)
                else:
                    splitter=RecursiveCharacterTextSplitter(chunk_size=1000,chunk_overlap=100)
                return [(d.page_content,file_path) for d in splitter.create_documents([content])]
            return [(content,file_path)]
        except Exception as e:
            return [(f'[FILE METADATA]\nFilename: {os.path.basename(file_path)}\nExtraction failed safely: {e}',file_path)]

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

        files = list(paths)
        if not files:
            return "No files selected."

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
                resp = _ollama_embeddings_with_fallback(self.embedding_model, chunk)
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
            incoming_sources = []
            for src in new_sources:
                if src not in incoming_sources:
                    incoming_sources.append(src)

            if replace:
                # SINGLE: the incoming upload is the complete Brain.
                self.chunks = list(new_chunks)
                self.sources = list(new_sources)
                self.embeddings = new_np
                self.source_order = list(incoming_sources)
                self.local_history = []
            else:
                # APPEND: treat a source as one logical file. If the same file is
                # uploaded again, replace its old chunks rather than creating two
                # competing versions of the same file. New files are appended in
                # upload order, making source_order[-1] unambiguous.
                remove_sources = set(incoming_sources)
                keep = [i for i, src in enumerate(self.sources) if src not in remove_sources]
                if keep:
                    kept_chunks = [self.chunks[i] for i in keep]
                    kept_sources = [self.sources[i] for i in keep]
                    kept_emb = np.asarray(self.embeddings, dtype=np.float32)[keep]
                    self.chunks = kept_chunks + list(new_chunks)
                    self.sources = kept_sources + list(new_sources)
                    self.embeddings = np.concatenate((kept_emb, new_np), axis=0)
                else:
                    self.chunks = list(new_chunks)
                    self.sources = list(new_sources)
                    self.embeddings = new_np

                existing_order = [src for src in self.source_order if src not in remove_sources]
                self.source_order = existing_order + incoming_sources
                self.local_history = []

        msg = f"Success: Indexed {len(new_vecs)} chunks from {len(set(new_sources))} file{'s' if len(set(new_sources)) != 1 else ''}."
        if failures:
            msg += f" Skipped {failures} failed chunks."
        return msg

    def retain_latest_file(self):
        """Single-mode invariant: retain only the most recently uploaded logical file."""
        with self._lock:
            self._ensure_runtime_state()
            if not self.sources or len(self.sources) != len(self.chunks):
                return "No Brain data to reduce."
            latest = self.source_order[-1] if self.source_order else self.sources[-1]
            keep = [i for i, src in enumerate(self.sources) if src == latest]
            if not keep:
                return "No latest file found."
            self.chunks = [self.chunks[i] for i in keep]
            self.sources = [self.sources[i] for i in keep]
            self.embeddings = np.asarray(self.embeddings, dtype=np.float32)[keep]
            self.source_order = [latest]
            # Do NOT clear local_history here. Mode changes are not session
            # boundaries; only a fresh app launch starts a fresh AI session.
            return f"Retained latest file: {os.path.basename(latest)}"

    def context_summary(self):
        self._ensure_runtime_state()
        with self._lock:
            files = []
            seen = set()
            for src in getattr(self, "source_order", []) or self.sources:
                if src in self.sources and src not in seen:
                    seen.add(src)
                    files.append(str(src))
            return {
                "brain_type": "local",
                "mode": getattr(self, "team_mode", "single"),
                "file_count": len(files),
                "files": files[-50:],
                "file_details": [
                    {"source": src, "filename": os.path.basename(src),
                     "chunks": sum(1 for x in self.sources if x == src)}
                    for src in files
                ],
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
        files_data = []
        for full in paths:
            try:
                extracted = self._read_file(full)
                rel = os.path.basename(full)
                for text, _src in extracted:
                    if str(text).strip():
                        files_data.append({"text": str(text), "source": f"HostUpload/{rel}"})
            except Exception as e:
                callback_fn(f"⚠️ Skipped {os.path.basename(full)}: {e}")

        if not files_data:
            # Keep collaborator uploads aligned with the Host upload path. If a
            # format has no directly extractable text, send safe file metadata
            # rather than failing the upload as invalid content.
            for full in files:
                try:
                    name = os.path.basename(full)
                    size = os.path.getsize(full)
                    ext = os.path.splitext(full)[1].lower() or "[none]"
                    files_data.append({
                        "text": (
                            "[FILE METADATA]\\n"
                            f"Filename: {name}\\n"
                            f"Extension: {ext}\\n"
                            f"Size: {size} bytes\\n"
                            "No directly extractable text content was found."
                        ),
                        "source": f"RemoteUpload/{name}"
                    })
                except Exception:
                    pass

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
                        pickle.dump({'chunks': list(self.chunks), 'sources': list(self.sources), 'source_order': list(self.source_order), 'embedding_model': self.embedding_model}, f)
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
                snapshot_embedding_model = d.get('embedding_model', 'llama3.1')
                source_order = d.get('source_order', [])
                if len(chunks) != len(sources) or len(chunks) != len(emb):
                    return "Invalid Brain file: chunks and embeddings are inconsistent."
                with self._lock:
                    self.chunks = chunks; self.sources = sources; self.embeddings = np.asarray(emb, dtype=np.float32)
                    self.embedding_model = snapshot_embedding_model
                    seen = []
                    for src in source_order or sources:
                        if src in sources and src not in seen:
                            seen.append(src)
                    for src in sources:
                        if src not in seen:
                            seen.append(src)
                    self.source_order = seen
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
        # Snapshot all mutable Brain data before any Ollama call.  Querying must
        # never hold the Brain lock while inference is running; otherwise a slow
        # embedding/chat call can stall Team uploads and server-side coordination.
        with self._lock:
            chunks = list(self.chunks)
            sources = list(self.sources)
            matrix = np.asarray(self.embeddings, dtype=np.float32).copy()
            source_order = list(getattr(self, "source_order", []) or [])
            workspace_context = dict(getattr(self, "workspace_context", {}) or {})
            team_mode = getattr(self, "team_mode", "single")

        if not chunks or matrix.size == 0:
            return "❌ Brain is empty. Please load code on Host and click Sync.", []

        active_history = history if history is not None else self.local_history

        try:
            q_vec = np.asarray(_ollama_embeddings_with_fallback(self.embedding_model, query)['embedding'], dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] != len(chunks) or matrix.shape[1] != q_vec.shape[0]:
                return (
                    "⚠️ This Brain was created with a different embedding model and cannot be queried safely in this session. "
                    "Re-index the files or load a compatible Brain.", []
                )
            # True cosine similarity avoids scale-dependent retrieval errors.
            q_norm = np.linalg.norm(q_vec)
            row_norms = np.linalg.norm(matrix, axis=1)
            if q_norm == 0:
                return "⚠️ Could not understand the query embedding.", []
            sims = np.dot(matrix, q_vec) / np.maximum(row_norms * q_norm, 1e-12)

            # Source selection is file-first, not chunk-first. This prevents one
            # highly similar chunk from one file from causing a second unrelated
            # file to enter the context.
            q_lower = query.lower()
            explicit = []
            file_selector = re.search(r"@file\s+([^\n]+)", query, flags=re.IGNORECASE)
            if file_selector:
                wanted = file_selector.group(1).strip().strip("`\"")
                for src in dict.fromkeys(sources):
                    if wanted.lower() in {str(src).lower(), os.path.basename(str(src)).lower()}:
                        explicit.append(src)
                if not explicit:
                    return f"⚠️ I could not find the requested file `{wanted}` in the Team Brain.", []
            for src in dict.fromkeys(sources):
                base = os.path.basename(str(src))
                stem = os.path.splitext(base)[0]
                if len(base) >= 5 and base.lower() in q_lower:
                    explicit.append(src)
                elif len(stem) >= 4 and re.search(r"(?<![\w])" + re.escape(stem.lower()) + r"(?![\w])", q_lower):
                    explicit.append(src)

            source_scores = {}
            for src in dict.fromkeys(sources):
                idxs = [i for i, x in enumerate(sources) if x == src]
                vals = sorted((float(sims[i]) for i in idxs), reverse=True)
                # Best chunk dominates, but the second-best chunk provides a
                # small confidence bonus so files with one accidental match lose.
                source_scores[src] = vals[0] + (0.12 * vals[1] if len(vals) > 1 else 0.0)

            if explicit:
                # Never silently combine two contributors' files that share the
                # same filename. If the user says only `main.py` and there are
                # multiple logical owners, force an unambiguous question instead.
                by_name = {}
                for src in explicit:
                    by_name.setdefault(os.path.basename(str(src)).lower(), []).append(src)
                ambiguous = [vals for vals in by_name.values() if len(vals) > 1]
                if ambiguous:
                    names = ", ".join(ambiguous[0])
                    return f"⚠️ `{os.path.basename(str(ambiguous[0][0]))}` exists in multiple team files ({names}). Please specify the contributor/path, e.g. `@file {names.split(', ')[0]}`.", []
                candidate_sources = explicit
            else:
                ordered_sources = sorted(source_scores, key=source_scores.get, reverse=True)
                # Normally answer from the strongest file; only bring in a second
                # file when its evidence is close enough to the first.
                candidate_sources = ordered_sources[:2]
                if candidate_sources and source_scores[candidate_sources[0]] < 0.24:
                    return "⚠️ I couldn't find sufficiently relevant evidence in the Team Brain for that question. Please name the file if you want a file-specific answer.", []
                if len(candidate_sources) == 2 and source_scores[candidate_sources[1]] < max(0.24, source_scores[candidate_sources[0]] * 0.72):
                    candidate_sources = candidate_sources[:1]

            candidate = [i for i, src in enumerate(sources) if src in candidate_sources]
            ranked = sorted(candidate, key=lambda i: float(sims[i]), reverse=True)

            selected = []
            per_source = {}
            for i in ranked:
                src = sources[i]
                limit = 8 if explicit else 5
                if per_source.get(src, 0) >= limit:
                    continue
                # Do not feed very weak chunks to the model.
                if float(sims[i]) < 0.20 and selected:
                    continue
                selected.append(i)
                per_source[src] = per_source.get(src, 0) + 1
                if len(selected) >= (10 if explicit else 8):
                    break

            if not selected:
                return "⚠️ I couldn't find relevant content in the Team Brain for that question.", []

            # Label every chunk with its real source. The model is explicitly
            # instructed not to merge facts across files unless the context
            # supports that relationship.
            ctx_parts = []
            for i in selected:
                ctx_parts.append(f"[SOURCE: {sources[i]} | FILE: {os.path.basename(str(sources[i]))}]\n{self.chunks[i]}")
            ctx = "\n\n---\n\n".join(ctx_parts)
            srcs = list(dict.fromkeys(str(sources[i]) for i in selected))
        except Exception as e: return _friendly_ollama_error(e, "Retrieval"), []

        info = {
            "file_count": len(source_order),
            "chunk_count": len(chunks),
            "files": [str(x) for x in source_order if x in set(sources)],
            "team": workspace_context.get("team", {}),
        }
        if not info["files"]:
            info["files"] = list(dict.fromkeys(str(x) for x in sources))
        info["file_count"] = len(info["files"])
        info["chunk_count"] = len(chunks)
        file_list = ", ".join(info["files"]) if info["files"] else "No indexed files"
        system_msg = (
            "You are CodeChat AI, the AI assistant inside CodeChat Pro Team Edition. "
            "You know that you are working inside a code/document workspace. "
            "Use ONLY the provided retrieved Context and workspace metadata. "
            "Never invent facts that are not supported by the Context. "
            "Treat each [SOURCE: filename] section as belonging to that file. Do not merge or attribute code/content from one file to another. "
            "If the question asks about a specific file and the Context does not contain enough evidence from that file, say that you do not have enough information instead of guessing. "
            "When useful, explicitly name the source file supporting your answer. "
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
                # Ensure the locally generated Host answer reaches the public
                # Team Stream before a polling cycle can replace the local view.
                host_log_url = "http://localhost:8000/host_log"
                payload = {"query": query, "answer": ans}
                for _ in range(3):
                    try:
                        log_res = requests.post(
                            host_log_url,
                            json=payload,
                            headers={"x-access-token": os.environ.get("CODECHAT_HOST_TOKEN", "")},
                            timeout=2.0
                        )
                        if log_res.status_code == 200:
                            break
                    except Exception:
                        pass
            
            return ans, srcs
        except Exception as e: return _friendly_ollama_error(e, "AI response"), []


def run_isolated_ai_query(payload):
    """Run one Team AI query in an isolated worker process.

    The worker receives only a snapshot of the authoritative Brain state, so a
    stalled Ollama call cannot monopolize the FastAPI control-plane process.
    """
    brain = CoreBrain.__new__(CoreBrain)
    brain.chunks = list(payload.get("chunks", []))
    brain.sources = list(payload.get("sources", []))
    brain.embeddings = np.asarray(payload.get("embeddings", []), dtype=np.float32)
    brain.source_order = list(payload.get("source_order", []))
    brain.local_history = list(payload.get("history", []))
    brain.model = payload.get("model", "llama3.1")
    brain.embedding_model = payload.get("embedding_model", "nomic-embed-text")
    brain.team_mode = payload.get("team_mode", "single")
    brain.workspace_context = dict(payload.get("workspace_context", {}) or {})
    brain._lock = threading.RLock()
    return brain.ask_question(payload.get("query", ""), history=brain.local_history, is_public=False)

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

    def get_chat_notifications(self):
        try:
            res = requests.get(f"{self.url}/chat/notifications", headers=self.headers, timeout=3)
            return res.json() if res.status_code == 200 else {"unread": {}, "notifications": []}
        except Exception:
            return {"unread": {}, "notifications": []}

    def mark_chat_read(self, conversation_id, message_id=None):
        try:
            res = requests.post(f"{self.url}/chat/read", json={"conversation_id": conversation_id, "message_id": message_id}, headers=self.headers, timeout=3)
            return res.json() if res.status_code == 200 else {"error": self._error_from_response(res)}
        except Exception as e:
            return {"error": str(e)}

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
        files = list(paths)
        if not files:
            return "No files selected."

        callback_fn(f"📤 Preparing {len(files)} file{'s' if len(files) != 1 else ''}...")
        files_data = []
        for full in files:
            try:
                extracted = self._read_file(full)
                rel = os.path.basename(full)
                for text, _src in extracted:
                    if str(text).strip():
                        files_data.append({"text": str(text), "source": f"RemoteUpload/{rel}"})
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
