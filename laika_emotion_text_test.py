# =============================================================================
# 도구 없이도 표정이 되는가 - tool_use 와 텍스트 인라인 표기 비교
#
# 왜 필요한가 (laika_stream_diag.json, 2026-09-17)
#   tool_choice 로 도구를 강제하면 응답이 조각으로 안 오고 한 번에 온다.
#     T1 SDK + tool_use    : 28~29조각이 0.002초 안에.  첫 조각 4.63~4.85초
#     T2 SDK + 도구 없음    : 50~52조각이 2.3~2.4초에 퍼짐. 첫 조각 1.33~1.45초
#     T3 httpx + tool_use  : 1개 오고 32개가 몰림.       첫 조각 1.21~1.29초
#   도구를 빼면 첫 소리를 3초 넘게 당길 수 있다. 문제는 감정을 어떻게 받느냐다.
#
#   대안: 도구 대신 "[기쁨] 문장" 처럼 텍스트에 감정을 적게 한다.
#   되는지는 재봐야 안다. 기존 도구 방식 실측(emotion_test_result.json, 10회)은
#     도구 사용 10/10, 문장 21개, 감정 이름 이탈 3개(장난 1, 장난기 2), 평균 3.55초
#
# 무엇을 재나  (emotion_test_cases.txt 의 같은 입력으로)
#   A  tool_use 강제        - 지금 방식. 비교 기준
#   B  텍스트 인라인 + 스트리밍 - "[감정] 문장" 을 줄 단위로 받는다
#
#   두 조건에서 이것을 센다.
#     형식 준수율   B 에서 문장이 "[감정] " 으로 시작한 비율
#     감정 이탈     vts_emotion_map.json 의 map 에 없는 이름이 나온 횟수
#     첫 문장까지   B 는 스트리밍이므로 첫 문장이 완성된 시각을 잰다
#                  (이 값이 있어야 LLM+TTS 합산이 정확해진다)
#     전체까지
#
# 실행
#   conda deactivate
#   conda activate laika
#   python laika_emotion_text_test.py
#
#   --trials 2        입력당 시행 횟수
#   --only B          한 조건만
#
# 결과: laika_emotion_text_test.json / laika_emotion_text_test.png
# =============================================================================

import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
CASES_PATH = BASE / "emotion_test_cases.txt"

# "[기쁨] 안녕하세요" 를 잡는다. 대괄호 안에 공백이 있어도 받는다.
LINE_RE = re.compile(r"^\s*\[\s*([^\]]{1,12})\s*\]\s*(.+?)\s*$")


def load_cfg():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def load_cases():
    out = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def emotions_of(cfg):
    from laika_emotion import EmotionMap
    e = EmotionMap(cfg)
    return e.emotions, e.default


def text_system(persona, emotions, default):
    """도구 대신 형식을 지시한다. 페르소나는 그대로 두고 뒤에 덧붙인다.

    1차 측정(2026-09-18, 21문장)에서 나온 세 가지를 지시에 넣었다.
      - 첫 줄에만 대괄호를 빠뜨림 2건  -> "첫 줄부터 예외 없이" 를 명시
      - 같은 문장을 두 번 뱉음 1건      -> 중복 금지를 명시
      - 숫자를 그대로 씀 2건            -> 페르소나에 있는 규칙을 여기 다시 적음
        (형식 지시를 페르소나 뒤에 붙였더니 숫자 규칙이 밀린 것으로 보인다.
         밀렸다는 것 자체는 확인 안 됐고, 다시 적어 재측정으로 확인한다)"""
    names = ", ".join(emotions)
    return persona + (
        "\n\n[출력 형식]\n"
        "한 줄에 한 문장씩 쓴다. 각 줄은 대괄호 안의 감정으로 시작한다.\n"
        "  [감정] 문장\n"
        "첫 줄부터 마지막 줄까지 예외 없이 대괄호로 시작한다. "
        "대괄호 없는 줄을 앞에 두지 않는다.\n"
        f"감정은 반드시 다음 중에서만 고른다: {names}\n"
        f"감정 변화가 뚜렷하지 않으면 '{default}' 를 쓴다.\n"
        "대괄호와 감정 이름 말고 다른 표시나 번호는 넣지 않는다.\n"
        "같은 내용을 두 줄에 반복해서 쓰지 않는다.\n"
        "문장 안의 숫자와 영문 약어는 소리나는 대로 한글로 쓴다. "
        "5만원이 아니라 오만원, 3등이 아니라 삼등, LP 가 아니라 라이프포인트."
    )


def parse_lines(text):
    """[감정] 문장 형식을 문장 목록으로 바꾼다.

    형식을 벗어난 줄도 버리지 않는다. 감정 없이 문장만 담는다."""
    out = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = LINE_RE.match(raw)
        if m:
            out.append({"emotion": m.group(1).strip(), "text": m.group(2).strip(),
                        "형식": True})
        else:
            out.append({"emotion": None, "text": raw, "형식": False})
    return out


