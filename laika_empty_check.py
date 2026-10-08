# =============================================================================
# 무발화(빈 응답) 원인 확인 — 진단 전용. 방송 코드는 건드리지 않는다.
#
# 확인하려는 것
#   metrics.csv 에서 tool사용=0 / 문장수=1 / tts합계초 빈칸 / 오류 빈칸 인 9건은
#   API 응답에 tool_use 도 text 도 없었다는 뜻이다(근거: laika_core.py 392~408행).
#   그 응답의 stop_reason 과 output_tokens 를 실제로 찍어야 원인을 말할 수 있다.
#
# 재는 것
#   stop_reason        : max_tokens 면 잘린 것, tool_use/end_turn 이면 다른 원인
#   usage.output_tokens: max_tokens 상한에 붙었는지
#   content 블록 종류   : tool_use 유무, input 키 목록
#   sentences 개수     : 0 이면 방송에서 무발화가 된다
#
# 실행 (laika 가상환경에서)
#   conda deactivate          <- 켜져 있는 다른 환경을 먼저 빠져나온다
#   conda activate laika
#   python laika_empty_check.py
#
#   conda activate laika 하면 작업 폴더로 자동 이동한다
#   (인수인계 문서 2-2절, laika_cd.ps1 훅)
#   시작할 때 sys.executable 을 찍으므로 어느 환경으로 돌았는지 출력으로 확인된다
#
#   --trials 3               한 조합당 시행 횟수
#   --max-tokens 200,400,800 상한을 바꿔가며 비교
#   --history                직전 세션 대화를 붙여서 재현 (기본은 안 붙임)
#
# 결과: laika_empty_check.json / laika_empty_check.png
# =============================================================================

import argparse
import json
import sys
import time
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = base_dir()

# metrics.csv 에서 무발화가 난 시각의 입력. 화면 로그에서 확인된 3건 +
# 길이만 남아 있는 건은 같은 길이의 대체 문구로 채웠다(표시해 둠).
CASES = [
    ("자기소개 해봐",        "22:56:40 무발화 (입력길이 7, 로그 확인)"),
    ("자신을 소개해봐",      "22:56:56 무발화 (입력길이 8, 로그 확인)"),
    ("진행해",              "22:57:07 무발화 (입력길이 3, 로그 확인)"),
    ("너 자신에 대해 설명해봐", "22:55:03 무발화 (입력길이 12, 원문 미확인 — 대체 문구)"),
    ("오늘 날씨 좋다",       "대조군 (정상 동작하던 길이대)"),
]


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_api_key(cfg):
    key = (cfg.get("api_key") or "").strip()
    if key:
        return key, "config.json"
    import os
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return key, "환경변수 ANTHROPIC_API_KEY"


def build_history(cfg):
    """직전 세션 대화를 그대로 붙인다. 방송 때와 같은 조건으로 맞추기 위한 것."""
    try:
        from laika_history import History
    except Exception as e:
        print(f"  히스토리 불러오기 실패, 빈 상태로 진행합니다: {e}")
        return []
    h = History(cfg)
    try:
        h.resume()
    except Exception as e:
        print(f"  resume 실패, 빈 상태로 진행합니다: {e}")
        return []
    msgs = h.messages()
    print(f"  히스토리: 메시지 {len(msgs)}개")
    return msgs


def one_call(client, model, persona, tool, history, user_text, max_tokens):
    """한 번 호출하고 판정에 필요한 값만 뽑는다."""
    row = {
        "입력": user_text, "max_tokens": max_tokens,
        "stop_reason": "", "input_tokens": None, "output_tokens": None,
        "블록": [], "tool_use있음": False, "tool_input_키": [],
        "문장수": 0, "text길이": 0, "초": None, "오류": "",
    }
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=persona,
            tools=[tool],
            tool_choice={"type": "tool", "name": "speak"},
            messages=history + [{"role": "user", "content": f"[호스트] {user_text}"}],
        )
    except Exception as e:
        row["초"] = round(time.time() - t0, 3)
        row["오류"] = f"{type(e).__name__}: {e}"
        return row

    row["초"] = round(time.time() - t0, 3)
    row["stop_reason"] = getattr(resp, "stop_reason", "") or ""
    usage = getattr(resp, "usage", None)
    if usage is not None:
        row["input_tokens"] = getattr(usage, "input_tokens", None)
        row["output_tokens"] = getattr(usage, "output_tokens", None)

    text = ""
    for b in resp.content:
        bt = getattr(b, "type", "")
        row["블록"].append(bt)
        if bt == "tool_use" and getattr(b, "name", "") == "speak":
            row["tool_use있음"] = True
            inp = b.input if isinstance(b.input, dict) else {}
            row["tool_input_키"] = sorted(inp.keys())
            row["문장수"] = len(inp.get("sentences") or [])
        elif bt == "text":
            text += b.text
    row["text길이"] = len(text.strip())
    return row


def is_silent(row):
    """방송에서 무발화가 되는 조건. laika_core.py 398~408행과 같은 판정."""
    return (not row["오류"]) and row["문장수"] == 0 and row["text길이"] == 0


