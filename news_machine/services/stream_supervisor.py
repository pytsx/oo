import re
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from news_machine.config import Settings


class StreamSupervisor:
    """Inicia e acompanha transcritores sem invocar shell."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.script = Path(__file__).resolve().parents[2] / "__main__.py"
        self.media_dir = Path(settings.media_dir).resolve()
        self.media_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_url(url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Informe uma URL HTTP ou HTTPS válida")
        return url.strip()

    @staticmethod
    def normalize_stream_id(value: str | None) -> str:
        if value:
            normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-.")
            if normalized:
                return normalized[:80]
        return f"stream-{datetime.now().strftime('%Y%m%d-%H%M')}-{uuid4().hex[:6]}"

    def start(self, *, url: str, stream_id: str | None, model: str, language: str,
              device: str, use_ollama: bool) -> dict:
        url, stream_id = self.validate_url(url), self.normalize_stream_id(stream_id)
        with self.lock:
            if any(job["stream_id"] == stream_id and job["process"].poll() is None for job in self.jobs.values()):
                raise ValueError("Já existe uma coleta ativa com esse identificador")
        job_id = f"job_{uuid4().hex}"
        command = [sys.executable, str(self.script), url, "--analyze", "--stream-id", stream_id,
            "--news-db", self.settings.db_path, "--model", model, "--language", language, "--device", device,
            "--record-audio", str(self.media_dir / f"{stream_id}.wav"),
            "--output", str(self.media_dir / f"{stream_id}-transcription")]
        if not use_ollama:
            command.append("--no-ollama")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=self.script.parent, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creationflags)
        job = {"id": job_id, "stream_id": stream_id, "url": url, "model": model,
            "language": language, "device": device, "started_at": datetime.now(timezone.utc),
            "process": process, "logs": deque(maxlen=100), "stop_requested": False,
            "stopped_at": None}
        with self.lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._drain, args=(job,), name=f"log-{job_id}", daemon=True).start()
        return self.serialize(job)

    @staticmethod
    def _drain(job):
        if job["process"].stdout:
            for line in job["process"].stdout:
                job["logs"].append(line.rstrip())

    @staticmethod
    def serialize(job: dict) -> dict:
        code = job["process"].poll()
        if code is None:
            status = "stopping" if job["stop_requested"] else "running"
        elif job["stop_requested"]:
            status = "stopped"
        else:
            status = "finished" if code == 0 else "failed"
        return {key: job[key] for key in ("id", "stream_id", "url", "model", "language", "device", "started_at", "stopped_at")} | {
            "status": status,
            "exit_code": code, "logs": list(job["logs"])[-12:],
        }

    def list(self) -> list[dict]:
        with self.lock:
            return [self.serialize(job) for job in reversed(self.jobs.values())]

    def stop(self, stream_id: str) -> dict:
        with self.lock:
            matches = [job for job in self.jobs.values()
                if job["stream_id"] == stream_id and job["process"].poll() is None]
            if not matches:
                raise LookupError("Não existe coleta ativa para essa transmissão")
            job = matches[-1]
            job["stop_requested"] = True
        process = job["process"]
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            process.wait(timeout=8)
        except (subprocess.TimeoutExpired, OSError):
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
        job["stopped_at"] = datetime.now(timezone.utc)
        return self.serialize(job)

    def close(self):
        with self.lock:
            processes = [job["process"] for job in self.jobs.values() if job["process"].poll() is None]
        for process in processes:
            process.terminate()
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()
