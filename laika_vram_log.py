# =============================================================================
# VRAM 사용량 기록 - 로컬 LLM 을 얹을 여유가 있는가
#
# 왜 필요한가
#   GPU 는 RTX 3090 (24GB) 로 확인됐지만, 그 24GB 를 이미 여럿이 나눠 쓴다.
#     - faster-whisper medium (config.json stt.device = cuda)
#     - GPT-SoVITS (127.0.0.1:9880)
#     - VTube Studio (Live2D 렌더링)
#     - 게임 (config.json vision.window_title = "masterduel")
#   2026-08-28 에 transcribe 가 MemoryError: bad allocation 으로 실패한 적이 있다.
#   24GB 인데 실패했다는 것은 그 시점 실제 여유가 작았다는 뜻이다.
#   로컬 LLM 을 넣을 수 있는지는 "총량" 이 아니라 "방송 중 최저 여유" 가 정한다.
#
# 무엇을 재나
#   2초마다 total / used / free 와 프로세스별 사용량을 남긴다.
#   방송을 켜고 평소처럼 쓰다가 Ctrl+C 로 멈추면 표와 그래프가 나온다.
#
# 실행
#   conda deactivate
#   conda activate laika
#   pip install nvidia-ml-py
#   python laika_vram_log.py
#
#   --interval 2.0     샘플 간격(초)
#   --minutes 0        이 시간이 지나면 자동 종료 (0 이면 Ctrl+C 까지)
#   --out vram.csv     기록 파일
#
# 결과: laika_vram_log.csv / laika_vram_log.png
# =============================================================================

import argparse
import csv
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
MB = 1024 * 1024


def open_nvml():
    try:
        import pynvml
    except ImportError:
        print("pynvml 이 없습니다. 이 환경에 설치하세요:")
        print(f'  "{sys.executable}" -m pip install nvidia-ml-py')
        return None, None
    try:
        pynvml.nvmlInit()
    except Exception as e:
        print(f"NVML 초기화 실패: {type(e).__name__}: {e}")
        print("드라이버가 없거나 GPU 를 못 찾는 경우입니다.")
        return None, None
    return pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)


