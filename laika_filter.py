# =============================================================================
# 언어 필터
#
# 왜 이렇게 만드나 (2026-09-16, 세션 기록 194건 실측)
#   단어 목록을 기존 기록에 그대로 돌려본 결과:
#     입력  실제 욕설 1건(호스트 본인) / 오탐 0건
#     출력  실제 욕설 0건 / 오탐 8건   <- '패' x6, '미쳤' x2
#   '패' 는 유희왕 용어다. 출력에서 차단을 켜면 멀쩡한 중계가 망가진다.
#   그래서 기본값은 이렇다.
#     입력 : 걸리면 버린다        (block_input  = True)
#     출력 : 기록만 하고 통과시킨다 (block_output = False)
#   방송을 몇 번 돌려 filter_log.csv 에 오탐이 쌓이면 word_allow.txt 로 빼고,
#   그때 block_output 을 켠다.
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   금지어  : word_filter.txt
#   예외어  : word_allow.txt        (금지어와 겹치면 예외가 이긴다)
#   동작    : config.json 의 filter 항목
#   이 파일들만 고치면 코드 수정 없이 바뀐다.
#
# 단독 실행하면 sessions/ 기록에 지금 설정을 그대로 돌려보고 그래프를 낸다.
#   python laika_filter.py              드라이런 + 그래프
#   python laika_filter.py --text "..."  한 문장만 검사
# =============================================================================

import csv
import json
import re
import sys
import threading
import time
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "block_input": True,      # 걸린 입력은 라이카에게 안 넘긴다
    "block_output": False,    # 출력은 기록만. 오탐이 정리된 뒤에 켠다
    "replace_with": "...",    # block_output 이 True 일 때 대체할 문자열
    "log_file": "filter_log.csv",
    "words_file": "word_filter.txt",
    "allow_file": "word_allow.txt",
}

WORDS_TEMPLATE = """\
# 라이카가 거를 단어. 한 줄에 하나.
#
# 변형은 | 로 같이 적는다.  예)  씨발|시발|ㅅㅂ|시1발
# 글자 사이의 공백·마침표·별표 등은 자동으로 무시하므로
# '시 발', '시.발', '시*발' 은 따로 안 적어도 걸린다.
#
# '#' 로 시작하는 줄과 빈 줄은 무시된다.
# 이 파일만 고치면 코드 수정 없이 목록이 바뀐다.
#
# 주의: 도메인 단어와 겹치지 않는지 확인할 것.
#       '패' 를 넣으면 유희왕 중계의 '패에 추가' 가 전부 걸린다.
#       겹치는 것은 word_allow.txt 에 예외로 넣는다.

씨발|시발|씨빨|ㅅㅂ
좆|존나|ㅈㄴ
병신|ㅂㅅ
지랄
개새끼|개새
니미
엠창
"""

ALLOW_TEMPLATE = """\
# 예외어. 여기 걸린 구간은 금지어에 맞아도 통과시킨다.
# 한 줄에 하나.
#
# 실측(2026-09-16, 세션 194건)에서 오탐이 난 것들:
#   '패'   -> 유희왕 용어. 덱/패/묘지
#   '미쳤' -> '비주얼이 미쳤는데요' 처럼 칭찬으로 쓰임
#
# 금지어 목록에 그 단어를 안 넣었으면 여기에도 안 적어도 된다.
# filter_log.csv 를 보고 오탐이 보이면 여기에 추가한다.
"""

# 글자 사이에 끼어들 수 있는 것들. 여기까지는 자동으로 무시한다.
SEP = r"[\s\.\,\-\_\*\~\^\|\/\\]*"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _read_lines(path: Path, template: str):
    if not path.exists():
        path.write_text(template, encoding="utf-8")
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