def plot(rows, model, out_png):
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

    done = [r for r in rows if not r["오류"]]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6))

    # 1) stop_reason 분포
    ax = axes[0]
    reasons = {}
    for r in done:
        reasons[r["stop_reason"] or "(없음)"] = reasons.get(r["stop_reason"] or "(없음)", 0) + 1
    keys = list(reasons)
    ax.bar(keys, [reasons[k] for k in keys], color="#4A7EBB")
    for i, k in enumerate(keys):
        ax.text(i, reasons[k], str(reasons[k]), ha="center", va="bottom", fontsize=10)
    ax.set_title(f"stop_reason 분포 (n={len(done)})", fontsize=11)
    ax.grid(axis="y", alpha=.25)

    # 2) output_tokens — 상한선에 붙는지
    ax = axes[1]
    for silent, color, label in ((False, "#4A7EBB", "정상"), (True, "#C0392B", "무발화")):
        xs = [r["max_tokens"] for r in done if is_silent(r) is silent]
        ys = [r["output_tokens"] for r in done if is_silent(r) is silent]
        if xs:
            ax.scatter(xs, ys, s=45, c=color, label=f"{label} n={len(xs)}",
                       marker="x" if silent else "o")
    for mt in sorted({r["max_tokens"] for r in done}):
        ax.axhline(mt, ls="--", lw=1, c="#999")
        ax.text(mt, mt, f" 상한 {mt}", fontsize=8, color="#666", va="bottom")
    ax.set_xlabel("max_tokens"); ax.set_ylabel("output_tokens")
    ax.set_title("출력 토큰이 상한에 붙었는가", fontsize=11)
    ax.legend(fontsize=8); ax.grid(alpha=.25)

    # 3) max_tokens 별 무발화율
    ax = axes[2]
    mts = sorted({r["max_tokens"] for r in done})
    rate, ns = [], []
    for mt in mts:
        g = [r for r in done if r["max_tokens"] == mt]
        ns.append(len(g))
        rate.append(100.0 * sum(1 for r in g if is_silent(r)) / len(g) if g else 0)
    ax.bar([str(m) for m in mts], rate, color="#C0392B")
    for i, v in enumerate(rate):
        ax.text(i, v, f"{v:.0f}%  (n={ns[i]})", ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_xlabel("max_tokens"); ax.set_ylabel("무발화율 %")
    ax.set_title("상한을 올리면 사라지는가", fontsize=11)
    ax.grid(axis="y", alpha=.25)

    fig.suptitle(f"무발화 원인 확인 — {model}", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3, help="한 조합당 시행 횟수")
    ap.add_argument("--max-tokens", default="", help="쉼표로 구분. 기본은 config 값과 그 2배, 4배")
    ap.add_argument("--history", action="store_true", help="직전 세션 대화를 붙여서 재현")
    ap.add_argument("--model", default="", help="config.json 의 model 을 덮어쓴다")
    args = ap.parse_args()

    cfg = load_json(BASE / "config.json", {}) or {}
    model = args.model or cfg.get("model") or "claude-sonnet-4-6"
    persona = cfg.get("persona") or ""
    cfg_mt = int(cfg.get("max_tokens") or 200)

    if args.max_tokens.strip():
        mts = [int(x) for x in args.max_tokens.split(",") if x.strip()]
    else:
        mts = [cfg_mt, cfg_mt * 2, cfg_mt * 4]

    emap = load_json(BASE / "vts_emotion_map.json", {}) or {}
    emotions = list((emap.get("map") or {}).keys())
    default = emap.get("default") or (emotions[0] if emotions else "중립")
    if not emotions:
        print("vts_emotion_map.json 의 map 이 비어 있습니다. 중단합니다.")
        return 1

    from laika_emotion import build_tool
    tool = build_tool(emotions, default)

    key, key_src = load_api_key(cfg)
    if not key:
        print("API 키를 찾지 못했습니다 (config.json 의 api_key / 환경변수).")
        return 1

    import anthropic
    client = anthropic.Anthropic(api_key=key)

    history = build_history(cfg) if args.history else []

    total = len(CASES) * len(mts) * args.trials
    print("=" * 78)
    print("무발화(빈 응답) 원인 확인")
    print(f"  파이썬      : {sys.executable}")
    print(f"  모델        : {model}")
    print(f"  키 출처     : {key_src}")
    print(f"  페르소나    : {len(persona)}자")
    print(f"  감정 목록   : {len(emotions)}개 {emotions}")
    print(f"  max_tokens  : {mts}  (config 값 {cfg_mt})")
    print(f"  히스토리    : {'붙임 %d개' % len(history) if history else '없음'}")
    print(f"  호출 수     : {total}")
    print("=" * 78)

    rows = []
    for mt in mts:
        for text, note in CASES:
            for i in range(args.trials):
                r = one_call(client, model, persona, tool, history, text, mt)
                r["비고"] = note
                r["시행"] = i + 1
                rows.append(r)
                mark = "무발화" if is_silent(r) else ("오류" if r["오류"] else "정상 ")
                print(f"[{mark}] mt={mt:<4} {text[:12]:<14} "
                      f"stop={r['stop_reason']:<12} out={r['output_tokens']} "
                      f"문장={r['문장수']} text={r['text길이']}자 {r['초']}초"
                      + (f"  {r['오류'][:60]}" if r["오류"] else ""))
                time.sleep(0.3)

    print("-" * 78)
    done = [r for r in rows if not r["오류"]]
    silent = [r for r in done if is_silent(r)]
    print(f"호출 성공 {len(done)}건 / 무발화 {len(silent)}건")
    for mt in mts:
        g = [r for r in done if r["max_tokens"] == mt]
        s = [r for r in g if is_silent(r)]
        avg = sum(r["output_tokens"] or 0 for r in g) / len(g) if g else 0
        print(f"  max_tokens={mt:<5} 무발화 {len(s)}/{len(g)}  "
              f"output_tokens 평균 {avg:.1f}  "
              f"stop_reason {sorted({r['stop_reason'] for r in g})}")

    out_json = BASE / "laika_empty_check.json"
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out_json}")
    plot(rows, model, BASE / "laika_empty_check.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