def proc_map(pynvml, h):
    """GPU 를 쓰는 프로세스와 사용량(MB). 권한이 없으면 빈 dict."""
    out = {}
    for fn in ("nvmlDeviceGetComputeRunningProcesses_v3",
               "nvmlDeviceGetComputeRunningProcesses",
               "nvmlDeviceGetGraphicsRunningProcesses_v3",
               "nvmlDeviceGetGraphicsRunningProcesses"):
        f = getattr(pynvml, fn, None)
        if f is None:
            continue
        try:
            for p in f(h):
                try:
                    name = pynvml.nvmlSystemGetProcessName(p.pid)
                    name = name.decode() if isinstance(name, bytes) else name
                    name = Path(name).name
                except Exception:
                    name = f"pid{p.pid}"
                used = getattr(p, "usedGpuMemory", None)
                out[name] = out.get(name, 0) + (used // MB if used else 0)
        except Exception:
            pass
    return out


def plot(rows, out_png, total_mb):
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

    t = [r["초"] for r in rows]
    used = [r["used_MB"] for r in rows]
    free = [r["free_MB"] for r in rows]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5))
    a1.plot(t, used, lw=1.4, color="#C0392B", label="사용 중")
    a1.plot(t, free, lw=1.4, color="#4ec9a5", label="남은 것")
    a1.axhline(total_mb, ls="--", lw=1, c="#666")
    a1.text(0, total_mb, f" 총 {total_mb} MB", fontsize=9, color="#555", va="bottom")
    lo = min(free)
    a1.axhline(lo, ls=":", lw=1.2, c="#2a7a63")
    a1.text(t[-1], lo, f" 최저 여유 {lo} MB ", fontsize=9, color="#2a7a63",
            ha="right", va="bottom")
    a1.set_xlabel("경과 (초)"); a1.set_ylabel("MB")
    a1.set_title("방송 중 VRAM", fontsize=11.5)
    a1.legend(fontsize=9); a1.grid(alpha=.25)

    # 프로세스별 최대 사용량
    names = {}
    for r in rows:
        for k, v in r["_procs"].items():
            names[k] = max(names.get(k, 0), v)
    top = sorted(names.items(), key=lambda x: -x[1])[:8]
    if top:
        a2.barh([k for k, _ in top][::-1], [v for _, v in top][::-1],
                color="#7c6cf5")
        for i, (k, v) in enumerate(top[::-1]):
            a2.text(v, i, f" {v}", va="center", fontsize=9)
        a2.set_xlabel("최대 사용량 (MB)")
    else:
        a2.text(.5, .5, "프로세스별 사용량을 못 읽었습니다\n(권한 또는 드라이버 제한)",
                ha="center", va="center", fontsize=11, color="#888")
        a2.set_xticks([]); a2.set_yticks([])
    a2.set_title("무엇이 쓰고 있나", fontsize=11.5)
    a2.grid(axis="x", alpha=.25)

    headroom = total_mb - max(used)
    fig.suptitle(f"VRAM 기록 — 최저 여유 {min(free)} MB / 로컬 LLM 이 쓸 수 있는 여유 "
                 f"약 {headroom} MB", fontsize=13)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="0 이면 Ctrl+C 까지 계속")
    ap.add_argument("--out", default="laika_vram_log.csv")
    args = ap.parse_args()

    pynvml, h = open_nvml()
    if pynvml is None:
        return 1

    name = pynvml.nvmlDeviceGetName(h)
    name = name.decode() if isinstance(name, bytes) else name
    total = pynvml.nvmlDeviceGetMemoryInfo(h).total // MB
    print("=" * 74)
    print("VRAM 기록")
    print(f"  GPU      : {name}")
    print(f"  총 VRAM  : {total} MB")
    print(f"  간격     : {args.interval}초")
    print(f"  종료     : {'Ctrl+C' if args.minutes <= 0 else f'{args.minutes}분 뒤'}")
    print("=" * 74)
    print("  방송을 켜고 평소처럼 쓰십시오. 말도 걸고, 표정도 바꾸고,")
    print("  게임도 켜 두어야 실제 최저 여유가 나옵니다.\n")

    rows, t0 = [], time.time()
    try:
        while True:
            m = pynvml.nvmlDeviceGetMemoryInfo(h)
            procs = proc_map(pynvml, h)
            el = time.time() - t0
            row = {"초": round(el, 1),
                   "시각": time.strftime("%H:%M:%S"),
                   "total_MB": total,
                   "used_MB": m.used // MB,
                   "free_MB": m.free // MB,
                   "_procs": procs}
            rows.append(row)
            top = sorted(procs.items(), key=lambda x: -x[1])[:3]
            print(f"  {row['시각']}  사용 {row['used_MB']:>6} MB / "
                  f"여유 {row['free_MB']:>6} MB   "
                  + "  ".join(f"{k} {v}MB" for k, v in top))
            if args.minutes > 0 and el >= args.minutes * 60:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  (중단)")

    if not rows:
        print("기록된 것이 없습니다.")
        return 1

    free = [r["free_MB"] for r in rows]
    used = [r["used_MB"] for r in rows]
    print("\n" + "-" * 74)
    print(f"샘플 {len(rows)}개 / {rows[-1]['초']:.0f}초")
    print(f"  사용   최소 {min(used)} MB / 최대 {max(used)} MB / 평균 {sum(used)//len(used)} MB")
    print(f"  여유   최소 {min(free)} MB / 최대 {max(free)} MB")
    print(f"\n  로컬 LLM 이 쓸 수 있는 여유 = 총 {total} - 최대 사용 {max(used)} "
          f"= 약 {total - max(used)} MB")
    print("  (모델 가중치 + KV 캐시 + 여유분이 이 안에 들어가야 한다)")

    names = {}
    for r in rows:
        for k, v in r["_procs"].items():
            names[k] = max(names.get(k, 0), v)
    if names:
        print("\n  프로세스별 최대 사용량")
        for k, v in sorted(names.items(), key=lambda x: -x[1])[:10]:
            print(f"    {k:<34} {v:>6} MB")
    else:
        print("\n  프로세스별 사용량은 못 읽었습니다 (권한 또는 드라이버 제한).")

    out = BASE / args.out
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["초", "시각", "total_MB", "used_MB", "free_MB", "프로세스"])
        for r in rows:
            w.writerow([r["초"], r["시각"], r["total_MB"], r["used_MB"], r["free_MB"],
                        "; ".join(f"{k}={v}" for k, v in
                                  sorted(r["_procs"].items(), key=lambda x: -x[1]))])
    print(f"\n기록: {out}")
    plot(rows, BASE / "laika_vram_log.png", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
