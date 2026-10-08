# =============================================================================
# TTS 모드 듣기 비교 - mode 0/1/2/3 을 같은 문장으로 뽑아 wav 로 저장한다
#
# 왜 필요한가 (laika_stream_diag.json, 2026-09-17)
#   PYTHONIOENCODING=utf-8 로 스트리밍이 살아난 뒤 첫 소리까지가 이렇게 나왔다.
#     mode 0 (지금) 7.922초   mode 1 1.681초   mode 2 0.956초   mode 3 0.842초
#   api_v2.py 44행에 적힌 품질은 이렇다.
#     1 Best Quality, Slowest / 2 Medium Quality / 3 Lower Quality, Faster
#   품질은 숫자로 못 고른다. 귀로 들어야 한다.
#
# 무엇을 하나
#   같은 문장을 네 모드로 합성해 tts_samples/ 에 wav 로 남긴다.
#   파일명에 모드와 문장 번호가 들어가므로 번갈아 들으면 된다.
#   첫 조각까지 걸린 시간도 같이 재서 표와 그래프로 낸다.
#
#   저장은 전부 같은 방식으로 맞춘다.
#     mode 0  : 서버가 준 wav 를 그대로 쓴다
#     mode 1~3: media_type=raw 로 받아 조각을 이어붙이고 wav 로 쓴다
#               (스트리밍 wav 의 헤더는 nframes=0 이라 재생기가 길이를 못 읽는다)
#   샘플레이트는 mode 0 의 wav 에서 읽어 그대로 쓴다. 추측하지 않는다.
#
# 실행 (GPT-SoVITS 서버가 떠 있어야 한다)
#   conda deactivate
#   conda activate laika
#   python laika_tts_mode_sample.py
#
#   --modes 0,2,3      뽑을 모드
#   --cases 2          앞에서 N 개 문장만
#   --text "..."       이 문장 하나만
#
# 결과: tts_samples/*.wav / laika_tts_mode_sample.json / laika_tts_mode_sample.png
# =============================================================================

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).parent
OUT = BASE / "tts_samples"

# 라이카가 실제로 말한 문장 (sessions/ 에서 가져옴) + 숫자 읽기 확인용
CASES = [
    ("안녕하세요 여러분, 저는 라이카예요.", "짧은 인사"),
    ("오 진짜요? 날씨 좋으면 기분도 같이 좋아지죠!", "짧은 반응"),
    ("사강 이대이, 곱창 대 훈제삼겹살이에요! 둘 다 비주얼이 진짜 미쳤는데 저는 훈제삼겹살이요.", "긴 문장"),
    ("공격력 2500에 별 8개짜리 악마족이에요.", "숫자 읽기 (정규화 확인)"),
]


def load_cfg():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def payload_for(cfg, text, mode, media_type):
    try:
        from laika_tts_norm import TTSNormalizer
        spoken = TTSNormalizer().normalize(text)
    except Exception:
        spoken = text
    return spoken, {
        "text": spoken, "text_lang": "ko",
        "ref_audio_path": cfg["ref_audio"],
        "prompt_text": cfg["ref_text"], "prompt_lang": "ko",
        "aux_ref_audio_paths": [a for a in cfg.get("aux_ref_audio", []) if a],
        "streaming_mode": mode, "media_type": media_type,
        **cfg["tts"],
    }


def synth(cfg, text, mode):
    """한 모드로 합성한다. 반환: (오디오 float32, sr, 잰 값 dict)"""
    import requests
    import soundfile as sf
    import io

    if mode == 0:
        spoken, pl = payload_for(cfg, text, 0, "wav")
        t0 = time.time()
        r = requests.post(cfg["sovits_url"], json=pl, timeout=180)
        r.raise_for_status()
        first = time.time() - t0
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32", always_2d=False)
        return data, sr, {"첫조각": round(first, 3), "조각수": 1,
                          "마지막조각": round(first, 3), "바이트": len(r.content),
                          "읽은문장": spoken}

    spoken, pl = payload_for(cfg, text, mode, "raw")
    t0 = time.time()
    r = requests.post(cfg["sovits_url"], json=pl, stream=True, timeout=180)
    r.raise_for_status()
    buf, marks = bytearray(), []
    for chunk in r.iter_content(chunk_size=4096):
        if not chunk:
            continue
        marks.append(time.time() - t0)
        buf += chunk
    r.close()
    if not marks:
        raise RuntimeError("조각이 하나도 안 왔습니다")
    pcm = np.frombuffer(bytes(buf), dtype="<i2").astype("float32") / 32768.0
    return pcm, None, {"첫조각": round(marks[0], 3), "조각수": len(marks),
                       "마지막조각": round(marks[-1], 3), "바이트": len(buf),
                       "읽은문장": spoken}


