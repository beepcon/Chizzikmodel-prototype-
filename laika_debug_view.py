# =============================================================================
# 라이카 디버그 음성 확인 도구
#
#   debug_audio/ 에 쌓인 음성을 목록으로 보고, 파형을 그리고,
#   STT 설정 조합을 바꿔가며 같은 배열을 다시 전사해 비교한다.
#
#   laika_core 가 STT 에 넘기는 것은 파일 경로가 아니라 numpy 배열이다.
#   따라서 여기서도 wav 를 배열로 되읽어 배열째 넘긴다. (조건 일치)
#
#   사용:
#     python laika_debug_view.py                 목록만
#     python laika_debug_view.py --plot          전체 파형 그리기
#     python laika_debug_view.py --file 2026...  한 개만 자세히
#     python laika_debug_view.py --compare 2026... 설정 조합 비교
# =============================================================================

import argparse
import json
import time
from pathlib import Path

import numpy as np

import laika_core  # DLL 등록이 먼저 실행되어야 실제 프로그램과 같은 조건이 된다

BASE = Path(__file__).parent
DEBUG_DIR = BASE / "debug_audio"


# ---------------------------------------------------------------- 공통

def setup_font():
    import matplotlib
    from matplotlib import font_manager
    for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def load_records():
    """저장된 wav + json 쌍을 읽어 시각순으로 반환한다."""
    if not DEBUG_DIR.is_dir():
        return []
    out = []
    for jf in sorted(DEBUG_DIR.glob("*.json")):
        wf = jf.with_suffix(".wav")
        if not wf.exists():
            continue
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [건너뜀] {jf.name} 읽기 실패: {e}")
            continue
        out.append({"stem": jf.stem, "wav": wf, "meta": meta})
    return out


def load_audio(wav_path):
    """core 가 넘기는 것과 같은 형태(float32, 1차원)로 되읽는다."""
    import soundfile as sf
    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype("float32"), sr


def rms_envelope(a, sr, win_ms=20):
    win = max(1, int(sr * win_ms / 1000))
    n = len(a) // win
    if n < 1:
        return np.array([]), np.array([])
    e = np.array([np.sqrt(np.mean(a[i * win:(i + 1) * win] ** 2)) for i in range(n)])
    t = np.arange(n) * win / sr
    return t, e


# ---------------------------------------------------------------- 목록

def cmd_list(records):
    print(f"\n저장 폴더: {DEBUG_DIR}")
    print(f"파일 수  : {len(records)}\n")
    if not records:
        print("아직 저장된 것이 없습니다. 방송을 켜고 마이크로 말한 뒤 다시 실행하세요.")
        return

    print(f"{'파일':<24}{'길이':>7}{'peak':>9}{'rms':>9}  전사결과")
    print("-" * 100)
    for r in records:
        m = r["meta"]
        txt = m.get("전사결과")
        err = m.get("전사오류")
        shown = txt if txt else (f"[{err}]" if err else "[빈 문자열]")
        print(f"{r['stem']:<24}{m.get('길이초', 0):>6.2f}s"
              f"{m.get('peak', 0):>9.4f}{m.get('rms', 0):>9.4f}  {shown[:50]}")

    # 요약 통계
    durs = [m["meta"].get("길이초", 0) for m in records]
    empties = sum(1 for r in records if not r["meta"].get("전사결과"))
    dropped = sum(1 for r in records
                  if r["meta"].get("전사오류", "") and "min_speech_sec" in str(r["meta"]["전사오류"]))
    print("-" * 100)
    print(f"평균 길이 {np.mean(durs):.2f}s / 최단 {np.min(durs):.2f}s / 최장 {np.max(durs):.2f}s")
    print(f"전사 결과 없음 {empties}건 / 길이 미달로 버려짐 {dropped}건")


# ---------------------------------------------------------------- 파형

def cmd_plot(records, limit=12):
    if not records:
        print("그릴 것이 없습니다.")
        return
    import matplotlib
    matplotlib.use("Agg")
    setup_font()
    import matplotlib.pyplot as plt

    recs = records[-limit:]
    rows = (len(recs) + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(15, 2.4 * rows), squeeze=False)

    for ax, r in zip(axes.ravel(), recs):
        a, sr = load_audio(r["wav"])
        m = r["meta"]
        t = np.arange(len(a)) / sr
        ax.plot(t, a, lw=0.5, color="#4c72b0")

        te, e = rms_envelope(a, sr)
        if len(te):
            ax.plot(te, e, lw=1.2, color="#dd8452", label="RMS(20ms)")

        thr = m.get("설정_threshold")
        if thr:
            ax.axhline(thr, color="red", ls="--", lw=1, label=f"threshold {thr}")

        txt = m.get("전사결과") or f"[{m.get('전사오류') or '빈 문자열'}]"
        ax.set_title(f"{r['stem']}  {m.get('길이초', 0):.2f}s  →  {txt[:34]}", fontsize=9)
        ax.set_ylim(-1, 1)
        ax.grid(alpha=0.25)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="upper right")

    for ax in axes.ravel()[len(recs):]:
        ax.axis("off")

    fig.suptitle("저장된 마이크 입력 파형 (붉은 선 = 발화 판정 임계값)", fontsize=13)
    fig.tight_layout()
    out = BASE / "debug_waveforms.png"
    fig.savefig(out, dpi=110)
    print(f"→ 파형 저장: {out}")


