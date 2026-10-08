# =============================================================================
# C 측정 - no_speech_prob / avg_logprob 로 무음 오인식을 거를 수 있는가
#
# 왜 필요한가
#   debug_audio 의 2026-08-29 기록 162건 중 6건이 무음인데 말로 전사됐다.
#   그중 3건은 "한국어 대화입니다." 였고, 이것은 laika_core.py 126행
#   build_initial_prompt 의 접두어와 글자까지 같다.
#   rms 로는 이미 0.020 에서 완전히 갈렸다(오분류 0건). C 는 그 대안이다.
#
#   인수인계 4절에 "no_speech_prob 실제 값은 미측정 (세그먼트 0개라
#   평균이 0으로 계산됨)" 이라고 남아 있다. 그 미측정을 여기서 없앤다.
#   세그먼트가 0개가 되지 않도록 임계를 풀고 전사한다.
#
# 조건 3가지를 같은 배열로 돌린다
#   A 앱동일   : 지금 방송이 쓰는 설정 그대로. 재현되는지 본다.
#   B 임계해제 : no_speech_threshold=1.0, log_prob_threshold=None.
#                세그먼트가 반드시 나와 no_speech_prob 을 읽을 수 있다.
#   C 프롬프트없음 : B 와 같되 initial_prompt 를 빼서 echo 가설을 본다.
#
# 실행 (방송을 끄고 돌릴 것. GPU 메모리를 TTS 서버와 나눠 쓰면 실패한다)
#   conda deactivate
#   conda activate laika
#   python laika_nospeech_check.py
#
#   --date 20260829     대상 날짜 (기본 20260829)
#   --limit 30          앞에서 N 건만
#   --short-only 2.0    이 길이 이하만 (빠른 확인용)
#   --device cpu        GPU 메모리가 모자랄 때
#
# 결과: laika_nospeech_check.json / laika_nospeech_check.png
# =============================================================================

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

import laika_core          # DLL 등록이 먼저여야 방송과 같은 조건이 된다

BASE = Path(__file__).parent
DEBUG_DIR = BASE / "debug_audio"

# 무음에서 나온 것으로 확인된 문구. 라벨을 붙이는 데만 쓴다.
GHOST = {"한국어 대화입니다.", "고맙습니다.", "시청해주셔서 감사합니다.",
         "감사합니다.", "구독과 좋아요 부탁드립니다."}


def load_audio(wav_path):
    """core 가 넘기는 것과 같은 형태(float32, 1차원)로 되읽는다."""
    import soundfile as sf
    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype("float32"), sr


def load_records(date_prefix):
    out = []
    for jf in sorted(DEBUG_DIR.glob(f"{date_prefix}_*.json")):
        wf = jf.with_suffix(".wav")
        if not wf.exists():
            continue
        try:
            meta = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"stem": jf.stem, "wav": wf, "meta": meta})
    return out


def label_of(meta):
    """저장된 전사결과로 라벨을 붙인다."""
    if meta.get("전사오류"):
        return "오류"
    t = (meta.get("전사결과") or "").strip()
    if not t:
        return "빈문자열"
    if t in GHOST:
        return "환청"
    return "정상"


def run_one(model, audio, prompt, relaxed):
    """한 조건으로 전사하고 판정에 쓸 값만 뽑는다.

    relaxed=True 면 임계를 풀어 세그먼트가 버려지지 않게 한다.
    그래야 no_speech_prob 을 실제로 읽을 수 있다."""
    kw = {"language": "ko"}
    if prompt:
        kw["initial_prompt"] = prompt
    if relaxed:
        kw["no_speech_threshold"] = 1.0     # 이 값보다 커야 버리므로 안 버려진다
        kw["log_prob_threshold"] = None
        kw["condition_on_previous_text"] = False   # 파일끼리 섞이지 않게

    t0 = time.time()
    segs, info = model.transcribe(audio, **kw)
    segs = list(segs)
    el = time.time() - t0

    ns = [float(s.no_speech_prob) for s in segs]
    lp = [float(s.avg_logprob) for s in segs]
    return {
        "텍스트": " ".join(s.text.strip() for s in segs).strip(),
        "세그먼트수": len(segs),
        "no_speech_최대": round(max(ns), 4) if ns else None,
        "no_speech_평균": round(float(np.mean(ns)), 4) if ns else None,
        "avg_logprob_평균": round(float(np.mean(lp)), 4) if lp else None,
        "초": round(el, 3),
    }