def plot(rows, out_png):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception as e:
        print(f"그래프 생략 (matplotlib 없음): {e}")
        return
    for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic",
                 "Noto Sans CJK KR", "Noto Sans CJK JP"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    ok = [r for r in rows if not r.get("오류")]
    if not ok:
        print("그릴 것이 없습니다.")
        return
    cases = sorted({r["문장번호"] for r in ok})
    modes = sorted({r["모드"] for r in ok})

    fig, axes = plt.subplots(len(cases), 2, figsize=(14, 3.0 * len(cases)),
                             squeeze=False, gridspec_kw={"width_ratios": [2, 1]})
    colors = {0: "#C0392B", 1: "#7c6cf5", 2: "#4A7EBB", 3: "#4ec9a5"}
    for i, ci in enumerate(cases):
        a1, a2 = axes[i][0], axes[i][1]
        for r in [x for x in ok if x["문장번호"] == ci]:
            env = r.get("_envelope") or []
            if env:
                t = [k * r["_env_step"] for k in range(len(env))]
                a1.plot(t, env, lw=1.1, color=colors.get(r["모드"], "#888"),
                        label=f"mode {r['모드']}  {r['길이초']:.2f}s")
        a1.set_ylabel("크기"); a1.legend(fontsize=8, ncol=2)
        a1.set_title(f"문장 {ci}: {[c for c in CASES][ci-1][1] if ci-1 < len(CASES) else ''}"
                     f"  — 파형(20ms RMS)", fontsize=10.5)
        a1.grid(alpha=.2)
        if i == len(cases) - 1:
            a1.set_xlabel("초")

        g = [x for x in ok if x["문장번호"] == ci]
        a2.bar([f"m{x['모드']}" for x in g], [x["첫조각"] for x in g],
               color=[colors.get(x["모드"], "#888") for x in g])
        for j, x in enumerate(g):
            a2.text(j, x["첫조각"], f"{x['첫조각']:.2f}", ha="center",
                    va="bottom", fontsize=9, fontweight="bold")
        a2.set_ylabel("첫 조각(초)"); a2.grid(axis="y", alpha=.25)
        a2.set_title("첫 소리까지", fontsize=10.5)

    fig.suptitle("TTS 모드 비교 — 파형은 눈으로, 품질은 tts_samples/ 의 wav 로", fontsize=13)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="0,1,2,3")
    ap.add_argument("--cases", type=int, default=0)
    ap.add_argument("--text", default="")
    args = ap.parse_args()

    import soundfile as sf
    cfg = load_cfg()
    modes = [int(m) for m in args.modes.split(",") if m.strip() != ""]
    cases = [(args.text, "직접 지정")] if args.text else (
        CASES[:args.cases] if args.cases else CASES)

    OUT.mkdir(exist_ok=True)
    print("=" * 78)
    print("TTS 모드 듣기 비교")
    print(f"  서버   : {cfg['sovits_url']}")
    print(f"  모드   : {modes}")
    print(f"  문장   : {len(cases)}개")
    print(f"  저장   : {OUT}")
    print("=" * 78)

    rows, sr_known = [], None
    for ci, (text, note) in enumerate(cases, 1):
        print(f"\n[문장 {ci}] {note}")
        print(f"  {text}")
        for mode in modes:
            try:
                data, sr, info = synth(cfg, text, mode)
            except Exception as e:
                rows.append({"문장번호": ci, "모드": mode, "원문": text,
                             "오류": f"{type(e).__name__}: {e}"})
                print(f"  mode {mode}  실패: {type(e).__name__}: {e}")
                continue
            if sr is not None:
                sr_known = sr                      # mode 0 의 wav 에서 읽은 값
            use_sr = sr or sr_known
            if use_sr is None:
                rows.append({"문장번호": ci, "모드": mode, "원문": text,
                             "오류": "샘플레이트를 모릅니다. mode 0 을 함께 돌리십시오."})
                print(f"  mode {mode}  실패: 샘플레이트 불명 (mode 0 을 함께 돌릴 것)")
                continue

            path = OUT / f"s{ci}_mode{mode}.wav"
            sf.write(path, data, use_sr)
            dur = len(data) / use_sr

            win = max(1, int(use_sr * 0.02))
            n = len(data) // win
            env = [float(np.sqrt(np.mean(data[k*win:(k+1)*win] ** 2)))
                   for k in range(n)] if n else []

            rows.append({"문장번호": ci, "모드": mode, "원문": text, "비고": note,
                         "파일": path.name, "sr": use_sr, "길이초": round(dur, 3),
                         "오류": "", **info,
                         "_envelope": [round(v, 5) for v in env],
                         "_env_step": 0.02})
            print(f"  mode {mode}  첫조각 {info['첫조각']:>6.3f}s  조각 {info['조각수']:>4}  "
                  f"길이 {dur:>5.2f}s  -> {path.name}")

    print("\n" + "-" * 78)
    print(f"{'문장':<5}{'모드':<6}{'첫조각':>9}{'조각':>7}{'길이':>8}   파일")
    for r in rows:
        if r.get("오류"):
            print(f"{r['문장번호']:<5}{r['모드']:<6}{'실패':>9}   {r['오류'][:44]}")
            continue
        print(f"{r['문장번호']:<5}{r['모드']:<6}{r['첫조각']:>9.3f}{r['조각수']:>7}"
              f"{r['길이초']:>8.2f}   {r['파일']}")

    print(f"\n들어보실 파일: {OUT}")
    print("  같은 문장 번호끼리 모드만 바꿔가며 들으시면 됩니다.")
    print("  예) s1_mode0.wav <-> s1_mode2.wav <-> s1_mode3.wav")

    out = BASE / "laika_tts_mode_sample.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out}")
    plot(rows, BASE / "laika_tts_mode_sample.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
