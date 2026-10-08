# =============================================================================
# 문장별 감정 수신 검증
#
#   Claude API 의 tool use 로 [{emotion, text}, ...] 를 받을 수 있는지,
#   그리고 감정 이름이 vts_emotion_map.json 의 목록을 벗어나지 않는지 확인한다.
#
#   실제로 표정을 바꾸지는 않는다. VTS 에 아무것도 보내지 않는다.
#
#   실행:
#     python laika_emotion_test.py
#     python laika_emotion_test.py --n 5          시행 횟수
#     python laika_emotion_test.py --model claude-sonnet-5
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#
#   감정 목록  : vts_emotion_map.json 의 map 키에서 읽는다. 없으면 초안을 만든다.
#   API 키     : config.json 의 api_key 를 읽는다. 없으면 환경변수 ANTHROPIC_API_KEY.
#   모델       : config.json 의 model. --model 로 덮어쓸 수 있다.
#   시험 문장  : emotion_test_cases.txt 에서 읽는다. 없으면 초안을 만든다.
#
#   즉 감정을 추가하거나 시험 문장을 바꿔도 코드를 고칠 필요가 없다.
# =============================================================================

import argparse
import json
import os
import sys
import time
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = base_dir()
CONFIG_PATH = BASE / "config.json"
MAP_PATH = BASE / "vts_emotion_map.json"
CASES_PATH = BASE / "emotion_test_cases.txt"

# 매핑 파일이 없을 때만 만드는 초안.
# laika_vts.py 가 만드는 것과 같은 형식이라, 어느 쪽을 먼저 돌려도 된다.
MAP_TEMPLATE = {
    "_설명": [
        "라이카가 쓸 감정 이름을 VTS 핫키 이름에 연결합니다.",
        "왼쪽(감정)은 LLM 이 출력할 이름, 오른쪽은 VTS 핫키 이름입니다.",
        "빈 문자열이면 그 감정에서는 표정을 바꾸지 않습니다.",
        "이 파일만 고치면 코드 수정 없이 표정이 바뀝니다.",
    ],
    "_사용가능한_핫키": [],
    "default": "",
    "map": {e: "" for e in
            ["중립", "기쁨", "슬픔", "놀람", "화남", "부끄러움", "졸림", "생각"]},
}

CASES_TEMPLATE = """\
# 감정이 문장마다 달라져야 하는 시험 입력을 한 줄에 하나씩 적는다.
# '#' 로 시작하는 줄과 빈 줄은 무시된다.

호스트: 나 오늘 로또 당첨됐어! 근데 3등이라 5만원밖에 안 돼.
시청자1: 라이카 오늘 목소리 이상한데? 감기 걸렸어?
호스트: 야 너 방금 뭐라고 했냐. 다시 말해봐.
시청자2: 라이카야 잘자~ 나 이제 자러 간다
호스트: 이거 어떻게 생각해? 좀 어려운 문제인데.
"""


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[경고] {Path(path).name} 읽기 실패: {e}")
        return default


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def load_emotions():
    """감정 목록을 매핑 파일에서 읽는다. 스키마 enum 에 그대로 들어간다."""
    m = load_json(MAP_PATH)
    if m is None:
        save_json(MAP_PATH, MAP_TEMPLATE)
        print(f"[매핑] 초안을 만들었습니다: {MAP_PATH.name}")
        m = MAP_TEMPLATE
    emotions = list((m.get("map") or {}).keys())
    if not emotions:
        raise SystemExit(f"{MAP_PATH.name} 의 map 이 비어 있습니다. 감정 이름을 넣어주세요.")
    return emotions, m


def load_cases():
    if not CASES_PATH.exists():
        CASES_PATH.write_text(CASES_TEMPLATE, encoding="utf-8")
        print(f"[입력] 초안을 만들었습니다: {CASES_PATH.name}")
    out = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def load_api_key(cfg):
    key = (cfg.get("api_key") or "").strip()
    if key:
        return key, "config.json"
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return key, "환경변수"
    raise SystemExit("API 키를 찾을 수 없습니다. "
                     "config.json 의 api_key 또는 환경변수 ANTHROPIC_API_KEY 를 설정하세요.")


# ---------------------------------------------------------------- 도구 정의