def cmd_detail(records, stem):
    rec = next((r for r in records if r["stem"] == stem or stem in r["stem"]), None)
    if rec is None:
        print(f"찾을 수 없습니다: {stem}")
        return None

    import matplotlib
    matplotlib.use("Agg")
    setup_font()
    import matplotlib.pyplot as plt

    a, sr = load_audio(rec["wav"])
    m = rec["meta"]

    print(f"\n{'=' * 70}\n{rec['stem']}\n{'=' * 70}")
    for k, v in m.items():
        print(f"  {k:<20}: {v}")

    fig, axes = plt.subplots(3, 1, figsize=(13, 8))
    t = np.arange(len(a)) / sr

    axes[0].plot(t, a, lw=0.5, color="#4c72b0")
    axes[0].set_title("파형 전체", fontsize=11)
    axes[0].set_ylim(-1, 1)

    te, e = rms_envelope(a, sr)
    axes[1].plot(te, e, color="#dd8452", lw=1.4)
    thr = m.get("설정_threshold")
    if thr:
        axes[1].axhline(thr, color="red", ls="--", lw=1,
                        label=f"threshold {thr} (이 위여야 발화로 잡힘)")
        axes[1].legend(fontsize=8)
    axes[1].set_title("RMS 포락선 — 앞뒤가 잘렸는지 확인", fontsize=11)

    # 스펙트로그램: 소리 자체가 깨졌는지(클리핑/잡음) 눈으로 본다
    axes[2].specgram(a, Fs=sr, NFFT=512, noverlap=384, cmap="magma")
    axes[2].set_title("스펙트로그램 — 잡음/클리핑 확인", fontsize=11)
    axes[2].set_xlabel("초")

    for ax in axes[:2]:
        ax.grid(alpha=0.25)

    txt = m.get("전사결과") or f"[{m.get('전사오류') or '빈 문자열'}]"
    fig.suptitle(f"{rec['stem']}   전사 결과: {txt}", fontsize=12)
    fig.tight_layout()
    out = BASE / f"debug_detail_{rec['stem']}.png"
    fig.savefig(out, dpi=110)
    print(f"\n→ 상세 그래프: {out}")
    return rec


# ---------------------------------------------------------------- 어휘 파일

VOCAB_PATH = BASE / "stt_vocab.txt"

# 파일이 없을 때만 만드는 초안.
# 이미 있으면 손대지 않는다. 고친 내용이 날아가면 안 된다.
VOCAB_TEMPLATE = """\
# Whisper 가 자주 틀리는 고유명사를 한 줄에 하나씩 적는다.
# 이 목록은 initial_prompt 로 들어가 인식 후보를 유도한다.
#
# '#' 로 시작하는 줄과 빈 줄은 무시된다.
# 이 파일만 고치면 코드 수정 없이 어휘가 바뀐다.
#
# 주의: initial_prompt 는 강제가 아니라 힌트다.
#       너무 많이 넣으면 오히려 엉뚱한 단어가 끼어든다.
#       20~30개 이내로 유지하고, 실제로 틀린 것만 넣는 편이 낫다.

라이카
카구야
초카고야히메
"""


def load_vocab(path=None):
    """어휘 목록을 읽는다. 파일이 없으면 초안을 만들고 그 내용을 쓴다.

    laika_core 에서도 그대로 import 해서 쓸 수 있다."""
    p = Path(path) if path else VOCAB_PATH
    if not p.exists():
        p.write_text(VOCAB_TEMPLATE, encoding="utf-8")
        print(f"[어휘] 초안을 만들었습니다: {p.name}")

    terms = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def build_initial_prompt(terms=None, prefix="한국어 대화입니다."):
    """어휘 목록을 initial_prompt 문자열로 만든다.
    비어 있으면 None 을 돌려준다. (인자 자체를 넘기지 않기 위함)"""
    terms = load_vocab() if terms is None else terms
    if not terms:
        return None
    return f"{prefix} {', '.join(terms)}."


# ---------------------------------------------------------------- 조합 비교

