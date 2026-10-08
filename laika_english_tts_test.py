# =============================================================================
# B / C 시험 - 영어 단어를 어떻게 넘겨야 제대로 읽는가  (귀로 판정)
#
# 같은 문장을 세 가지로 바꿔 GPT-SoVITS 에 넘기고 wav 로 남긴다.
#   now  지금 정규화 그대로       -> 영어를 한 글자씩 (에이치아이비...)
#   B    대문자 약어(2~5자)만 스펠링, 나머지 영단어는 그대로 넘김
#        -> GPT-SoVITS 가 text_lang=ko 에서 영단어를 어떻게 읽는지 확인
#   C    사전식 한글 표기로 바꿔 넘김 (tts_dict.json 에 넣을 후보)
#
# 방송 코드(laika_tts_norm.py)는 건드리지 않는다. 후보 규칙은 이 파일 안에만 있다.
# 합성은 mode 0(논스트리밍)으로 한다. 읽는 방식만 비교하기 위해서다.
#
# 실행 (GPT-SoVITS 서버가 떠 있어야 한다. 방송이 말하는 중이면 요청이 겹치므로 끄고 돌린다)
#   conda deactivate
#   conda activate laika
#   python laika_english_tts_test.py
#
# 결과: tts_samples_eng/e{번호}_{now|B|C}.wav / laika_english_tts_test.json / .png
# =============================================================================

import io
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "tts_samples_eng"

# (원문, C 에 쓸 한글 표기) - C 는 사람이 정한 후보다
CASES = [
    ("곰은 원래 좀 hibernation 하잖아요.", {"hibernation": "하이버네이션"}),
    ("오늘 game 한 판 할까요?", {"game": "게임"}),
    ("Master Duel 랭크전 시작해요!", {"Master Duel": "마스터 듀얼"}),
    ("그거 완전 OK 예요, ATK 2500 이면 충분해요.", {}),
    ("진짜 nice 하네요, good job!", {"nice": "나이스", "good job": "굿 잡"}),
]


def candidate_B(text, norm):
    """대문자 약어만 기존 규칙으로 읽고, 나머지 영단어는 그대로 둔다.

    기존 normalize 를 쓰되, 소문자가 섞인 영단어는 미리 자리표로 빼 두었다가
    되돌린다. 그러면 241행의 한 글자 읽기에 안 걸린다."""
    keep = {}

    def hide(m):
        w = m.group(0)
        if re.fullmatch(r"[A-Z]{2,5}", w):
            return w                      # 약어 모양은 기존 규칙에 맡긴다
        key = "\ue000" + chr(0xE100 + len(keep)) + "\ue001"   # 숫자·라틴 없는 사용자영역 문자
        keep[key] = w
        return key

    tmp = re.sub(r"[A-Za-z]+", hide, text)
    out = norm.normalize(tmp)
    for k, w in keep.items():
        out = out.replace(k, w)
    return out


def candidate_C(text, mapping, norm):
    for k, v in mapping.items():
        text = text.replace(k, v)
    return norm.normalize(text)


def synth(cfg, spoken):
    import requests
    import soundfile as sf
    extra = {k: v for k, v in (cfg.get("tts") or {}).items() if k != "streaming_mode"}
    payload = {"text": spoken, "text_lang": "ko",
               "ref_audio_path": cfg["ref_audio"], "prompt_text": cfg["ref_text"],
               "prompt_lang": "ko",
               "aux_ref_audio_paths": [a for a in cfg.get("aux_ref_audio", []) if a],
               **extra, "streaming_mode": False}
    t0 = time.time()
    r = requests.post(cfg["sovits_url"], json=payload, timeout=180)
    r.raise_for_status()
    data, sr = sf.read(io.BytesIO(r.content), dtype="float32", always_2d=False)
    return data, sr, time.time() - t0


def main():
    import soundfile as sf
    import numpy as np
    from laika_tts_norm import TTSNormalizer
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    norm = TTSNormalizer()
    OUT.mkdir(exist_ok=True)

    print("=" * 76)
    print("B / C 시험 - 영어 단어 읽기")
    print(f"  서버 {cfg['sovits_url']}   저장 {OUT}")
    print("=" * 76)

    rows = []
    for i, (text, cmap) in enumerate(CASES, 1):
        variants = {"now": norm.normalize(text), "B": candidate_B(text, norm)}
        if cmap:
            variants["C"] = candidate_C(text, cmap, norm)
        print(f"\n[{i}] {text}")
        for tag, spoken in variants.items():
            try:
                data, sr, el = synth(cfg, spoken)
                path = OUT / f"e{i}_{tag}.wav"
                sf.write(path, data, sr)
                rows.append({"번호": i, "원문": text, "방식": tag, "넘긴문장": spoken,
                             "파일": path.name, "길이초": round(len(data) / sr, 2),
                             "합성초": round(el, 2), "오류": ""})
                print(f"  {tag:<4} {spoken!r}  -> {path.name} ({len(data)/sr:.2f}s)")
            except Exception as e:
                rows.append({"번호": i, "원문": text, "방식": tag, "넘긴문장": spoken,
                             "오류": f"{type(e).__name__}: {e}"})
                print(f"  {tag:<4} 실패 {type(e).__name__}: {e}")

    (BASE / "laika_english_tts_test.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {BASE / 'laika_english_tts_test.json'}")
    print(f"들어보실 파일: {OUT}")
    print("  같은 번호끼리 e1_now / e1_B / e1_C 를 번갈아 들으시면 됩니다.")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for cand in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans CJK JP"):
            if any(f.name == cand for f in font_manager.fontManager.ttflist):
                matplotlib.rcParams["font.family"] = cand
                break
        matplotlib.rcParams["axes.unicode_minus"] = False
        ok = [r for r in rows if not r["오류"]]
        fig, ax = plt.subplots(figsize=(12, 4.8))
        col = {"now": "#C0392B", "B": "#4A7EBB", "C": "#4ec9a5"}
        nums = sorted({r["번호"] for r in ok})
        for j, tag in enumerate(("now", "B", "C")):
            v = [next((r["길이초"] for r in ok if r["번호"] == n and r["방식"] == tag), 0)
                 for n in nums]
            ax.bar([n + (j - 1) * .27 for n in nums], v, width=.27, color=col[tag], label=tag)
        ax.set_xticks(nums); ax.set_xticklabels([f"문장 {n}" for n in nums])
        ax.set_ylabel("오디오 길이 (초)")
        ax.set_title("같은 문장인데 길이가 튀면 스펠링으로 늘어난 것 (now)", fontsize=12)
        ax.legend(); ax.grid(axis="y", alpha=.25)
        fig.tight_layout(); fig.savefig(BASE / "laika_english_tts_test.png", dpi=130)
        print(f"그래프: {BASE / 'laika_english_tts_test.png'}")
    except Exception as e:
        print(f"그래프 생략: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