class WordFilter:
    """입력과 출력에서 금지어를 찾는다. 실패해도 방송을 막지 않는다."""

    def __init__(self, cfg=None, log=None):
        raw = (cfg or {}).get("filter") or {}
        self.cfg = dict(DEFAULTS)
        self.cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        self.log = log
        self._lock = threading.Lock()

        b = base_dir()
        self.words_path = b / self.cfg["words_file"]
        self.allow_path = b / self.cfg["allow_file"]
        self.log_path = b / self.cfg["log_file"]
        self.reload()

    def _say(self, kind, msg):
        if self.log:
            self.log(kind, msg)

    # ------------------------------------------------------------ 목록

    def reload(self):
        """방송 중에 파일을 고쳐도 반영되게 한다."""
        try:
            lines = _read_lines(self.words_path, WORDS_TEMPLATE)
            allow = _read_lines(self.allow_path, ALLOW_TEMPLATE)
        except Exception as e:
            self._say("error", f"[필터] 목록 읽기 실패, 필터를 끕니다: {e}")
            self.rules, self.allow = [], []
            return

        self.rules = []
        for line in lines:
            # 한 줄에 '씨발|시발|ㅅㅂ' 처럼 변형을 같이 적을 수 있다.
            variants = [v.strip() for v in line.split("|") if v.strip()]
            if not variants:
                continue
            name = variants[0]
            pats = []
            for v in variants:
                # 글자 사이에 공백/마침표/별표 등이 끼어도 잡는다
                pats.append(SEP.join(re.escape(ch) for ch in v))
            self.rules.append((name, re.compile("|".join(pats))))
        self.allow = [a for a in allow if a]

    # ------------------------------------------------------------ 검사

    def _allowed(self, text, start, end):
        """걸린 구간이 예외어 안에 들어 있으면 통과시킨다."""
        for a in self.allow:
            i = text.find(a)
            while i != -1:
                if i <= start and end <= i + len(a):
                    return True
                i = text.find(a, i + 1)
        return False

    def find(self, text):
        """걸린 것들을 돌려준다. [(단어이름, 시작, 끝, 실제로걸린문자열), ...]"""
        if not self.cfg["enabled"] or not text:
            return []
        hits = []
        for name, pat in self.rules:
            for m in pat.finditer(text):
                if self._allowed(text, m.start(), m.end()):
                    continue
                hits.append((name, m.start(), m.end(), m.group(0)))
        hits.sort(key=lambda h: h[1])
        return hits

    # ------------------------------------------------------------ 적용

    def check_input(self, who, text):
        """입력용. 반환: (통과여부, 걸린목록)

        block_input 이 True 면 걸린 입력은 라이카에게 넘기지 않는다."""
        hits = self.find(text)
        if not hits:
            return True, []
        self._write("입력", who, text, hits,
                    "버림" if self.cfg["block_input"] else "통과")
        if self.cfg["block_input"]:
            self._say("system",
                      f"[필터] 입력을 버렸습니다 ({who}): "
                      f"{', '.join(h[0] for h in hits)}")
            return False, hits
        return True, hits

    def check_output(self, text):
        """출력용. 반환: (내보낼 문자열, 걸린목록)

        block_output 이 False 면 원문 그대로 돌려주고 기록만 남긴다.
        실측에서 출력 오탐이 8건이었기 때문에 기본값이 False 다."""
        hits = self.find(text)
        if not hits:
            return text, []

        if not self.cfg["block_output"]:
            self._write("출력", "라이카", text, hits, "기록만")
            self._say("system",
                      f"[필터] 출력에 걸린 말이 있습니다(통과시킴): "
                      f"{', '.join(h[0] for h in hits)}")
            return text, hits

        out, last = [], 0
        for _, s, e, _raw in hits:
            out.append(text[last:s]); out.append(self.cfg["replace_with"]); last = e
        out.append(text[last:])
        new = "".join(out)
        self._write("출력", "라이카", text, hits, f"대체 -> {new[:40]}")
        self._say("system",
                  f"[필터] 출력을 대체했습니다: {', '.join(h[0] for h in hits)}")
        return new, hits

    # ------------------------------------------------------------ 기록

    COLUMNS = ["시각", "방향", "누가", "걸린단어", "실제문자열", "동작", "원문"]

    def _write(self, direction, who, text, hits, action):
        """어디서 몇 건이 걸렸는지 남긴다. 오탐을 골라내는 근거가 된다."""
        try:
            with self._lock:
                new = not self.log_path.exists()
                with open(self.log_path, "a", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(self.COLUMNS)
                    w.writerow([
                        time.strftime("%Y-%m-%d %H:%M:%S"), direction, who,
                        " ".join(h[0] for h in hits),
                        " ".join(h[3] for h in hits),
                        action, text,
                    ])
        except Exception as e:
            self._say("error", f"[필터] 기록 실패: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 드라이런

def dryrun(argv):
    """지금 설정을 sessions/ 기록에 그대로 돌려본다. 방송 코드는 안 건드린다."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="이 문장 하나만 검사")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args(argv)

    b = base_dir()
    cfg = {}
    try:
        cfg = json.loads((b / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    wf = WordFilter(cfg, log=lambda k, m: print(f"  [{k}] {m}"))

    print("=" * 74)
    print("언어 필터 드라이런")
    print(f"  금지어 파일 : {wf.words_path.name}  규칙 {len(wf.rules)}개")
    print(f"  예외어 파일 : {wf.allow_path.name}  {len(wf.allow)}개")
    print(f"  입력 차단   : {wf.cfg['block_input']}")
    print(f"  출력 차단   : {wf.cfg['block_output']}  <- False 면 기록만")
    print("=" * 74)

    if args.text:
        hits = wf.find(args.text)
        print(f"\n입력: {args.text!r}")
        print(f"걸린 것 {len(hits)}건")
        for n, s, e, raw in hits:
            print(f"  {n}  위치 {s}~{e}  실제 {raw!r}")
        return 0

    users, asst = [], []
    for f in sorted((b / "sessions").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for k in ("chat", "vision"):
            for t in (d.get(k) or []):
                if isinstance(t.get("user"), str):
                    users.append(t["user"])
                if isinstance(t.get("assistant"), str):
                    asst.append(t["assistant"])

    if not users and not asst:
        print("sessions/ 에 검사할 기록이 없습니다.")
        return 1

    def scan(items, label):
        rows = []
        for txt in items:
            for n, s, e, raw in wf.find(txt):
                rows.append((n, raw, txt))
        print(f"\n[{label}] {len(items)}건 중 {len(rows)}건이 걸림")
        cnt = {}
        for n, _, _ in rows:
            cnt[n] = cnt.get(n, 0) + 1
        for n, c in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"    {n} x{c}")
        for n, raw, txt in rows[:10]:
            print(f"    - ({n}/{raw}) {txt[:66]}")
        return rows, cnt

    ru, cu = scan(users, "입력 — 호스트 음성 + 시청자 채팅")
    ra, ca = scan(asst, "출력 — 라이카가 실제로 말한 문장")

    print("\n  걸린 것이 멀쩡한 말이면 word_allow.txt 에 넣으십시오.")
    print("  출력 차단은 오탐이 0 이 된 뒤에 config 의 filter.block_output 으로 켭니다.")

    if not args.no_plot:
        plot(cu, ca, len(users), len(asst), b / "filter_dryrun.png")
    return 0


def plot(cu, ca, nu, na, out_png):
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

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, (cnt, title, n) in zip(axes, [(cu, "입력", nu), (ca, "출력", na)]):
        if cnt:
            ws = sorted(cnt, key=lambda w: -cnt[w])
            ax.bar(ws, [cnt[w] for w in ws], color="#C0392B")
            for i, w in enumerate(ws):
                ax.text(i, cnt[w], str(cnt[w]), ha="center", va="bottom",
                        fontsize=11, fontweight="bold")
            ax.set_ylim(0, max(cnt.values()) + 1)
        else:
            ax.text(.5, .5, "걸린 것 없음", ha="center", va="center",
                    fontsize=15, color="#4ec9a5", fontweight="bold")
            ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(f"{title} — {n}건 검사", fontsize=12)
        ax.set_ylabel("걸린 건수"); ax.grid(axis="y", alpha=.25)
    fig.suptitle("언어 필터 드라이런 — 지금 목록을 기존 기록에 돌려본 결과", fontsize=13)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"\n그래프: {out_png}")


if __name__ == "__main__":
    sys.exit(dryrun(sys.argv[1:]))
