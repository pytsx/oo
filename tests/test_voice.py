import numpy as np

from news_machine.perception.voice import RATE, VoiceClusterer


def test_voice_clusterer_keeps_similar_audio_and_splits_distinct_spectrum():
    time = np.arange(RATE) / RATE
    low = np.sin(2 * np.pi * 220 * time).astype(np.float32)
    high = np.sin(2 * np.pi * 880 * time).astype(np.float32)
    clusterer = VoiceClusterer()
    assert clusterer.assign(low) == "speaker_01"
    assert clusterer.assign(low) == "speaker_01"
    assert clusterer.assign(high) == "speaker_02"
