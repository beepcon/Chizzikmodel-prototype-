# =============================================================================
# 참조 음성 만들기
#
# 긴 음성 파일에서 GPT-SoVITS 참조로 쓸 3~10초 구간을 뽑아낸다.
#
# 참조 음성이 나쁘면 목소리가 통째로 무너지므로 다음을 자동으로 처리한다.
#   - Whisper 타임스탬프는 앞뒤에 침묵을 포함하는 경우가 많다 -> 에너지 기준으로 잘라낸다
#   - 실제 발화 길이가 3~10초를 벗어나면 후보에서 제외한다
#   - 32kHz 모노로 통일한다 (96kHz 원본을 그대로 넣으면 피치가 틀어진다)
#   - 잘라낸 파일을 다시 전사해 대사를 확정한다 (구간이 밀리면 대사가 어긋난다)
# =============================================================================

import numpy as np


def _to_mono(data):
    return data.mean(axis=1) if data.ndim > 1 else data


def _resample(data, src_sr, dst_sr):
    if src_sr == dst_sr:
        return data
    from scipy.signal import resample_poly
    g = np.gcd(int(src_sr), int(dst_sr))
    return resample_poly(data, dst_sr // g, src_sr // g)


def _trim_silence(clip, sr, ratio=0.08):
    """앞뒤 무음을 잘라낸다. 실제 발화 길이를 알아야 3~10초 판정이 맞는다."""
    win = max(1, int(sr * 0.02))
    n = len(clip) // win
    if n < 2:
        return clip
    rms = np.array([np.sqrt(np.mean(clip[i * win:(i + 1) * win] ** 2)) for i in range(n)])
    if rms.max() <= 0:
        return clip
    voiced = np.where(rms > rms.max() * ratio)[0]
    if len(voiced) == 0:
        return clip
    return clip[voiced[0] * win: min(len(clip), (voiced[-1] + 1) * win)]


def find_candidates(model, wav_path, on_progress=None,
                    min_sec=3.0, max_sec=10.0, min_chars=10):
    """음성 파일을 전사해 참조로 쓸 만한 구간을 찾는다.

    반환: [{start, end, dur, text}] — dur 은 무음을 뺀 실제 발화 길이
    """
    import soundfile as sf

    info = sf.info(str(wav_path))
    total = info.duration

    data, sr = sf.read(str(wav_path))
    data = _to_mono(data)

    segments, _ = model.transcribe(str(wav_path), language="ko")

    out = []
    for seg in segments:
        if on_progress:
            on_progress(min(1.0, seg.end / total) if total else 0.0)

        text = seg.text.strip()
        if len(text) < min_chars:
            continue

        clip = data[int(seg.start * sr): int(seg.end * sr)]
        clip = _trim_silence(clip, sr)
        dur = len(clip) / sr
        if not (min_sec <= dur <= max_sec):
            continue

        out.append({"start": seg.start, "end": seg.end, "dur": dur, "text": text})

    if on_progress:
        on_progress(1.0)
    return out


def extract(model, wav_path, start, end, out_path, target_sr=32000):
    """구간을 잘라 저장하고, 저장된 파일을 다시 전사해 대사를 확정한다.

    반환: (실제 길이, 대사)
    """
    import soundfile as sf

    data, sr = sf.read(str(wav_path))
    data = _to_mono(data)

    clip = _trim_silence(data[int(start * sr): int(end * sr)], sr)
    clip = _resample(clip, sr, target_sr)

    peak = np.abs(clip).max()
    if peak > 0.98:                      # 클리핑이 있으면 살짝 낮춘다
        clip = clip * (0.95 / peak)

    sf.write(str(out_path), clip.astype(np.float32), target_sr, subtype="PCM_16")

    # 저장된 파일을 다시 전사한다.
    # 구간이 밀려 옆 문장이 섞이면 대사가 어긋나고, 대사가 틀리면 발음이 무너진다.
    segs, _ = model.transcribe(str(out_path), language="ko")
    text = " ".join(s.text.strip() for s in segs).strip()

    return len(clip) / target_sr, text