def build_tool(emotions):
    """감정 목록을 enum 으로 못박은 tool 정의를 만든다.

    enum 을 쓰는 이유: 매핑에 없는 감정 이름이 오면 표정을 못 찾는다.
    목록을 스키마에 넣어두면 그 안에서만 고르게 된다."""
    return {
        "name": "speak",
        "description": ("라이카가 말할 내용을 문장 단위로 나누어 전달한다. "
                        "각 문장마다 그 문장을 말할 때의 감정을 함께 지정한다."),
        "input_schema": {
            "type": "object",
            "properties": {
                "sentences": {
                    "type": "array",
                    "description": "말할 문장들. 순서대로 재생된다.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "emotion": {
                                "type": "string",
                                "enum": emotions,
                                "description": "이 문장을 말할 때의 감정",
                            },
                            "text": {
                                "type": "string",
                                "description": "실제로 말할 문장. 감정 표시를 넣지 않는다.",
                            },
                        },
                        "required": ["emotion", "text"],
                    },
                },
            },
            "required": ["sentences"],
        },
    }


def call_once(client, model, tool, persona, user_text, max_tokens):
    """한 번 호출해 문장 배열을 받는다.

    반환: (문장리스트, 소요초, stop_reason, 폴백텍스트)
    tool 을 못 쓰면 문장리스트가 비고 폴백텍스트가 채워진다."""
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=persona,
        tools=[tool],
        tool_choice={"type": "tool", "name": "speak"},   # 반드시 도구를 쓰게 한다
        messages=[{"role": "user", "content": user_text}],
    )
    el = time.time() - t0

    sentences, fallback = [], ""
    for block in resp.content:
        if block.type == "tool_use" and block.name == "speak":
            sentences = block.input.get("sentences", [])
        elif block.type == "text":
            fallback += block.text

    return sentences, el, resp.stop_reason, fallback.strip()


# ---------------------------------------------------------------- 실행

def run(args):
    cfg = load_json(CONFIG_PATH, {}) or {}
    emotions, emotion_map = load_emotions()
    cases = load_cases()
    key, key_src = load_api_key(cfg)

    model = args.model or cfg.get("model") or "claude-sonnet-4-6"
    max_tokens = int(cfg.get("max_tokens") or 200)
    persona = cfg.get("persona") or "너는 라이카라는 AI 버튜버다. 짧고 자연스럽게 말한다."

    print("=" * 78)
    print("문장별 감정 수신 검증 (tool use)")
    print("=" * 78)
    print(f"  모델        : {model}")
    print(f"  API 키      : {key_src} 에서 읽음")
    print(f"  감정 목록   : {len(emotions)}개  {', '.join(emotions)}")
    print(f"  시험 입력   : {len(cases)}개 x {args.n}회")
    print(f"  max_tokens  : {max_tokens}")

    # 매핑이 비어 있으면 표정은 못 바꾸지만, 형식 검증은 가능하다
    filled = sum(1 for v in (emotion_map.get("map") or {}).values() if v)
    if filled == 0:
        print(f"  [참고] {MAP_PATH.name} 의 핫키 이름이 아직 비어 있습니다.")
        print("         형식 검증에는 지장이 없습니다. VTS 핫키를 만든 뒤 채우면 됩니다.")

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    tool = build_tool(emotions)

    rows = []
    for case in cases:
        for i in range(args.n):
            print(f"\n{'-' * 78}")
            print(f"입력: {case}   (시행 {i + 1}/{args.n})")
            try:
                sents, el, stop, fallback = call_once(
                    client, model, tool, persona, case, max_tokens)
            except Exception as e:
                print(f"  [실패] {type(e).__name__}: {e}")
                rows.append({"입력": case, "시행": i + 1, "성공": False,
                             "오류": f"{type(e).__name__}: {e}",
                             "문장수": 0, "초": 0.0, "이탈": []})
                continue

            if not sents:
                print(f"  [도구 미사용] stop_reason={stop}")
                print(f"  텍스트 응답: {fallback[:80]}")
                rows.append({"입력": case, "시행": i + 1, "성공": False,
                             "오류": f"도구 미사용 (stop_reason={stop})",
                             "문장수": 0, "초": round(el, 2), "이탈": []})
                continue

            # 감정 이름이 매핑 목록을 벗어났는지 확인한다
            off = [s.get("emotion") for s in sents
                   if s.get("emotion") not in emotions]

            print(f"  {el:.2f}초  문장 {len(sents)}개  stop_reason={stop}")
            for s in sents:
                mark = "!" if s.get("emotion") not in emotions else " "
                hotkey = (emotion_map.get("map") or {}).get(s.get("emotion"), "")
                arrow = f"  -> 핫키 '{hotkey}'" if hotkey else "  -> (핫키 미지정)"
                print(f"   {mark} [{s.get('emotion')}] {s.get('text')}{arrow}")
            if off:
                print(f"  [이탈] 매핑에 없는 감정: {off}")

            rows.append({"입력": case, "시행": i + 1, "성공": True, "오류": "",
                         "문장수": len(sents), "초": round(el, 2),
                         "이탈": off,
                         "문장": sents})

    summarize(rows, emotions, model)
    return rows


