# =============================================================================
# 응답 지연 측정 - 첫 소리까지의 시간을 줄일 수 있는가
#
# 왜 필요한가 (metrics.csv 2026-08-29 방송 314건)
#   첫 소리까지 평균 8.31초 = LLM 4.26초(51%) + 첫 문장 TTS 4.05초(49%)
#   둘 다 "다 받고 나서" 시작하는 구조다.
#     laika_core.py 388행 : messages.create(...)   <- 논스트리밍
#     laika_core.py 533행 : "streaming_mode": False
#   문장당 출력 토큰이 중앙 67개인데 2~3문장을 다 받은 뒤에야 TTS 를 부른다.
#
#   회귀(laika_empty_check.json 45건): llm초 = 0.018 + 27.3ms x 토큰 (R2=0.520)
#   R2 가 0.52 라 네트워크 왕복 시간을 분리하지 못했다. 그래서 TTFT 를 직접 잰다.
#
# 무엇을 재나  (같은 입력으로 조건을 바꿔가며 "첫 소리까지" 를 잰다)
#   A   : 지금 방식        논스트리밍 LLM      + 논스트리밍 TTS
#   B   : 스트리밍 LLM     (TTFT / 첫 문장 완성) + 논스트리밍 TTS
#   C1  : 스트리밍 LLM     + 스트리밍 TTS mode 1  (Best Quality, Slowest)
#   C2  : 스트리밍 LLM     + 스트리밍 TTS mode 2  (Medium Quality)
#   C3  : 스트리밍 LLM     + 스트리밍 TTS mode 3  (Lower Quality, Faster)
#   모드 설명 근거: api_v2.py 44행
#
#   C1~C3 는 B 가 만든 첫 문장을 그대로 써서 TTS 만 비교한다.
#   같은 문장이라야 공정하고, LLM 호출도 아낀다.
#
# 소리는 재생하지 않는다. 받은 오디오는 버린다. 방송 코드는 건드리지 않는다.
#
# 실행 (GPT-SoVITS 서버가 떠 있어야 한다. 방송은 꺼도 된다)
#   conda deactivate
#   conda activate laika
#   python laika_latency_check.py
#
#   --trials 2      한 조건당 시행 횟수
#   --history       직전 세션 대화를 붙여 방송 조건에 맞춘다
#   --cases 2       앞에서 N 개 케이스만
#
# 결과: laika_latency_check.json / laika_latency_check.png
# =============================================================================

import argparse
import json
import sys
import time
from pathlib import Path

import laika_core

BASE = Path(__file__).parent

# 실제 방송 로그(debug_audio 2026-08-29)에서 가져온 입력 + 대조군
CASES = [
    ("자기소개 해봐", "짧은 요청 (08-29 로그)"),
    ("니가 방송을 하면서 말을 잘한다는 걸 보여줄만한 게 뭐가 있을까?", "긴 요청 (08-29 로그)"),
    ("오늘 날씨 좋다", "짧은 잡담 (대조군)"),
]


def load_cfg():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def build_kwargs(cfg, history, user_text):
    """laika_core._llm_reply 가 만드는 것과 같은 인자."""
    from laika_emotion import EmotionMap, build_tool
    emap = EmotionMap(cfg)
    kw = {
        "model": cfg.get("model", "claude-sonnet-4-6"),
        "max_tokens": cfg.get("max_tokens", 200),
        "system": cfg["persona"],
        "messages": history + [{"role": "user", "content": f"[호스트] {user_text}"}],
    }
    if emap.cfg["enabled"] and emap.is_configured() and emap.emotions:
        kw["tools"] = [build_tool(emap.emotions, emap.default)]
        kw["tool_choice"] = {"type": "tool", "name": "speak"}
    return kw


def first_sentence(buf):
    """스트리밍 중 누적된 부분 JSON 에서 첫 문장이 완성됐는지 본다.

    문장 객체는 {"emotion": ..., "text": ...} 로 중첩이 없다.
    바깥 객체부터 순서대로 잘라 보며 json.loads 가 되는 첫 조각을 찾는다."""
    i = buf.find("{")
    while i != -1:
        j = buf.find("}", i)
        if j == -1:
            return None
        try:
            o = json.loads(buf[i:j + 1])
            if isinstance(o, dict) and o.get("text"):
                return o
        except Exception:
            pass
        i = buf.find("{", i + 1)
    return None


