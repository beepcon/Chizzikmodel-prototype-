# =============================================================================
# 응답 시간 확인
#
#   방송 중 쌓인 metrics.csv 를 표와 그래프로 본다.
#
#     python laika_metrics_view.py
#     python laika_metrics_view.py --last 100      최근 100건만
#
#   보는 법
#     첫소리까지초 = LLM + 첫 문장 TTS. 시청자가 체감하는 지연이다.
#     tool사용 0 이 섞여 있으면 도구를 못 쓴 경우가 있다는 뜻이다.
#     감정이탈 이 계속 나오면 vts_emotion_map.json 에 그 감정을 추가하면 된다.
# =============================================================================

import argparse
import csv
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = base_dir()


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals_sorted = sorted(vals)
    mid = len(vals_sorted) // 2
    median = (vals_sorted[mid] if len(vals_sorted) % 2
              else (vals_sorted[mid - 1] + vals_sorted[mid]) / 2)
    return {"n": len(vals), "평균": sum(vals) / len(vals), "중앙": median,
            "최소": min(vals), "최대": max(vals)}


def report(rows):
    print(f"\n{'=' * 78}")
    print(f"응답 시간 기록  —  {len(rows)}건")
    print("=" * 78)

    if not rows:
        print("  기록이 없습니다. 방송을 켜고 라이카가 말한 뒤 다시 실행하세요.")
        return

    errs = [r for r in rows if r.get("오류")]
    tool_off = [r for r in rows if r.get("tool사용") == "0"]
    off_emo = sum(int(r.get("감정이탈") or 0) for r in rows)
    vision = [r for r in rows if r.get("종류") == "vision"]

    print(f"  오류          : {len(errs)}건")
    print(f"  도구 미사용   : {len(tool_off)}건"
          + ("  ← 폴백으로 처리됨" if tool_off else ""))
    print(f"  감정 이탈     : {off_emo}개 문장"
          + ("  ← default 로 처리됨" if off_emo else ""))
    print(f"  화면 반응     : {len(vision)}건 / 채팅 {len(rows) - len(vision)}건")

    cols = [("llm초", "LLM 호출"), ("첫문장tts초", "첫 문장 TTS"),
            ("첫소리까지초", "첫 소리까지 (체감 지연)"),
            ("tts합계초", "TTS 전체"), ("표정합계초", "표정 전환"),
            ("전체초", "전체")]

    print(f"\n  {'구간':<24}{'건수':>5}{'평균':>9}{'중앙':>9}{'최소':>9}{'최대':>9}")
    print("  " + "-" * 66)
    for key, label in cols:
        s = stat([fnum(r.get(key)) for r in rows])
        if s is None:
            print(f"  {label:<24}{'-':>5}")
            continue
        print(f"  {label:<24}{s['n']:>5}{s['평균']:>9.2f}{s['중앙']:>9.2f}"
              f"{s['최소']:>9.2f}{s['최대']:>9.2f}")

    # tool use 가 실제로 느린지 — 같은 조건끼리 비교해야 의미가 있다
    on = [fnum(r.get("llm초")) for r in rows if r.get("tool사용") == "1"]
    off = [fnum(r.get("llm초")) for r in rows if r.get("tool사용") == "0"]
    s_on, s_off = stat(on), stat(off)
    print("\n  [tool use 비용]")
    if s_on:
        print(f"    도구 사용   : {s_on['n']}건  평균 {s_on['평균']:.2f}s")
    if s_off:
        print(f"    도구 미사용 : {s_off['n']}건  평균 {s_off['평균']:.2f}s")
        if s_on:
            print(f"    차이        : {s_on['평균'] - s_off['평균']:+.2f}s")
    else:
        print("    도구 미사용 표본이 없어 비교할 수 없습니다.")


