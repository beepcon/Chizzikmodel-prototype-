# =============================================================================
# A + C 적용 확인
#   A : 페르소나에 영어 금지 한 줄  (config.json / laika_gui.py DEFAULT_CFG)
#   C : tts_dict.json 의 "영단어" 사전  (laika_tts_norm.py 가 읽음)
#
# 서버 없이 돈다. 글자만 확인한다.
#   1) 페르소나에 금지 문장이 있는가
#   2) 사전에 있는 영단어가 한글로 바뀌는가
#   3) 사전에 없는 영단어가 남아서 한 글자씩 읽히는가 (남으면 사전에 추가할 후보)
#   4) 경계 확인 : game 사전이 gamer 를 잘라먹지 않는가
# 결과: laika_ac_check.json / laika_ac_check.png
# =============================================================================
import json, re, sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
from laika_tts_norm import TTSNormalizer, _ALPHA

CASES = [
    "곰은 원래 좀 hibernation 하잖아요.",
    "오늘 game 한 판 할까요?",
    "Master Duel 랭크전 시작해요!",
    "그거 완전 OK 예요, ATK 2500 이면 충분해요.",
    "진짜 nice 하네요, good job!",
    "우앙4시님 LP 8000 남았어요.",
    "저 gamer 아니고 그냥 구경꾼이에요.",            # 경계 확인
    "그 boss 패턴은 진짜 어려워요.",
    "저 kubernetes 같은 건 몰라요.",                 # 사전에 없는 단어
]

SPELLED = set(_ALPHA.values())


def spelled_count(s):
    """한 글자씩 읽힌 흔적을 센다. 에이치아이비이... 같은 연속 음절."""
    hits = re.findall(r"(?:" + "|".join(sorted(SPELLED, key=len, reverse=True)) + r"){4,}", s)
    return hits


def main():
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    norm = TTSNormalizer()
    eng = (norm.data.get("영단어") or {})

    a_ok = "영어 단어나 영어 표현을 쓰지 마라" in cfg["persona"]
    print("=" * 76)
    print(f"A  페르소나 영어 금지 문장 : {'있음' if a_ok else '없음'}")
    print(f"C  영단어 사전 항목 수      : {len(eng)}")
    print("=" * 76)

    rows = []
    for i, t in enumerate(CASES, 1):
        out = norm.normalize(t)
        left = re.findall(r"[A-Za-z]{2,}", out)      # 안 바뀌고 남은 영어
        sp = spelled_count(out)
        rows.append({"번호": i, "원문": t, "변환": out,
                     "남은영어": left, "철자읽기": sp})
        mark = "OK " if not sp else "주의"
        print(f"[{i}] {mark} {t}\n      -> {out}")
        if sp:
            print(f"      철자로 읽힌 부분 {sp}")

    (BASE / "laika_ac_check.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    bad = [r for r in rows if r["철자읽기"]]
    print("-" * 76)
    print(f"문장 {len(rows)}개 중 철자로 읽히는 문장 {len(bad)}개")
    for r in bad:
        print(f"  [{r['번호']}] {r['원문']}  -> 사전 추가 후보")
    print(f"기록: {BASE / 'laika_ac_check.json'}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        for cand in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
            if any(f.name == cand for f in font_manager.fontManager.ttflist):
                matplotlib.rcParams["font.family"] = cand
                break
        matplotlib.rcParams["axes.unicode_minus"] = False

        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
        ok = len(rows) - len(bad)
        a1.bar(["정상", "철자 읽기"], [ok, len(bad)], color=["#4c9f70", "#c0504d"])
        for x, v in enumerate([ok, len(bad)]):
            a1.text(x, v, str(v), ha="center", va="bottom")
        a1.set_title(f"A+C 적용 후 ({len(rows)}문장)"); a1.grid(axis="y", alpha=.25)

        labs = [f"{r['번호']}" for r in rows]
        vals = [len(r["철자읽기"]) for r in rows]
        a2.bar(labs, vals, color=["#c0504d" if v else "#4c9f70" for v in vals])
        a2.set_title("문장별 철자 읽기 덩어리 수"); a2.set_xlabel("문장 번호")
        a2.set_ylim(0, max(vals + [1]) + 0.5); a2.grid(axis="y", alpha=.25)

        fig.suptitle(f"A+C 확인   영단어 사전 {len(eng)}개", fontsize=13)
        fig.tight_layout(); fig.savefig(BASE / "laika_ac_check.png", dpi=130)
        print(f"그림: {BASE / 'laika_ac_check.png'}")
    except Exception as e:
        print(f"그림 생략: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
