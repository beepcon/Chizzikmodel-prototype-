# =============================================================================
# 무음 오인식 대책 적용 후 검증
#
# 무엇을 확인하나
#   1. 무음 6건이 실제로 사라지는가
#   2. 정상 156건이 그대로 남는가
#   3. log_prob_threshold=None 때문에 정상 전사 내용이 달라지지 않는가
#      (이 값은 온도 재시도 판단에도 쓰인다. 부작용이 있으면 여기서 드러난다)
#
# 어떻게
#   - A(rms 컷)는 _mic_thread 와 같은 식으로 여기서 재현한다.
#   - C(디코딩 설정)는 흉내내지 않고 LaikaCore._stt_kwargs 를 그대로 호출한다.
#     검증이 실제 코드 경로를 지나야 의미가 있다.
#
# 실행 (방송을 끄고)
#   conda deactivate
#   conda activate laika
#   python laika_verify_stt_fix.py
#
#   --date 20260829   대상 날짜 (기본 20260829)
#   --device cpu      GPU 메모리가 모자랄 때
#
# 결과: laika_verify_stt_fix.json / laika_verify_stt_fix.png
# =============================================================================

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

import laika_core

BASE = Path(__file__).parent
DEBUG_DIR = BASE / "debug_audio"

GHOST = {"한국어 대화입니다.", "고맙습니다.", "시청해주셔서 감사합니다.",
         "감사합니다.", "다음 영상에서 만나요!", "시청해 주셔서 감사합니다."}


class _CfgOnly:
    """_stt_kwargs 는 self.cfg 만 쓴다. 코어를 통째로 띄우지 않고 실제 메서드를 부른다."""
    def __init__(self, cfg):
        self.cfg = cfg