def plot(rows):
    if not rows:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError:
        print("[그래프] 건너뜀: matplotlib 이 없습니다. (pip install matplotlib)")
        return

    for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
        if any(f.name == cand for f in font_manager.fontManager.ttflist):
            matplotlib.rcParams["font.family"] = cand
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(2, 2, figsize=(15, 8))

    # 1) 첫 소리까지 — 시청자가 체감하는 지연
    ax = axes[0][0]
    ys = [fnum(r.get("첫소리까지초")) for r in rows]
    xs = [i for i, y in enumerate(ys, 1) if y is not None]
    ys = [y for y in ys if y is not None]
    if ys:
        ax.plot(xs, ys, "o-", ms=3, lw=1, color="#dd8452")
        avg = sum(ys) / len(ys)
        ax.axhline(avg, color="#4c72b0", ls="--", lw=1, label=f"평균 {avg:.2f}s")
        ax.axhline(3.0, color="red", ls=":", lw=1, label="목표 3초")
        ax.legend(fontsize=8)
    ax.set_title("첫 소리까지 걸린 시간 (체감 지연)", fontsize=11)
    ax.set_xlabel("응답 순서")
    ax.grid(alpha=0.3)

    # 2) 구간별 평균 — 어디가 느린지
    ax = axes[0][1]
    keys = [("llm초", "LLM"), ("첫문장tts초", "첫 TTS"),
            ("tts합계초", "TTS 전체"), ("표정합계초", "표정")]
    labels, vals = [], []
    for k, lab in keys:
        s = stat([fnum(r.get(k)) for r in rows])
        if s:
            labels.append(lab)
            vals.append(s["평균"])
    if vals:
        bars = ax.bar(labels, vals, color="#4c72b0")
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=9)
    ax.set_title("구간별 평균 시간 (초)", fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    # 3) tool use 사용 여부와 감정 이탈
    ax = axes[1][0]
    on = sum(1 for r in rows if r.get("tool사용") == "1")
    off = len(rows) - on
    off_emo = sum(int(r.get("감정이탈") or 0) for r in rows)
    errs = sum(1 for r in rows if r.get("오류"))
    names = ["도구 사용", "도구 미사용", "감정 이탈\n(문장)", "오류"]
    vs = [on, off, off_emo, errs]
    ax.bar(names, vs, color=["#55a868", "#c44e52", "#dd8452", "#c44e52"])
    for i, v in enumerate(vs):
        ax.text(i, v, str(v), ha="center", va="bottom", fontsize=10)
    ax.set_title("동작 상태", fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    # 4) 채팅 vs 화면 — 화면 응답이 더 느린지
    ax = axes[1][1]
    for kind, color in (("chat", "#4c72b0"), ("vision", "#dd8452")):
        ys = [fnum(r.get("llm초")) for r in rows if r.get("종류") == kind]
        ys = [y for y in ys if y is not None]
        if ys:
            ax.plot(range(1, len(ys) + 1), ys, "o-", ms=3, lw=1,
                    color=color, label=f"{kind} (평균 {sum(ys)/len(ys):.2f}s)")
    ax.legend(fontsize=8)
    ax.set_title("LLM 호출 시간 — 채팅 vs 화면", fontsize=11)
    ax.set_xlabel("각 종류별 순서")
    ax.grid(alpha=0.3)

    fig.suptitle(f"라이카 응답 시간 — {len(rows)}건", fontsize=13)
    fig.tight_layout()
    out = BASE / "metrics_report.png"
    fig.savefig(out, dpi=110)
    print(f"\n→ 그래프: {out.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="metrics.csv")
    ap.add_argument("--last", type=int, help="최근 N건만 본다")
    args = ap.parse_args()

    path = BASE / args.file
    if not path.exists():
        print(f"파일이 없습니다: {path}")
        print("방송을 한 번 켜고 라이카가 말한 뒤 다시 실행하세요.")
        return

    rows = load(path)
    if args.last:
        rows = rows[-args.last:]
    report(rows)
    plot(rows)


if __name__ == "__main__":
    main()