def run_tool(client, cfg, emotions, default, user_text):
    """A 조건. laika_core._llm_reply 와 같은 방식."""
    from laika_emotion import build_tool
    t0 = time.time()
    resp = client.messages.create(
        model=cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=cfg.get("max_tokens", 200),
        system=cfg["persona"],
        tools=[build_tool(emotions, default)],
        tool_choice={"type": "tool", "name": "speak"},
        messages=[{"role": "user", "content": user_text}],
    )
    el = time.time() - t0
    sents = []
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use" and b.name == "speak":
            for s in (b.input or {}).get("sentences") or []:
                sents.append({"emotion": s.get("emotion"), "text": s.get("text", ""),
                              "형식": True})
    u = getattr(resp, "usage", None)
    return {"조건": "A", "문장": sents, "첫문장까지": round(el, 3),
            "전체까지": round(el, 3), "TTFT": None,
            "output_tokens": getattr(u, "output_tokens", None) if u else None,
            "stop_reason": getattr(resp, "stop_reason", "")}


def run_text(client, cfg, emotions, default, user_text):
    """B 조건. 스트리밍으로 받으며 첫 줄이 끝나는 순간을 잡는다."""
    sysmsg = text_system(cfg["persona"], emotions, default)
    buf, ttft, first_at = "", None, None
    t0 = time.time()
    with client.messages.stream(
        model=cfg.get("model", "claude-sonnet-4-6"),
        max_tokens=cfg.get("max_tokens", 200),
        system=sysmsg,
        messages=[{"role": "user", "content": user_text}],
    ) as s:
        for ev in s:
            if getattr(ev, "type", "") == "content_block_delta":
                piece = getattr(getattr(ev, "delta", None), "text", None)
                if not piece:
                    continue
                if ttft is None:
                    ttft = time.time() - t0
                buf += piece
                # 줄바꿈이 오면 그 줄은 완성된 것이다
                if first_at is None and "\n" in buf:
                    head = buf.split("\n", 1)[0].strip()
                    if head:
                        first_at = time.time() - t0
        final = s.get_final_message()
    el = time.time() - t0
    u = getattr(final, "usage", None)
    return {"조건": "B", "문장": parse_lines(buf), "원문": buf,
            "첫문장까지": round(first_at, 3) if first_at else round(el, 3),
            "전체까지": round(el, 3),
            "TTFT": round(ttft, 3) if ttft else None,
            "output_tokens": getattr(u, "output_tokens", None) if u else None,
            "stop_reason": getattr(final, "stop_reason", "")}