def read_wav(path):
    """debug_audio 의 wav 는 float32(format 3) 라 wave 모듈로 못 연다."""
    b = Path(path).read_bytes()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError("RIFF 아님")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(b):
        cid = b[pos:pos + 4]
        sz = struct.unpack("<I", b[pos + 4:pos + 8])[0]
        body = b[pos + 8:pos + 8 + sz]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + sz + (sz & 1)
    tag, ch, rate, _, _, bits = fmt
    if tag == 3 and bits == 32:
        a = np.frombuffer(data, dtype="<f4")
    elif tag == 1 and bits == 16:
        a = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
    else:
        raise ValueError(f"미지원 포맷 tag={tag} bits={bits}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a.astype("float32"), rate


def label_of(meta):
    t = (meta.get("전사결과") or "").strip()
    if meta.get("전사오류"):
        return "오류"
    return "정상" if (t and t not in GHOST) else "무음"


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

    good = [r for r in rows if r["라벨"] == "정상"]
    bad = [r for r in rows if r["라벨"] == "무음"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.2))

    # 1) 적용 전후 결과
    before_bad = sum(1 for r in bad if r["이전_전사"].strip())
    after_bad = sum(1 for r in bad if r["새_전사"].strip())
    before_good = sum(1 for r in good if r["이전_전사"].strip())
    after_good = sum(1 for r in good if r["새_전사"].strip())
    x = [0, 1]
    a1.bar([i - 0.2 for i in x], [before_bad, after_bad], width=0.4,
           color="#C0392B", label="무음인데 말이 나온 건")
    a1.bar([i + 0.2 for i in x], [before_good, after_good], width=0.4,
           color="#4A7EBB", label="정상 발화가 남은 건")
    for i, (b, g) in enumerate(((before_bad, before_good), (after_bad, after_good))):
        a1.text(i - 0.2, b + 1, str(b), ha="center", fontsize=11, fontweight="bold")
        a1.text(i + 0.2, g + 1, str(g), ha="center", fontsize=11, fontweight="bold")
    a1.set_xticks(x); a1.set_xticklabels(["적용 전", "적용 후"])
    a1.set_ylabel("건수")
    a1.set_title(f"무음 {len(bad)}건 / 정상 {len(good)}건", fontsize=11.5)
    a1.legend(fontsize=9); a1.grid(axis="y", alpha=.25)

    # 2) 어디서 걸렸나
    cut_a = sum(1 for r in rows if r["차단"] == "A(min_rms)")
    cut_c = sum(1 for r in rows if r["차단"] == "C(no_speech)")
    passed = sum(1 for r in rows if r["차단"] == "통과")
    a2.bar(["A\nmin_rms", "C\nno_speech", "통과"], [cut_a, cut_c, passed],
           color=["#2a7a63", "#7c6cf5", "#9aa0b5"])
    for i, v in enumerate([cut_a, cut_c, passed]):
        a2.text(i, v + 1, str(v), ha="center", fontsize=11, fontweight="bold")
    a2.set_ylabel("건수")
    a2.set_title("어느 단계에서 걸렸나", fontsize=11.5)
    a2.grid(axis="y", alpha=.25)

    fig.suptitle(f"무음 오인식 대책 검증 — {len(rows)}건", fontsize=13)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20260829")
    ap.add_argument("--device", default="")
    ap.add_argument("--compute", default="")
    args = ap.parse_args()

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    stt = cfg.get("stt", {})
    device = args.device or stt.get("device", "cuda")
    compute = args.compute or ("float16" if device == "cuda" else "int8")
    min_rms = float(stt.get("min_rms") or 0)

    # 실제 코드 경로를 그대로 부른다
    kwargs = laika_core.LaikaCore._stt_kwargs(_CfgOnly(cfg))

    recs = []
    for jf in sorted(DEBUG_DIR.glob(f"{args.date}_*.json")):
        wf = jf.with_suffix(".wav")
        if wf.exists():
            recs.append((jf, wf, json.loads(jf.read_text(encoding="utf-8"))))
    if not recs:
        print(f"{args.date} 기록이 없습니다.")
        return 1

    print("=" * 78)
    print("무음 오인식 대책 검증")
    print(f"  파이썬  : {sys.executable}")
    print(f"  모델    : {stt.get('model_size')} / {device} / {compute}")
    print(f"  min_rms : {min_rms}   (A)")
    print(f"  _stt_kwargs 가 실제로 넘기는 값:")
    for k, v in kwargs.items():
        shown = (v[:40] + "...") if isinstance(v, str) and len(v) > 40 else v
        print(f"    {k:<28} = {shown!r}")
    print(f"  대상    : {len(recs)}건")
    print("=" * 78)

    from faster_whisper import WhisperModel
    model = WhisperModel(stt.get("model_size", "medium"), device=device,
                         compute_type=compute)

    rows, t0 = [], time.time()
    for i, (jf, wf, meta) in enumerate(recs, 1):
        audio, rate = read_wav(wf)
        rms = float(np.sqrt(np.mean(audio.astype("float64") ** 2)))
        lab = label_of(meta)
        row = {"stem": jf.stem, "라벨": lab, "rms": round(rms, 6),
               "이전_전사": (meta.get("전사결과") or ""), "새_전사": "",
               "차단": "", "no_speech": None}

        if min_rms and rms < min_rms:
            row["차단"] = "A(min_rms)"          # Whisper 를 부르지 않는다
        else:
            segs, _ = model.transcribe(audio, language="ko", **kwargs)
            segs = list(segs)
            row["새_전사"] = " ".join(s.text.strip() for s in segs).strip()
            ns = [float(s.no_speech_prob) for s in segs]
            row["no_speech"] = round(max(ns), 4) if ns else None
            row["차단"] = "통과" if row["새_전사"] else "C(no_speech)"

        rows.append(row)
        if i % 20 == 0 or i == len(recs):
            print(f"  {i}/{len(recs)} ... {time.time()-t0:.0f}초")

    good = [r for r in rows if r["라벨"] == "정상"]
    bad = [r for r in rows if r["라벨"] == "무음"]

    print("\n" + "-" * 78)
    leak = [r for r in bad if r["새_전사"].strip()]
    lost = [r for r in good if not r["새_전사"].strip()]
    print(f"1) 무음 {len(bad)}건 중 아직 말이 나오는 건 : {len(leak)}")
    for r in leak:
        print(f"     {r['stem']} rms={r['rms']:.4f} ns={r['no_speech']} -> {r['새_전사']!r}")
    print(f"2) 정상 {len(good)}건 중 사라진 건       : {len(lost)}")
    for r in lost:
        print(f"     {r['stem']} rms={r['rms']:.4f} ns={r['no_speech']} "
              f"(이전: {r['이전_전사']!r})")

    chg = [r for r in good if r["새_전사"].strip() != r["이전_전사"].strip()]
    print(f"3) 정상 {len(good)}건 중 내용이 달라진 건 : {len(chg)}")
    for r in chg[:10]:
        print(f"     이전: {r['이전_전사'][:56]!r}")
        print(f"     이후: {r['새_전사'][:56]!r}")
    if len(chg) > 10:
        print(f"     ... 외 {len(chg)-10}건 (json 참고)")

    print(f"\n차단 단계: A(min_rms) {sum(1 for r in rows if r['차단']=='A(min_rms)')}건 / "
          f"C(no_speech) {sum(1 for r in rows if r['차단']=='C(no_speech)')}건 / "
          f"통과 {sum(1 for r in rows if r['차단']=='통과')}건")

    out = BASE / "laika_verify_stt_fix.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out}")
    plot(rows, BASE / "laika_verify_stt_fix.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
