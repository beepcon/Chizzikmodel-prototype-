# make_ref.py - 긴 음성에서 참조용 3~10초 구간 추출 + 대사(prompt_text) 생성
# 사용법: python make_ref.py "D:\ai_streamer\refs\ko_main_1_.wav"

import sys
import soundfile as sf
from faster_whisper import WhisperModel

INPUT = sys.argv[1]

# 1) 전사 (3090이면 large-v3도 여유. 느리면 "medium"으로)
model = WhisperModel("large-v3", device="cuda", compute_type="float16")
segments, info = model.transcribe(INPUT, language="ko")

# 2) 3~10초 사이 구간만 후보로 출력
print(f"\n=== 참조 후보 구간 (3~10초) ===")
candidates = []
for seg in segments:
    dur = seg.end - seg.start
    text = seg.text.strip()
    if 3.0 <= dur <= 10.0 and text:
        candidates.append((seg.start, seg.end, text))
        print(f"[{len(candidates)-1}] {seg.start:6.1f}s ~ {seg.end:6.1f}s ({dur:.1f}s) | {text}")

if not candidates:
    print("3~10초 구간이 없음. 아래 전체 목록에서 인접 구간을 확인하세요.")
    sys.exit()

# 3) 번호 선택 -> 잘라서 저장 (모노 변환 포함)
idx = int(input("\n사용할 구간 번호 입력: "))
start, end, text = candidates[idx]

data, sr = sf.read(INPUT)
clip = data[int(start * sr):int(end * sr)]
if clip.ndim > 1:
    clip = clip.mean(axis=1)  # 스테레오 -> 모노

out_path = INPUT.rsplit(".", 1)[0] + f"_ref.wav"
sf.write(out_path, clip, sr)

print(f"\n저장 완료: {out_path}")
print(f"prompt_text로 쓸 대사:\n{text}")