"""Transcrição incremental de vídeos e lives, com contexto e confirmação estável.

Instalação: pip install faster-whisper yt-dlp numpy imageio-ffmpeg
Uso: python transcriber_v2.py "https://www.youtube.com/watch?v=..." --language pt
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import yt_dlp
from faster_whisper import WhisperModel
from news_machine.perception.voice import VoiceClusterer

RATE = 16000


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class Transcriber:
    def __init__(self, args, event_sink=None):
        self.args = args
        self.event_sink = event_sink
        self.stop = threading.Event()
        self.chunks = queue.Queue(maxsize=32)
        self.errors = queue.Queue()
        self.process = None
        self.captured = 0
        self.confirmed_until = 0.0
        self.results = []
        self.started = time.monotonic()
        self.voice_clusterer = VoiceClusterer() if args.voice_clustering else None
        print(f"Carregando Whisper ({args.model}, {args.device})...", flush=True)
        self.model = WhisperModel(
            args.model,
            device=args.device,
            compute_type="float16" if args.device == "cuda" else "int8",
        )

    def stream_url(self):
        with yt_dlp.YoutubeDL({
            "format": "bestaudio/best", "quiet": True, "no_warnings": True,
            "noplaylist": True,
        }) as ydl:
            info = ydl.extract_info(self.args.url, download=False)
            if info is None:
                raise RuntimeError("Não foi possível extrair o vídeo")
            return info["url"]

    def capture(self):
        audio_file = None
        try:
            if self.args.record_audio:
                audio_path = Path(self.args.record_audio)
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_file = wave.open(str(audio_path), "wb")
                audio_file.setnchannels(1); audio_file.setsampwidth(2); audio_file.setframerate(RATE)
            url = self.stream_url()
            cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
            if not self.args.fast:
                cmd += ["-re"]  # arquivos/VOD na velocidade aproximada de reprodução
            cmd += ["-i", url, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None, bufsize=0)
            block_bytes = RATE * 2  # 1 segundo de PCM mono int16
            while not self.stop.is_set():
                # read pode devolver menos bytes; acumular até um bloco inteiro
                data = bytearray()
                while len(data) < block_bytes and not self.stop.is_set():
                    piece = self.process.stdout.read(block_bytes - len(data))
                    if not piece:
                        break
                    data.extend(piece)
                if not data:
                    break
                data = data[:len(data) - len(data) % 2]
                if not data:
                    break
                if audio_file:
                    audio_file.writeframes(bytes(data))
                samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
                self.captured += len(samples)
                # Em caso de atraso, bloquear em vez de perder áudio silenciosamente.
                while not self.stop.is_set():
                    try:
                        self.chunks.put(samples, timeout=0.3)
                        break
                    except queue.Full:
                        continue
            if self.process.poll() not in (None, 0) and not self.stop.is_set():
                raise RuntimeError(f"FFmpeg terminou com código {self.process.returncode}")
        except Exception as exc:
            self.errors.put(exc)
        finally:
            if audio_file:
                audio_file.close()
            # Sinalizar EOF sem bloquear o encerramento
            while not self.stop.is_set():
                try:
                    self.chunks.put(None, timeout=0.3)
                    break
                except queue.Full:
                    continue

    def recognize(self, audio, window_start, window_end, final=False):
        if len(audio) < RATE // 2:
            return
        segments, _ = self.model.transcribe(
            audio, language=None if self.args.language == "auto" else self.args.language,
            beam_size=self.args.beam,
            vad_filter=True,
            condition_on_previous_text=False,
            hallucination_silence_threshold=None,
            word_timestamps=True,
        )
        # A borda direita é instável. Confirmar somente após receber contexto futuro.
        safe_end = window_end if final else max(0.0, window_end - self.args.holdback)
        candidates = []
        for s in segments:
            start, end = window_start + s.start, window_start + s.end
            content = s.text.strip()
            if not content or end > safe_end + 1e-3:
                continue
            if end <= self.confirmed_until + 0.08:
                continue
            # Se o começo está antes da última confirmação, há risco de repetir texto.
            # É mais seguro omitir este segmento do que confirmar uma duplicação.
            if start < self.confirmed_until - 0.12:
                continue
            speaker_id = "speaker_unknown"
            if self.voice_clusterer:
                left, right = max(0, int(s.start * RATE)), min(len(audio), int(s.end * RATE))
                speaker_id = self.voice_clusterer.assign(audio[left:right])
            words = [{"id": f"pending_w{index:03d}", "text": word.word.strip(),
                "start": round(window_start + word.start, 3), "end": round(window_start + word.end, 3),
                "confidence": getattr(word, "probability", None), "timing_source": "whisper"}
                for index, word in enumerate(s.words or []) if word.word.strip()]
            candidates.append((start, end, content, speaker_id, words))

        for start, end, content, speaker_id, words in candidates:
            event_id = f"speech_{int(start * 1000):010d}_{int(end * 1000):010d}"
            for index, word in enumerate(words):
                word["id"] = f"{event_id}_w{index:03d}"
            result = {
                "id": event_id,
                "start": round(start, 2), "end": round(end, 2),
                "text": content, "speaker_id": speaker_id,
                "words": words,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
            self.results.append(result)
            if self.event_sink:
                # A fila aplica backpressure; a analise ocorre em outro worker.
                self.event_sink.publish(result)
            self.confirmed_until = max(self.confirmed_until, end)
            print(f"[{stamp(start)}] {content}", flush=True)

    def run(self):
        thread = threading.Thread(target=self.capture, daemon=True)
        thread.start()
        # Manter apenas a janela necessária em RAM.
        blocks = deque()
        total_samples = 0
        next_step = self.args.window * RATE
        last_window_end = 0.0
        ended = False
        try:
            while not ended and not self.stop.is_set():
                try:
                    item = self.chunks.get(timeout=0.3)
                except queue.Empty:
                    continue
                if item is None:
                    ended = True
                    break
                blocks.append((total_samples, item))
                total_samples += len(item)
                if total_samples < next_step:
                    continue
                end = total_samples / RATE
                begin = max(0.0, end - self.args.window)
                audio = self.slice_audio(blocks, int(begin * RATE), total_samples)
                self.recognize(audio, begin, end)
                last_window_end = end
                next_step = total_samples + int(self.args.step * RATE)
                # Reter janela + contexto, mas não a gravação completa.
                minimum = max(0, total_samples - int((self.args.window + 2) * RATE))
                while blocks and blocks[0][0] + len(blocks[0][1]) < minimum:
                    blocks.popleft()
                delay = self.captured / RATE - self.confirmed_until
                if delay > self.args.window * 2:
                    print(f"[aviso] atraso de confirmação: {delay:.1f}s", file=sys.stderr)
            # Flush final: transcrever o restante, inclusive a borda que estava em espera.
            if not self.stop.is_set() and total_samples:
                end = total_samples / RATE
                begin = max(0.0, end - self.args.window)
                audio = self.slice_audio(blocks, int(begin * RATE), total_samples)
                self.recognize(audio, begin, end, final=True)
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
        finally:
            self.stop.set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            thread.join(timeout=4)
            self.save()
            if self.event_sink:
                self.event_sink.close()
            if not self.errors.empty():
                print(f"Erro na captura: {self.errors.get()}", file=sys.stderr)

    @staticmethod
    def slice_audio(blocks, start, end):
        pieces = []
        for offset, data in blocks:
            left = max(start, offset)
            right = min(end, offset + len(data))
            if right > left:
                pieces.append(data[left - offset:right - offset])
        return np.concatenate(pieces) if pieces else np.empty(0, np.float32)

    def save(self):
        base = Path(self.args.output)
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        base.with_suffix(".txt").write_text(
            "\n".join(f"[{stamp(r['start'])}] {r['text']}" for r in self.results) + "\n",
            encoding="utf-8",
        )
        print(f"Salvo: {base.with_suffix('.txt')} e {base.with_suffix('.json')}")


def main():
    parser = argparse.ArgumentParser(description="Transcrição incremental com Faster-Whisper")
    parser.add_argument("url", help="URL de vídeo ou transmissão")
    parser.add_argument("--model", default="small", help="small, medium, large-v3...")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--language", default="pt", help="pt, en, auto...")
    parser.add_argument("--window", type=float, default=8.0, help="janela de contexto em segundos")
    parser.add_argument("--step", type=float, default=2.0, help="intervalo entre inferências")
    parser.add_argument("--holdback", type=float, default=1.5, help="borda direita não confirmada")
    parser.add_argument("--beam", type=int, default=5)
    parser.add_argument("--output", default="transcription")
    parser.add_argument("--fast", action="store_true", help="processar VOD sem limitar velocidade")
    parser.add_argument("--analyze", action="store_true", help="enviar segmentos confirmados ao grafo de compreensao")
    parser.add_argument("--stream-id", help="identificador persistente da transmissao")
    parser.add_argument("--news-db", default="news_machine.db", help="banco SQLite do pipeline")
    parser.add_argument("--no-ollama", action="store_true", help="usar analisador deterministico")
    parser.add_argument("--record-audio", help="arquivo WAV para arquivar o áudio capturado")
    parser.add_argument("--voice-clustering", action=argparse.BooleanOptionalAction, default=True,
        help="agrupar trechos por assinatura acústica local (experimental)")
    args = parser.parse_args()
    if args.window <= 0 or args.step <= 0 or args.holdback < 0 or args.holdback >= args.window:
        parser.error("window e step devem ser positivos; holdback entre 0 e window")
    if args.step > args.window - args.holdback:
        parser.error("step deve ser <= window - holdback para evitar lacunas")
    event_sink = None
    if args.analyze:
        from news_machine.config import Settings
        from news_machine.services.runtime import EmbeddedNewsRuntime
        base = Settings()
        settings = Settings(args.news_db, base.ollama_base_url, base.ollama_model, not args.no_ollama)
        event_sink = EmbeddedNewsRuntime(settings, args.stream_id or args.url)
    Transcriber(args, event_sink).run()


if __name__ == "__main__":
    main()