def plot(rows, out_png):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception as e:
        print(f"그래프 생략 (matplotlib 없음): {e}")
        return

    for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic", "Noto Sans CJK KR"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    good = [r for r in rows if r["라벨"] == "정상" and r["B"]["no_speech_최대"] is not None]
    bad = [r for r in rows if r["라벨"] in ("환청", "빈문자열")
           and r["B"]["no_speech_최대"] is not None]

    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15.5, 4.8))

    # 1) no_speech_prob 분포
    a1.scatter([r["rms"] for r in good], [r["B"]["no_speech_최대"] for r in good],
               s=16, c="#4A7EBB", label=f"정상 n={len(good)}")
    a1.scatter([r["rms"] for r in bad], [r["B"]["no_speech_최대"] for r in bad],
               s=90, marker="x", c="#C0392B", label=f"환청·빈 n={len(bad)}")
    a1.set_xlabel("rms"); a1.set_ylabel("no_speech_prob (세그먼트 최대)")
    a1.set_title("no_speech_prob 이 무음 건을 갈라내는가", fontsize=11)
    a1.legend(fontsize=9); a1.grid(alpha=.25)

    # 2) 임계선별 오분류
    ths = [0.3, 0.5, 0.6, 0.7, 0.8, 0.9]
    miss = [sum(1 for r in bad if (r["B"]["no_speech_최대"] or 0) < t) for t in ths]
    lost = [sum(1 for r in good if (r["B"]["no_speech_최대"] or 0) >= t) for t in ths]
    x = range(len(ths))
    a2.bar([i - 0.2 for i in x], miss, width=0.4, color="#C0392B", label="못 거른 무음")
    a2.bar([i + 0.2 for i in x], lost, width=0.4, color="#E0A030", label="잘못 버린 실제 발화")
    for i, (m, l) in enumerate(zip(miss, lost)):
        a2.text(i - 0.2, m, str(m), ha="center", va="bottom", fontsize=9)
        a2.text(i + 0.2, l, str(l), ha="center", va="bottom", fontsize=9)
    a2.set_xticks(list(x)); a2.set_xticklabels([str(t) for t in ths])
    a2.set_xlabel("no_speech_prob 기준선 (이상이면 버림)"); a2.set_ylabel("건수")
    a2.set_title("no_speech 만으로 오분류 0 이 되는 선이 있는가", fontsize=11)
    a2.legend(fontsize=9); a2.grid(axis="y", alpha=.25)

    # 3) initial_prompt echo
    tgt = [r for r in rows if r["라벨"] in ("환청", "빈문자열")]
    echo_b = sum(1 for r in tgt if r["B"]["텍스트"].strip() in GHOST)
    echo_c = sum(1 for r in tgt if r["C"]["텍스트"].strip() in GHOST)
    empty_c = sum(1 for r in tgt if not r["C"]["텍스트"].strip())
    a3.bar(["프롬프트 있음\n(B)", "프롬프트 없음\n(C)"], [echo_b, echo_c],
           color=["#C0392B", "#4A7EBB"])
    for i, v in enumerate([echo_b, echo_c]):
        a3.text(i, v, str(v), ha="center", va="bottom", fontsize=11)
    a3.set_ylabel("환청 문구가 나온 건수")
    a3.set_title(f"initial_prompt 를 빼면 사라지는가\n무음 {len(tgt)}건 중 "
                 f"프롬프트 없을 때 빈 문자열 {empty_c}건", fontsize=11)
    a3.grid(axis="y", alpha=.25)

    fig.suptitle(f"C 측정 - no_speech_prob / initial_prompt echo ({len(rows)}건)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260829", help="대상 날짜 접두어")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 건만")
    ap.add_argument("--short-only", type=float, default=0.0,
                    help="이 길이(초) 이하만")
    ap.add_argument("--model", default="")
    ap.add_argument("--device", default="")
    ap.add_argument("--compute", default="")
    args = ap.parse_args()

    cfg = {}
    try:
        cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    stt = cfg.get("stt", {})
    model_size = args.model or stt.get("model_size", "medium")
    device = args.device or stt.get("device", "cuda")
    compute = args.compute or ("float16" if device == "cuda" else "int8")

    recs = load_records(args.date)
    if args.short_only > 0:
        recs = [r for r in recs if r["meta"].get("길이초", 99) <= args.short_only]
    if args.limit:
        recs = recs[:args.limit]
    if not recs:
        print(f"{args.date} 로 시작하는 기록이 없습니다: {DEBUG_DIR}")
        return 1

    prompt = laika_core.build_initial_prompt(laika_core.load_stt_vocab())

    print("=" * 78)
    print("C 측정 - no_speech_prob / initial_prompt echo")
    print(f"  파이썬        : {sys.executable}")
    print(f"  모델          : {model_size} / {device} / {compute}")
    print(f"  대상          : {len(recs)}건 ({args.date})")
    print(f"  initial_prompt: {len(prompt or '')}자")
    print(f"    {(prompt or '')[:70]}...")
    print(f"  호출 수       : {len(recs) * 3} (조건 3가지)")
    print("=" * 78)

    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device=device, compute_type=compute)

    rows = []
    for i, r in enumerate(recs, 1):
        meta = r["meta"]
        audio, sr = load_audio(r["wav"])
        lab = label_of(meta)
        row = {
            "stem": r["stem"],
            "라벨": lab,
            "길이초": meta.get("길이초"),
            "peak": meta.get("peak"),
            "rms": meta.get("rms"),
            "저장된_전사": (meta.get("전사결과") or ""),
        }
        try:
            row["A"] = run_one(model, audio, prompt, relaxed=False)
            row["B"] = run_one(model, audio, prompt, relaxed=True)
            row["C"] = run_one(model, audio, None, relaxed=True)
        except Exception as e:
            row["오류"] = f"{type(e).__name__}: {e}"
            print(f"[{i:>3}/{len(recs)}] {r['stem']}  실패: {row['오류']}")
            rows.append(row)
            continue

        rows.append(row)
        ns = row["B"]["no_speech_최대"]
        print(f"[{i:>3}/{len(recs)}] {row['라벨']:<5} rms={row['rms']:.4f} "
              f"no_speech={ns if ns is None else f'{ns:.4f}'} "
              f"| A={row['A']['텍스트'][:24]!r} | C={row['C']['텍스트'][:24]!r}")

    # ------------------------------------------------------------ 요약
    ok = [r for r in rows if "오류" not in r]
    good = [r for r in ok if r["라벨"] == "정상"]
    bad = [r for r in ok if r["라벨"] in ("환청", "빈문자열")]

    print("\n" + "-" * 78)
    print(f"측정 성공 {len(ok)}건 / 정상 {len(good)} / 환청·빈 {len(bad)}")

    def rng(items, key):
        v = [x["B"][key] for x in items if x["B"][key] is not None]
        return (min(v), max(v), sum(v) / len(v)) if v else (None, None, None)

    for name, items in (("정상", good), ("환청·빈", bad)):
        lo, hi, av = rng(items, "no_speech_최대")
        lo2, hi2, av2 = rng(items, "avg_logprob_평균")
        if lo is None:
            continue
        print(f"  {name:<7} no_speech {lo:.4f}~{hi:.4f} (평균 {av:.4f})  "
              f"avg_logprob {lo2:.3f}~{hi2:.3f} (평균 {av2:.3f})")

    print("\n  no_speech_prob 기준선별 오분류")
    for t in (0.3, 0.5, 0.6, 0.7, 0.8, 0.9):
        m = sum(1 for r in bad if (r["B"]["no_speech_최대"] or 0) < t)
        l = sum(1 for r in good if (r["B"]["no_speech_최대"] or 0) >= t)
        print(f"    >= {t:.1f} 이면 버림 : 못 거른 무음 {m}건 / 잘못 버린 발화 {l}건")

    echo_b = sum(1 for r in bad if r["B"]["텍스트"].strip() in GHOST)
    echo_c = sum(1 for r in bad if r["C"]["텍스트"].strip() in GHOST)
    empty_c = sum(1 for r in bad if not r["C"]["텍스트"].strip())
    print(f"\n  initial_prompt echo : 프롬프트 있음(B) {echo_b}건 / "
          f"없음(C) {echo_c}건 / 없을 때 빈 문자열 {empty_c}건 (무음 {len(bad)}건 중)")

    chg = [r for r in good if r["B"]["텍스트"].strip() != r["C"]["텍스트"].strip()]
    print(f"  정상 발화 중 프롬프트 유무로 결과가 달라진 건: {len(chg)}/{len(good)}")

    out = BASE / "laika_nospeech_check.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {out}")
    plot(rows, BASE / "laika_nospeech_check.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