def summarize(rows, emotions, model):
    total = len(rows)
    ok = [r for r in rows if r["성공"]]
    off = [r for r in ok if r["이탈"]]
    times = [r["초"] for r in ok]
    counts = [r["문장수"] for r in ok]

    print(f"\n{'=' * 78}")
    print("판정")
    print("=" * 78)
    print(f"  전체 시행       : {total}")
    print(f"  도구 사용 성공  : {len(ok)}  ({len(ok) / total * 100:.1f}%)" if total else "")
    print(f"  감정 이름 이탈  : {len(off)}건")
    if times:
        print(f"  응답 시간       : 평균 {sum(times) / len(times):.2f}s  "
              f"최소 {min(times):.2f}s  최대 {max(times):.2f}s")
        print(f"  문장 수         : 평균 {sum(counts) / len(counts):.1f}  "
              f"최소 {min(counts)}  최대 {max(counts)}")

    # 감정별 등장 횟수
    freq = {}
    for r in ok:
        for s in r.get("문장", []):
            e = s.get("emotion")
            freq[e] = freq.get(e, 0) + 1
    if freq:
        print("\n  감정별 등장 횟수")
        for e in emotions:
            print(f"    {e:<8} {freq.get(e, 0)}")
        extra = [e for e in freq if e not in emotions]
        if extra:
            print(f"    [목록 밖] {extra}")

    print("\n  결론")
    if total and len(ok) == total and not off:
        print("    tool use 로 문장별 감정을 안정적으로 받을 수 있습니다. ②안 채택 가능.")
    elif len(ok) < total:
        print("    도구를 쓰지 않는 경우가 있습니다. 폴백 처리가 반드시 필요합니다.")
    if off:
        print("    감정 이름이 목록을 벗어났습니다. 매핑 조회 시 default 로 떨어뜨려야 합니다.")

    out = BASE / "emotion_test_result.json"
    save_json(out, rows)
    print(f"\n→ 저장: {out.name}")
    plot(rows, emotions, model)


def plot(rows, emotions, model):
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

    ok = [r for r in rows if r["성공"]]
    fail = len(rows) - len(ok)

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))

    # 1) 성공/실패
    axes[0].bar(["도구 사용", "도구 미사용/실패"], [len(ok), fail],
                color=["#55a868", "#c44e52"])
    for i, v in enumerate([len(ok), fail]):
        axes[0].text(i, v, str(v), ha="center", va="bottom", fontsize=11)
    axes[0].set_title("tool use 성공 여부", fontsize=11)
    axes[0].grid(axis="y", alpha=0.3)

    # 2) 감정별 등장 횟수 — 한쪽으로 쏠리면 표정이 단조로워진다
    freq = {}
    for r in ok:
        for s in r.get("문장", []):
            e = s.get("emotion")
            freq[e] = freq.get(e, 0) + 1
    labels = emotions + [e for e in freq if e not in emotions]
    vals = [freq.get(e, 0) for e in labels]
    colors = ["#4c72b0" if e in emotions else "#c44e52" for e in labels]
    axes[1].barh(range(len(labels)), vals, color=colors)
    axes[1].set_yticks(range(len(labels)))
    axes[1].set_yticklabels(labels, fontsize=8)
    axes[1].invert_yaxis()
    axes[1].set_title("감정별 등장 횟수\n(붉은색 = 매핑 목록 밖)", fontsize=10)
    axes[1].grid(axis="x", alpha=0.3)
    for i, v in enumerate(vals):
        axes[1].text(v, i, f" {v}", va="center", fontsize=8)

    # 3) 응답 시간 — 방송에서 체감되는 지연
    times = [r["초"] for r in ok]
    if times:
        axes[2].plot(range(1, len(times) + 1), times, "o-", color="#dd8452")
        avg = sum(times) / len(times)
        axes[2].axhline(avg, color="#4c72b0", ls="--", lw=1,
                        label=f"평균 {avg:.2f}s")
        axes[2].legend(fontsize=8)
    axes[2].set_title("응답 시간 (초)", fontsize=11)
    axes[2].set_xlabel("시행")
    axes[2].grid(alpha=0.3)

    fig.suptitle(f"문장별 감정 수신 검증 — {model}", fontsize=13)
    fig.tight_layout()
    out = BASE / "emotion_test.png"
    fig.savefig(out, dpi=110)
    print(f"→ 그래프: {out.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2, help="입력당 반복 횟수 (기본 2)")
    ap.add_argument("--model", help="config.json 의 model 을 덮어쓴다")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
