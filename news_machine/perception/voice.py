import numpy as np

RATE = 16000


class VoiceClusterer:
    """Agrupamento acústico incremental; não identifica pessoas biometricamente."""

    def __init__(self, threshold: float = 0.90):
        self.threshold = threshold
        self.centroids: list[np.ndarray] = []
        self.counts: list[int] = []

    @staticmethod
    def embedding(samples: np.ndarray):
        if len(samples) < RATE // 3:
            return None
        samples = samples.astype(np.float32)
        samples = samples - samples.mean()
        peak = np.max(np.abs(samples))
        if peak < 1e-4:
            return None
        samples /= peak
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        frequencies = np.fft.rfftfreq(len(samples), 1 / RATE)
        edges = np.geomspace(80, 4000, 25)
        bands = np.array([np.log1p(spectrum[(frequencies >= left) & (frequencies < right)].mean())
            for left, right in zip(edges[:-1], edges[1:])], dtype=np.float32)
        bands -= bands.mean()
        norm = np.linalg.norm(bands)
        return bands / norm if norm else None

    def assign(self, samples: np.ndarray) -> str:
        vector = self.embedding(samples)
        if vector is None:
            return "speaker_unknown"
        scores = [float(np.dot(vector, centroid)) for centroid in self.centroids]
        if scores and max(scores) >= self.threshold:
            index = int(np.argmax(scores))
            count = self.counts[index]
            centroid = (self.centroids[index] * count + vector) / (count + 1)
            self.centroids[index] = centroid / max(np.linalg.norm(centroid), 1e-8)
            self.counts[index] += 1
        else:
            index = len(self.centroids)
            self.centroids.append(vector)
            self.counts.append(1)
        return f"speaker_{index + 1:02d}"