def plot(rows, emotions, out_png):
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
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15.5, 5)) 

    conds = ["A", "B"]
    lab = {"A": "A\ntool_use 강제", "B": "B\n텍스트 + 스트리밍"}
    first = []
    for c in conds:
        v = [r["첫문장까지"] for r in ok if r["조건"] == c]
        first.append(sum(v)/len(v) if v else 0)
    a1.bar([lab[c] for c in conds], first, color=["#C0392B", "#4ec9a5"])
    for i, v in enumerate(first):
        a1.text(i, v + .05, f"{v:.2f}초", ha="center", fontsize=11, fontweight="bold")
    a1.set_ylabel("초"); a1.set_title("첫 문장이 준비되기까지", fontsize=11.5)
    a1.grid(axis="y", alpha=.25)

    tot, esc = [], []
    for c in conds:
        ss = [s for r in ok if r["조건"] == c for s in r["문장"]]
        tot.append(len(ss))
        esc.append(sum(1 for s in ss if s.get("emotion") not in emotions))
    x = range(len(conds))
    a2.bar([i - .2 for i in x], tot, width=.4, color="#4A7EBB", label="문장 수")
    a2.bar([i + .2 for i in x], esc, width=.4, color="#E0A030", label="감정 이탈")
    for i in x:
        a2.text(i - .2, tot[i], str(tot[i]), ha="center", va="bottom",
                fontsize=10, fontweight="bold")
        a2.text(i + .2, esc[i], str(esc[i]), ha="center", va="bottom",
                fontsize=10, fontweight="bold")
    a2.set_xticks(list(x)); a2.set_xticklabels([lab[c] for c in conds])
    a2.set_ylabel("개"); a2.set_title("감정 이름이 목록을 벗어난 수", fontsize=11.5)
    a2.legend(fontsize=9); a2.grid(axis="y", alpha=.25)

    bs = [s for r in ok if r["조건"] == "B" for s in r["문장"]]
    good = sum(1 for s in bs if s.get("형식"))
    bad = len(bs) - good
    if bs:
        a3.pie([good, bad], labels=[f"형식 맞음\n{good}", f"형식 벗어남\n{bad}"],
               colors=["#4ec9a5", "#C0392B"], autopct="%1.0f%%",
               textprops={"fontsize": 10})
        a3.set_title(f"B 의 형식 준수 ({len(bs)}문장)", fontsize=11.5)
    else:
        a3.text(.5, .5, "B 결과 없음", ha="center", va="center")
        a3.set_xticks([]); a3.set_yticks([])

    fig.suptitle("도구 없이도 표정이 되는가 — 같은 입력으로 비교", fontsize=13.5)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--only", default="", help="A 또는 B 만")
    args = ap.parse_args()

    cfg = load_cfg()
    if not (cfg.get("api_key") or "").strip():
        print("config.json 에 api_key 가 없습니다.")
        return 1
    cases = load_cases()
    emotions, default = emotions_of(cfg)

    conds = [c for c in ("A", "B") if (not args.only or args.only.upper() == c)]
    print("=" * 78)
    print("도구 없이도 표정이 되는가")
    print(f"  모델     : {cfg.get('model')}  max_tokens={cfg.get('max_tokens')}")
    print(f"  감정 목록 : {emotions}  (기본 {default})")
    print(f"  입력     : {len(cases)}개 x {args.trials}회 x 조건 {conds}")
    print(f"  비교 기준 : emotion_test_result.json (도구 10/10, 문장 21, 이탈 3)")
    print("=" * 78)

    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])

    rows = []
    for text in cases:
        for tr in range(1, args.trials + 1):
            for c in conds:
                try:
                    r = (run_tool if c == "A" else run_text)(
                        client, cfg, emotions, default, text)
                except Exception as e:
                    rows.append({"조건": c, "입력": text, "시행": tr,
                                 "오류": f"{type(e).__name__}: {e}"})
                    print(f"  [{c}] 실패: {type(e).__name__}: {e}")
                    continue
                r.update({"입력": text, "시행": tr, "오류": ""})
                rows.append(r)
                off = [s.get("emotion") for s in r["문장"]
                       if s.get("emotion") not in emotions]
                bad = sum(1 for s in r["문장"] if not s.get("형식"))
                print(f"  [{c}] {text[:16]:<18} 첫문장 {r['첫문장까지']:>6}s  "
                      f"전체 {r['전체까지']:>6}s  문장 {len(r['문장'])}  "
                      f"이탈 {off if off else 0}"
                      + (f"  형식벗어남 {bad}" if c == "B" else ""))
                time.sleep(0.3)

    # 요약을 찍기 전에 먼저 저장한다.
    # 앞서 요약 print 에서 터져 호출을 다 하고도 자료를 잃은 적이 있다.
    out = BASE / "laika_emotion_text_test.json"
    try:
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기록: {out}")
    except Exception as e:
        print(f"\n기록 실패: {type(e).__name__}: {e}")

    ok = [r for r in rows if not r.get("오류")]
    print("-" * 78)
    for c in conds:
        g = [r for r in ok if r["조건"] == c]
        if not g:
            print(f"{c}: 결과 없음"); continue
        ss = [s for r in g for s in r["문장"]]
        # 형식을 벗어난 줄은 emotion 이 None 이다.
        # None 과 문자열을 같이 정렬하면 TypeError 가 난다. 나눠서 센다.
        off = [s.get("emotion") for s in ss if s.get("emotion") not in emotions]
        off_named = sorted({e for e in off if e is not None})
        off_none = sum(1 for e in off if e is None)
        bad = sum(1 for s in ss if not s.get("형식"))
        f1 = sum(r["첫문장까지"] for r in g) / len(g)
        ft = sum(r["전체까지"] for r in g) / len(g)
        print(f"{c}  호출 {len(g)}  문장 {len(ss)}")
        print(f"    첫 문장까지 평균 {f1:.3f}초 / 전체까지 평균 {ft:.3f}초")
        print(f"    감정 이탈 {len(off)}/{len(ss)}"
              + (f"  이름 벗어남 {off_named}" if off_named else "")
              + (f"  감정 없음 {off_none}개" if off_none else ""))
        if c == "B":
            print(f"    형식 벗어난 문장 {bad}/{len(ss)}"
                  f"  ({(len(ss)-bad)/len(ss)*100:.0f}% 준수)" if ss else "")
            tt = [r["TTFT"] for r in g if r.get("TTFT")]
            if tt:
                print(f"    TTFT 평균 {sum(tt)/len(tt):.3f}초")

    a = [r for r in ok if r["조건"] == "A"]
    b = [r for r in ok if r["조건"] == "B"]
    if a and b:
        fa = sum(r["첫문장까지"] for r in a) / len(a)
        fb = sum(r["첫문장까지"] for r in b) / len(b)
        print(f"\n  첫 문장까지: A {fa:.3f}초 -> B {fb:.3f}초  ({fa-fb:+.3f}초)")

    plot(rows, emotions, BASE / "laika_emotion_text_test.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
