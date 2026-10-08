# =============================================================================
# A 시험 - 페르소나에 "영어 단어 금지" 를 넣으면 영어가 줄어드는가
#
# 왜 (2026-09-22 방송)
#   라이카가 "곰은 원래 좀 hibernation 하잖아요" 라고 말했고,
#   laika_tts_norm.py 241행이 사전에 없는 영어를 한 글자씩 읽어
#   "에이치아이비이아르엔에이티아이오엔" 이 되었다.
#   페르소나에는 "숫자와 영문 약어는 한글로" 만 있고 일반 영단어는 막지 않는다.
#
# 무엇을 재나  (같은 입력으로 두 조건)
#   0  지금 지시 그대로
#   1  "영어 단어 금지" 한 줄을 더 넣음
#   출력 방식은 앞으로 쓸 텍스트 인라인([감정] 문장, 스트리밍 없음)으로 맞춘다.
#   영어 판정: 알파벳 2자 이상 덩어리 중 tts_dict.json 의 약어(LP, ATK 등)가 아닌 것.
#
# 실행
#   conda deactivate
#   conda activate laika
#   python laika_english_llm_test.py
#     --trials 3   입력당 조건당 시행 횟수
#
# 결과: laika_english_llm_test.json / .png
# =============================================================================

import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent

# 영어가 나오기 쉬운 입력. 마지막 두 개는 일부러 영어를 유도한다.
CASES = [
    "호스트: 곰은 겨울에 뭐 해?",
    "호스트: 너 게임 뭐 좋아해?",
    "호스트: 오늘 마스터듀얼 랭크전 할 건데 응원해줘",
    "호스트: 오늘 컨디션 어때?",
    "시청자1: 요즘 유행하는 밈 알아?",
    "시청자2: 라이카 영어 할 줄 알아?",
]

NO_ENGLISH = ("영어 단어나 영어 표현을 쓰지 않는다. 외래어가 꼭 필요하면 한글로 "
              "소리나는 대로 쓴다. hibernation 이 아니라 동면, game 이 아니라 게임, "
              "Master Duel 이 아니라 마스터 듀얼.")


def load_json(p):
    return json.loads((BASE / p).read_text(encoding="utf-8"))


def english_tokens(text, allowed):
    """약어 사전에 없는 영어 덩어리를 돌려준다."""
    out = []
    for m in re.finditer(r"[A-Za-z]{2,}", text):
        w = m.group(0)
        if w.lower() not in allowed:
            out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    args = ap.parse_args()

    cfg = load_json("config.json")
    emo = list(load_json("vts_emotion_map.json")["map"])
    allowed = {k.lower() for k in (load_json("tts_dict.json").get("약어") or {})}
    from laika_emotion_text_test import text_system, parse_lines

    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])

    conds = {
        "0": cfg["persona"],
        "1": cfg["persona"] + "\n- " + NO_ENGLISH,
    }
    print("=" * 76)
    print("A 시험 - 영어 단어 금지 지시")
    print(f"  모델 {cfg.get('model')} / 입력 {len(CASES)}개 x 조건 2 x {args.trials}회")
    print(f"  약어로 인정하는 것: {sorted(allowed)}")
    print("=" * 76)

    rows = []
    for text in CASES:
        for tr in range(1, args.trials + 1):
            for c, persona in conds.items():
                sysmsg = text_system(persona, emo, "중립")
                t0 = time.time()
                try:
                    resp = client.messages.create(
                        model=cfg.get("model"), max_tokens=cfg.get("max_tokens", 500),
                        system=sysmsg,
                        messages=[{"role": "user", "content": text}])
                    out = "".join(b.text for b in resp.content
                                  if getattr(b, "type", "") == "text")
                    err = ""
                except Exception as e:
                    out, err = "", f"{type(e).__name__}: {e}"
                sents = parse_lines(out)
                eng = [w for s in sents for w in english_tokens(s["text"], allowed)]
                rows.append({"조건": c, "입력": text, "시행": tr, "원문": out,
                             "문장수": len(sents), "영어": eng, "오류": err,
                             "초": round(time.time() - t0, 3)})
                print(f"  [{c}] {text[:20]:<22} 문장 {len(sents)}  영어 {eng if eng else '-'}"
                      + (f"  {err[:40]}" if err else ""))
                time.sleep(0.3)

    # 요약 전에 저장한다
    (BASE / "laika_english_llm_test.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "-" * 76)
    summ = {}
    for c in conds:
        g = [r for r in rows if r["조건"] == c and not r["오류"]]
        ns = sum(r["문장수"] for r in g)
        hit_resp = sum(1 for r in g if r["영어"])
        words = [w for r in g for w in r["영어"]]
        summ[c] = (len(g), hit_resp, ns, len(words))
        print(f"  조건 {c} ({'지금' if c == '0' else '영어 금지'})  응답 {len(g)}  "
              f"영어가 섞인 응답 {hit_resp} ({hit_resp/len(g)*100 if g else 0:.0f}%)  "
              f"영어 단어 {len(words)}개  {sorted(set(words))}")
    print(f"\n기록: {BASE / 'laika_english_llm_test.json'}")

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
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.8))
        labs = ["지금 지시", "영어 금지 추가"]
        rate = [summ[c][1] / summ[c][0] * 100 if summ[c][0] else 0 for c in conds]
        a1.bar(labs, rate, color=["#C0392B", "#4ec9a5"])
        for i, v in enumerate(rate):
            a1.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=12, fontweight="bold")
        a1.set_ylim(0, 105); a1.set_ylabel("%")
        a1.set_title("영어가 섞인 응답 비율", fontsize=12); a1.grid(axis="y", alpha=.25)
        per = {}
        for r in rows:
            if r["오류"]:
                continue
            per.setdefault((r["입력"], r["조건"]), []).append(1 if r["영어"] else 0)
        x = range(len(CASES))
        for off, c, col in ((-.2, "0", "#C0392B"), (.2, "1", "#4ec9a5")):
            v = [sum(per.get((t, c), [0])) for t in CASES]
            a2.bar([i + off for i in x], v, width=.4, color=col, label=labs[int(c)])
        a2.set_xticks(list(x))
        a2.set_xticklabels([t.split(":", 1)[1].strip()[:9] for t in CASES], fontsize=8.5)
        a2.set_ylabel(f"영어 섞인 횟수 (최대 {args.trials})")
        a2.set_title("입력별", fontsize=12); a2.legend(fontsize=9); a2.grid(axis="y", alpha=.25)
        fig.suptitle("A 시험 - 영어 단어 금지 지시의 효과", fontsize=13)
        fig.tight_layout(); fig.savefig(BASE / "laika_english_llm_test.png", dpi=130)
        print(f"그래프: {BASE / 'laika_english_llm_test.png'}")
    except Exception as e:
        print(f"그래프 생략: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