def build_presets():
    """비교할 조합을 만든다.

    어휘 힌트는 stt_vocab.txt 에서 읽으므로,
    파일을 고치면 코드를 건드리지 않고 다른 어휘로 다시 시험할 수 있다."""
    hint = build_initial_prompt()

    presets = {
        "현재값(인자없음)": {},
        "빔10": {"beam_size": 10},
        "이전문맥끄기": {"condition_on_previous_text": False},
        "빔10+문맥끄기": {"beam_size": 10, "condition_on_previous_text": False},
        "VAD켜기": {"vad_filter": True},
    }

    if hint:
        presets["어휘힌트"] = {"initial_prompt": hint}
        presets["빔10+문맥끄기+어휘힌트"] = {
            "beam_size": 10, "condition_on_previous_text": False,
            "initial_prompt": hint,
        }
    else:
        print(f"[어휘] {VOCAB_PATH.name} 이 비어 있어 어휘 힌트 조합을 건너뜁니다.")

    return presets



def cmd_compare(records, stem, model_size, device, compute):
    rec = next((r for r in records if r["stem"] == stem or stem in r["stem"]), None)
    if rec is None:
        print(f"찾을 수 없습니다: {stem}")
        return

    from faster_whisper import WhisperModel

    a, sr = load_audio(rec["wav"])
    print(f"\n{'=' * 70}")
    print(f"조합 비교: {rec['stem']}  ({len(a) / sr:.2f}초, {sr}Hz)")
    print(f"방송 당시 결과: {rec['meta'].get('전사결과')}")
    print(f"{'=' * 70}")

    t0 = time.time()
    model = WhisperModel(model_size, device=device, compute_type=compute)
    print(f"모델 로드 {time.time() - t0:.1f}초\n")

    rows = []
    presets = build_presets()
    vocab = load_vocab()
    if vocab:
        print(f"어휘 파일: {VOCAB_PATH.name}  ({len(vocab)}개) {', '.join(vocab[:10])}"
              + (" ..." if len(vocab) > 10 else ""))
    print()

    for name, kw in presets.items():
        t0 = time.time()
        try:
            segs, _ = model.transcribe(a, language="ko", **kw)   # 배열째 전달 (core 와 동일)
            segs = list(segs)
        except Exception as e:
            print(f"[{name}] 실패: {type(e).__name__}: {e}")
            continue
        el = time.time() - t0
        text = " ".join(s.text.strip() for s in segs).strip()
        lp = float(np.mean([s.avg_logprob for s in segs])) if segs else 0.0
        ns = float(np.mean([s.no_speech_prob for s in segs])) if segs else 0.0
        rows.append({"이름": name, "인자": kw, "초": round(el, 2),
                     "avg_logprob": round(lp, 3), "no_speech": round(ns, 3),
                     "전사": text})
        print(f"[{name}]")
        print(f"   {el:5.2f}s  logprob {lp:7.3f}  no_speech {ns:.3f}")
        print(f"   → {text}\n")

    out = BASE / f"debug_compare_{rec['stem']}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ 저장: {out}")
    plot_compare(rows, rec["stem"])


def plot_compare(rows, stem):
    if not rows:
        return
    import matplotlib
    matplotlib.use("Agg")
    setup_font()
    import matplotlib.pyplot as plt

    names = [r["이름"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    lps = [r["avg_logprob"] for r in rows]
    bars = axes[0].barh(range(len(names)), lps, color="#4c72b0")
    bars[0].set_color("#c44e52")
    axes[0].set_yticks(range(len(names)))
    axes[0].set_yticklabels(names, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].set_title("평균 logprob — 0 에 가까울수록 확신", fontsize=11)
    axes[0].grid(axis="x", alpha=0.3)
    for i, v in enumerate(lps):
        axes[0].text(v, i, f" {v}", va="center", fontsize=8)

    axes[1].axis("off")
    axes[1].set_title("전사 결과", fontsize=11, loc="left")
    for i, r in enumerate(rows):
        color = "#c44e52" if i == 0 else "#222222"
        axes[1].text(0.0, 1 - i * 0.13, f"{r['이름']}", fontsize=9,
                     color=color, weight="bold", transform=axes[1].transAxes)
        axes[1].text(0.0, 1 - i * 0.13 - 0.05, f"  {r['전사'][:50]}", fontsize=9,
                     color=color, transform=axes[1].transAxes)

    fig.suptitle(f"{stem}  설정 조합 비교 (붉은색 = 현재 코드)", fontsize=13)
    fig.tight_layout()
    out = BASE / f"debug_compare_{stem}.png"
    fig.savefig(out, dpi=110)
    print(f"→ 그래프: {out}")


# ---------------------------------------------------------------- 진입점

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plot", action="store_true", help="최근 파일들 파형 그리기")
    ap.add_argument("--file", help="한 개 자세히 보기 (파일명 일부)")
    ap.add_argument("--compare", help="설정 조합 비교 (파일명 일부)")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute", default="float16")
    args = ap.parse_args()

    records = load_records()
    cmd_list(records)

    if args.plot:
        cmd_plot(records)
    if args.file:
        cmd_detail(records, args.file)
    if args.compare:
        cmd_compare(records, args.compare, args.model, args.device, args.compute)


if __name__ == "__main__":
    main()