def llm_nonstream(client, kw):
    """A 조건. 전체 응답이 끝나야 문장을 얻는다."""
    t0 = time.time()
    resp = client.messages.create(**kw)
    done = time.time() - t0
    sents = []
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use" and b.name == "speak":
            sents = list((b.input or {}).get("sentences") or [])
    u = getattr(resp, "usage", None)
    return {"첫문장까지": round(done, 3), "전체까지": round(done, 3),
            "TTFT": None, "문장수": len(sents),
            "output_tokens": getattr(u, "output_tokens", None) if u else None,
            "첫문장": (sents[0].get("text", "") if sents else "")}


def llm_stream(client, kw):
    """B 조건. 첫 문장이 완성되는 순간을 잡는다."""
    t0 = time.time()
    ttft = None
    first_at = None
    first = None
    buf = ""
    with client.messages.stream(**kw) as s:
        for ev in s:
            et = getattr(ev, "type", "")
            if et == "content_block_delta":
                d = getattr(ev, "delta", None)
                pj = getattr(d, "partial_json", None)
                if pj is None:
                    pj = getattr(d, "text", None)
                if pj:
                    if ttft is None:
                        ttft = time.time() - t0
                    buf += pj
                    if first is None:
                        first = first_sentence(buf)
                        if first is not None:
                            first_at = time.time() - t0
        final = s.get_final_message()
    done = time.time() - t0
    u = getattr(final, "usage", None)
    return {"첫문장까지": round(first_at, 3) if first_at else None,
            "전체까지": round(done, 3),
            "TTFT": round(ttft, 3) if ttft else None,
            "문장수": None,
            "output_tokens": getattr(u, "output_tokens", None) if u else None,
            "첫문장": (first or {}).get("text", "")}


def tts_once(cfg, text, streaming_mode):
    """첫 오디오 바이트까지 걸린 시간. 받은 소리는 버린다.

    streaming_mode=0 이면 wav 가 통째로 올 때까지 기다린다(지금 방식).
    1/2/3 이면 조각으로 오므로 첫 조각까지만 잰다."""
    import requests
    # laika_core.py 218/224행과 같은 클래스를 쓴다. 인자는 log 뿐이다.
    from laika_tts_norm import TTSNormalizer
    try:
        spoken = TTSNormalizer().normalize(text)
    except Exception:
        spoken = text

    payload = {
        "text": spoken,
        "text_lang": "ko",
        "ref_audio_path": cfg["ref_audio"],
        "prompt_text": cfg["ref_text"],
        "prompt_lang": "ko",
        "aux_ref_audio_paths": [a for a in cfg.get("aux_ref_audio", []) if a],
        "streaming_mode": streaming_mode,
        **cfg["tts"],
    }
    t0 = time.time()
    if not streaming_mode:
        r = requests.post(cfg["sovits_url"], json=payload, timeout=120)
        r.raise_for_status()
        return round(time.time() - t0, 3), len(r.content)
    r = requests.post(cfg["sovits_url"], json=payload, stream=True, timeout=120)
    r.raise_for_status()
    first_at, got = None, 0
    for chunk in r.iter_content(chunk_size=4096):
        if not chunk:
            continue
        if first_at is None:
            first_at = time.time() - t0
        got += len(chunk)
        if first_at is not None:
            break              # 첫 조각까지만 재고 끊는다
    r.close()
    return (round(first_at, 3) if first_at else None), got


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

    order = ["A", "B", "C1", "C2", "C3"]
    label = {"A": "A\n지금 방식", "B": "B\nLLM 스트리밍",
             "C1": "C1\n+TTS 1", "C2": "C2\n+TTS 2", "C3": "C3\n+TTS 3"}

    def avg(cond, key):
        v = [r[key] for r in rows if r["조건"] == cond and r.get(key) is not None]
        return sum(v) / len(v) if v else 0.0

    llm = [avg(c, "첫문장까지") for c in order]
    tts = [avg(c, "tts초") for c in order]
    tot = [a + b for a, b in zip(llm, tts)]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14.5, 5.4))
    a1.bar(range(len(order)), llm, color="#7c6cf5", label="LLM 첫 문장까지")
    a1.bar(range(len(order)), tts, bottom=llm, color="#4ec9a5", label="TTS 첫 소리까지")
    for i, (l, t) in enumerate(zip(llm, tts)):
        if l: a1.text(i, l / 2, f"{l:.2f}", ha="center", va="center",
                      fontsize=10, color="white", fontweight="bold")
        if t: a1.text(i, l + t / 2, f"{t:.2f}", ha="center", va="center",
                      fontsize=10, color="white", fontweight="bold")
        a1.text(i, l + t + 0.12, f"{l+t:.2f}초", ha="center", fontsize=10.5,
                fontweight="bold")
    a1.axhline(8.31, ls="--", lw=1.3, c="#C0392B")
    a1.text(len(order) - 0.4, 8.45, "08-29 방송 실측 8.31초", fontsize=9.5,
            color="#C0392B", ha="right")
    a1.set_xticks(range(len(order))); a1.set_xticklabels([label[c] for c in order])
    a1.set_ylabel("초"); a1.set_title("첫 소리까지", fontsize=12)
    a1.legend(fontsize=9.5); a1.grid(axis="y", alpha=.25)

    ttft = [r["TTFT"] for r in rows if r["조건"] == "B" and r.get("TTFT")]
    if ttft:
        a2.hist(ttft, bins=12, color="#7c6cf5")
        a2.axvline(sum(ttft) / len(ttft), ls="--", lw=1.4, c="#C0392B")
        a2.text(sum(ttft) / len(ttft), 0.5, f"  평균 {sum(ttft)/len(ttft):.3f}초",
                color="#C0392B", fontsize=10.5)
        a2.set_xlabel("TTFT (첫 토큰까지, 초)"); a2.set_ylabel("건수")
        a2.set_title(f"네트워크 왕복 + 큐잉 실측 (n={len(ttft)})\n"
                     "회귀로는 못 갈랐던 값", fontsize=12)
    else:
        a2.text(.5, .5, "TTFT 를 얻지 못했습니다", ha="center", va="center", fontsize=12)
        a2.set_xticks([]); a2.set_yticks([])
    a2.grid(alpha=.25)

    best = min((t for t in tot if t > 0), default=0)
    fig.suptitle(f"응답 지연 측정 — 지금 {tot[0]:.2f}초 / 가장 빠른 조건 {best:.2f}초 "
                 f"(차이 {tot[0]-best:.2f}초)", fontsize=13)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--cases", type=int, default=0, help="앞에서 N 개만")
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    key = (cfg.get("api_key") or "").strip()
    if not key:
        print("config.json 에 api_key 가 없습니다.")
        return 1

    history = []
    if args.history:
        try:
            from laika_history import History
            h = History(cfg); h.resume(); history = h.messages()
            print(f"  히스토리 {len(history)}개 메시지를 붙입니다")
        except Exception as e:
            print(f"  히스토리 실패, 빈 상태로 진행: {e}")

    cases = CASES[:args.cases] if args.cases else CASES

    print("=" * 78)
    print("응답 지연 측정")
    print(f"  파이썬     : {sys.executable}")
    print(f"  모델       : {cfg.get('model')}  max_tokens={cfg.get('max_tokens')}")
    print(f"  TTS 서버   : {cfg['sovits_url']}")
    print(f"  케이스     : {len(cases)}개 x {args.trials}회")
    print(f"  LLM 호출   : {len(cases)*args.trials*2}회 / TTS 호출 {len(cases)*args.trials*4}회")
    print("=" * 78)

    import anthropic
    client = anthropic.Anthropic(api_key=key)

    rows = []
    for text, note in cases:
        kw = build_kwargs(cfg, history, text)
        for tr in range(1, args.trials + 1):
            # ---- A: 지금 방식
            try:
                a = llm_nonstream(client, kw)
                ta, _ = tts_once(cfg, a["첫문장"] or text, 0)
                rows.append({"조건": "A", "입력": text, "비고": note, "시행": tr,
                             **a, "tts초": ta, "tts모드": 0, "오류": ""})
                print(f"[A ] {text[:14]:<16} LLM {a['첫문장까지']:>6}s  TTS {ta:>6}s  "
                      f"= {round((a['첫문장까지'] or 0)+(ta or 0),2):>6}s")
            except Exception as e:
                rows.append({"조건": "A", "입력": text, "시행": tr,
                             "오류": f"{type(e).__name__}: {e}"})
                print(f"[A ] 실패: {type(e).__name__}: {e}")
                continue

            # ---- B: LLM 스트리밍
            try:
                b = llm_stream(client, kw)
                sent = b["첫문장"] or text
                tb, _ = tts_once(cfg, sent, 0)
                rows.append({"조건": "B", "입력": text, "비고": note, "시행": tr,
                             **b, "tts초": tb, "tts모드": 0, "오류": ""})
                print(f"[B ] TTFT {b['TTFT']:>6}s  첫문장 {b['첫문장까지']:>6}s  "
                      f"TTS {tb:>6}s  = {round((b['첫문장까지'] or 0)+(tb or 0),2):>6}s")
            except Exception as e:
                rows.append({"조건": "B", "입력": text, "시행": tr,
                             "오류": f"{type(e).__name__}: {e}"})
                print(f"[B ] 실패: {type(e).__name__}: {e}")
                continue

            # ---- C1~C3: 같은 첫 문장으로 TTS 스트리밍만 비교
            for mode in (1, 2, 3):
                try:
                    tc, got = tts_once(cfg, sent, mode)
                    rows.append({"조건": f"C{mode}", "입력": text, "비고": note,
                                 "시행": tr, **b, "tts초": tc, "tts모드": mode,
                                 "첫조각바이트": got, "오류": ""})
                    print(f"[C{mode}] 첫문장 {b['첫문장까지']:>6}s  "
                          f"TTS첫조각 {tc:>6}s  = "
                          f"{round((b['첫문장까지'] or 0)+(tc or 0),2):>6}s  ({got}B)")
                except Exception as e:
                    rows.append({"조건": f"C{mode}", "입력": text, "시행": tr,
                                 "오류": f"{type(e).__name__}: {e}"})
                    print(f"[C{mode}] 실패: {type(e).__name__}: {e}")
            time.sleep(0.3)

    # ------------------------------------------------------------ 요약
    print("\n" + "-" * 78)
    ok = [r for r in rows if not r.get("오류")]
    print(f"성공 {len(ok)} / 전체 {len(rows)}")

    def avg(c, k):
        v = [r[k] for r in ok if r["조건"] == c and r.get(k) is not None]
        return sum(v) / len(v) if v else None

    print(f"\n  {'조건':<4}{'첫문장까지':>10}{'TTS':>9}{'첫소리까지':>11}   설명")
    base = None
    for c, desc in (("A", "지금 방식 (논스트리밍 x2)"),
                    ("B", "LLM 스트리밍 + TTS 논스트리밍"),
                    ("C1", "LLM 스트리밍 + TTS 스트리밍 mode1"),
                    ("C2", "LLM 스트리밍 + TTS 스트리밍 mode2"),
                    ("C3", "LLM 스트리밍 + TTS 스트리밍 mode3")):
        l, t = avg(c, "첫문장까지"), avg(c, "tts초")
        if l is None or t is None:
            print(f"  {c:<4}{'-':>10}{'-':>9}{'-':>11}   {desc}")
            continue
        tot = l + t
        if c == "A":
            base = tot
        gain = f"  ({base-tot:+.2f}초)" if base and c != "A" else ""
        print(f"  {c:<4}{l:>10.3f}{t:>9.3f}{tot:>11.3f}   {desc}{gain}")

    ttft = [r["TTFT"] for r in ok if r["조건"] == "B" and r.get("TTFT")]
    if ttft:
        print(f"\n  TTFT (네트워크 왕복 + 큐잉) : 평균 {sum(ttft)/len(ttft):.3f}초 / "
              f"최소 {min(ttft):.3f} / 최대 {max(ttft):.3f}  (n={len(ttft)})")
        print("    -> 회귀(R2=0.520)로는 못 갈랐던 값이다. 이게 실측이다.")

    out = BASE / "laika_latency_check.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록: {out}")
    plot(ok, BASE / "laika_latency_check.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
