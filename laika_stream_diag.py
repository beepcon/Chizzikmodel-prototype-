# =============================================================================
# 스트리밍 진단 - 왜 스트리밍이 안 먹히는가
#
# 무엇이 관측됐나 (laika_latency_check.json, 2026-09-16, 6건)
#   TTFT 4.579  첫문장 4.580  전체 4.591   out_tok 206
#   TTFT 5.063  첫문장 5.064  전체 5.068   out_tok 221
#   TTFT 4.280  첫문장 4.280  전체 4.282   out_tok 185
#   6건 전부 TTFT ~= 첫문장 ~= 전체. 200 토큰이 0.01초 안에 다 들어온다.
#   조각으로 오는 게 아니라 다 만들어진 뒤 한 번에 온다.
#
#   그리고 GPT-SoVITS 스트리밍은 18건 전부 이렇게 실패했다.
#     ChunkedEncodingError: Response ended prematurely
#
#   둘 다 chunked 전송이라는 공통점이 있다. 원인은 아직 모른다.
#
# 무엇을 재나
#   [1] Anthropic  델타가 도착한 시각을 전부 찍는다. 세 경로로 따로 잰다.
#         T1 SDK + tool_use 강제   (방송과 같은 조건)
#         T2 SDK + 도구 없음       (tool_use JSON 이 원인인지 가른다)
#         T3 httpx 로 직접 SSE     (SDK 가 원인인지 가른다)
#       판정: 첫 델타와 마지막 델타의 간격(spread)이 0.1초 미만이면 버퍼링이다.
#
#   [2] GPT-SoVITS  streaming_mode 1/2/3 을 media_type wav/raw 로 각각.
#       이번엔 첫 조각에서 끊지 않고 끝까지 받는다.
#       앞의 실패가 내 코드가 끊은 탓인지, 서버가 끊은 탓인지 가른다.
#       비교를 위해 "첫 조각에서 끊기" 도 따로 재현한다.
#
#   [3] 환경  프록시 환경변수, 라이브러리 버전
#
# 실행 (GPT-SoVITS 서버를 켜두면 [2] 까지 잰다. 없으면 [1][3] 만 나온다)
#   conda deactivate
#   conda activate laika
#   python laika_stream_diag.py
#
#   --skip-tts     [2] 를 건너뛴다
#   --trials 2     [1] 의 조건당 시행 횟수
#
# 결과: laika_stream_diag.json / laika_stream_diag.png
# =============================================================================

import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
BUFFERED_THRESHOLD = 0.10      # 이보다 촘촘하면 버퍼링으로 본다


def load_cfg():
    return json.loads((BASE / "config.json").read_text(encoding="utf-8"))


def build_body(cfg, user_text, with_tool):
    """laika_core._llm_reply 와 같은 요청 본문."""
    body = {
        "model": cfg.get("model", "claude-sonnet-4-6"),
        "max_tokens": cfg.get("max_tokens", 200),
        "system": cfg["persona"],
        "messages": [{"role": "user", "content": f"[호스트] {user_text}"}],
    }
    if with_tool:
        from laika_emotion import EmotionMap, build_tool
        emap = EmotionMap(cfg)
        if emap.cfg["enabled"] and emap.is_configured() and emap.emotions:
            body["tools"] = [build_tool(emap.emotions, emap.default)]
            body["tool_choice"] = {"type": "tool", "name": "speak"}
    return body


def _verdict(spread, max_gap, n):
    """조각이 고르게 왔는지 판정한다.

    spread 가 커도 한 번의 큰 간격이 그 대부분이면 몰려 온 것이다."""
    if n <= 1 or spread < BUFFERED_THRESHOLD:
        return "버퍼링 (한 번에 옴)"
    if max_gap >= spread * 0.6:
        return f"버퍼링 (간격 {max_gap:.2f}s 가 확산 {spread:.2f}s 의 대부분)"
    return "스트리밍 정상"


def summarize(marks, total):
    """도착 시각 목록에서 판정에 쓸 값만 뽑는다."""
    if not marks:
        return {"조각수": 0, "첫조각": None, "마지막조각": None,
                "확산초": None, "판정": "조각 없음", "전체초": round(total, 3)}
    first, last = marks[0], marks[-1]
    spread = last - first
    gaps = [b - a for a, b in zip(marks, marks[1:])]
    return {
        "조각수": len(marks),
        "첫조각": round(first, 3),
        "마지막조각": round(last, 3),
        "확산초": round(spread, 3),
        "최대간격": round(max(gaps), 3) if gaps else None,
        "전체초": round(total, 3),
        # 확산만 보면 안 된다. 조각 하나가 일찍 오고 나머지가 몰려 와도
        # 확산은 커진다(T3 에서 실제로 그랬다: 1.21초에 1개, 5.31초에 32개).
        # 조각이 고르게 오는지를 보려면 가장 큰 간격이 확산의 대부분을
        # 차지하지 않는지까지 봐야 한다.
        "판정": _verdict(spread, max(gaps) if gaps else 0.0, len(marks)),
        "_marks": [round(m, 4) for m in marks],
    }


# ---------------------------------------------------------------- [1] LLM

def t_sdk(cfg, text, with_tool):
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    body = build_body(cfg, text, with_tool)
    marks = []
    t0 = time.time()
    with client.messages.stream(**body) as s:
        for ev in s:
            if getattr(ev, "type", "") == "content_block_delta":
                d = getattr(ev, "delta", None)
                if getattr(d, "partial_json", None) or getattr(d, "text", None):
                    marks.append(time.time() - t0)
        s.get_final_message()
    return summarize(marks, time.time() - t0)


def t_httpx(cfg, text, with_tool):
    """SDK 를 빼고 SSE 줄이 도착하는 시각을 직접 잰다."""
    import httpx
    body = build_body(cfg, text, with_tool)
    body["stream"] = True
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    marks = []
    t0 = time.time()
    with httpx.Client(timeout=120.0) as c:
        with c.stream("POST", "https://api.anthropic.com/v1/messages",
                      headers=headers, json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line and line.startswith("data:") and "_delta" in line:
                    marks.append(time.time() - t0)
    return summarize(marks, time.time() - t0)


# ---------------------------------------------------------------- [2] TTS

def t_tts(cfg, text, mode, media_type, stop_at_first):
    """GPT-SoVITS 스트리밍. 조각마다 도착 시각을 찍는다.

    stop_at_first=True 면 앞선 측정처럼 첫 조각에서 끊는다.
    그때만 ChunkedEncodingError 가 나는지 보려는 것이다."""
    import requests
    try:
        from laika_tts_norm import TTSNormalizer
        spoken = TTSNormalizer().normalize(text)
    except Exception:
        spoken = text
    payload = {
        "text": spoken, "text_lang": "ko",
        "ref_audio_path": cfg["ref_audio"],
        "prompt_text": cfg["ref_text"], "prompt_lang": "ko",
        "aux_ref_audio_paths": [a for a in cfg.get("aux_ref_audio", []) if a],
        "streaming_mode": mode, "media_type": media_type,
        **cfg["tts"],
    }
    marks, total_bytes, err = [], 0, ""
    t0 = time.time()
    try:
        r = requests.post(cfg["sovits_url"], json=payload,
                          stream=bool(mode), timeout=180)
        r.raise_for_status()
        if not mode:
            total_bytes = len(r.content)
            marks = [time.time() - t0]
        else:
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                marks.append(time.time() - t0)
                total_bytes += len(chunk)
                if stop_at_first:
                    break
            r.close()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    out = summarize(marks, time.time() - t0)
    out.update({"모드": mode, "media_type": media_type,
                "첫조각에서끊음": stop_at_first,
                "총바이트": total_bytes, "오류": err})
    if err and not marks:
        out["판정"] = "실패"
    elif err:
        out["판정"] += " / 도중 오류"
    return out


# ---------------------------------------------------------------- 그래프

def plot(res, out_png):
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

    llm = res["llm"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.4))

    labels, rows = [], []
    for name in ("T1", "T2", "T3"):
        for i, r in enumerate(llm.get(name, [])):
            if r.get("_marks"):
                labels.append(f"{name}#{i+1}")
                rows.append(r)
    for y, r in enumerate(rows):
        m = r["_marks"]
        buffered = (r["확산초"] or 0) < BUFFERED_THRESHOLD
        a1.scatter(m, [y] * len(m), s=14,
                   c="#C0392B" if buffered else "#4ec9a5")
        a1.text(max(m) + 0.12, y,
                f"{len(m)}조각 / 확산 {r['확산초']:.3f}초  {r['판정']}",
                va="center", fontsize=9,
                color="#8e2820" if buffered else "#1c5c4a")
    if rows:
        a1.set_yticks(range(len(rows))); a1.set_yticklabels(labels, fontsize=9)
        a1.set_xlabel("요청 시작부터 경과 (초)")
        a1.set_xlim(0, max(max(r["_marks"]) for r in rows) * 1.55)
    else:
        a1.text(.5, .5, "LLM 델타를 못 받았습니다", ha="center", va="center")
    a1.set_title("델타가 도착한 시각\n한 줄에 몰려 있으면 버퍼링", fontsize=12)
    a1.grid(axis="x", alpha=.25)

    tts = res.get("tts") or []
    if tts:
        names, firsts, spreads, oks = [], [], [], []
        for r in tts:
            tag = f"m{r['모드']}/{r['media_type']}" + ("/끊음" if r["첫조각에서끊음"] else "")
            names.append(tag)
            firsts.append(r["첫조각"] or 0)
            spreads.append(r["확산초"] or 0)
            oks.append(not r["오류"])
        y = range(len(names))
        a2.barh(list(y), firsts, color=["#4ec9a5" if o else "#C0392B" for o in oks])
        for i, r in enumerate(tts):
            txt = f" {r['조각수']}조각 {r['총바이트']}B"
            if r["오류"]:
                txt += "  " + r["오류"][:34]
            a2.text((firsts[i] or 0) + .02, i, txt, va="center", fontsize=8.5)
        a2.set_yticks(list(y)); a2.set_yticklabels(names, fontsize=9)
        a2.set_xlabel("첫 조각까지 (초)")
        a2.set_xlim(0, max(firsts + [1]) * 2.1)
    else:
        a2.text(.5, .5, "TTS 측정 없음", ha="center", va="center", fontsize=12)
        a2.set_xticks([]); a2.set_yticks([])
    a2.set_title("GPT-SoVITS 스트리밍 (초록=성공, 빨강=실패)", fontsize=12)
    a2.grid(axis="x", alpha=.25)

    fig.suptitle("스트리밍 진단 — 어디서 막히는가", fontsize=13.5)
    fig.tight_layout(); fig.savefig(out_png, dpi=130)
    print(f"그래프: {out_png}")


# ---------------------------------------------------------------- 실행

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--skip-tts", action="store_true")
    ap.add_argument("--only-tts", action="store_true",
                    help="[2] 만 잰다. 서버를 콘솔에서 직접 띄워놓고 쓸 때")
    ap.add_argument("--text", default="자기소개 해봐")
    args = ap.parse_args()

    cfg = load_cfg()
    if not (cfg.get("api_key") or "").strip():
        print("config.json 에 api_key 가 없습니다.")
        return 1

    env = {}
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    vers = {}
    for m in ("anthropic", "httpx", "requests", "httpcore"):
        try:
            vers[m] = __import__(m).__version__
        except Exception as e:
            vers[m] = f"({type(e).__name__})"

    print("=" * 78)
    print("스트리밍 진단")
    print(f"  파이썬     : {sys.executable}")
    print(f"  모델       : {cfg.get('model')}  max_tokens={cfg.get('max_tokens')}")
    print(f"  라이브러리 : " + "  ".join(f"{k} {v}" for k, v in vers.items()))
    print(f"  프록시 변수: {env if env else '없음'}")
    print(f"  버퍼링 기준: 확산 {BUFFERED_THRESHOLD}초 미만")
    print("=" * 78)

    res = {"환경": {"python": sys.executable, "버전": vers, "프록시": env},
           "llm": {"T1": [], "T2": [], "T3": []}, "tts": []}

    conds = [("T1", "SDK + tool_use 강제 (방송과 같음)", lambda: t_sdk(cfg, args.text, True)),
             ("T2", "SDK + 도구 없음", lambda: t_sdk(cfg, args.text, False)),
             ("T3", "httpx 직접 SSE + tool_use", lambda: t_httpx(cfg, args.text, True))]

    if args.only_tts:
        print("\n[1] 건너뜀 (--only-tts)")
    for tag, desc, fn in ([] if args.only_tts else conds):
        for i in range(args.trials):
            try:
                r = fn()
            except Exception as e:
                r = {"판정": "실패", "오류": f"{type(e).__name__}: {e}",
                     "조각수": 0, "확산초": None}
                print(f"  {tag}#{i+1} 실패: {r['오류'][:70]}")
                res["llm"][tag].append(r); continue
            res["llm"][tag].append(r)
            print(f"  {tag}#{i+1} {desc}")
            print(f"       조각 {r['조각수']:>4}개  첫조각 {r['첫조각']}s  "
                  f"마지막 {r['마지막조각']}s  확산 {r['확산초']}s  -> {r['판정']}")
            time.sleep(0.3)

    if not args.skip_tts:
        print("\n[2] GPT-SoVITS 스트리밍")
        print("    서버를 콘솔에서 직접 띄워두었다면, 실패할 때마다")
        print("    그 콘솔에 traceback 이 찍힌다. 그것이 원인이다.")
        sent = "안녕하세요 여러분, 저는 라이카예요."
        plan = [(0, "wav", False)]
        for m in (1, 2, 3):
            plan += [(m, "wav", False), (m, "raw", False), (m, "wav", True)]
        for mode, mt, stop in plan:
            r = t_tts(cfg, sent, mode, mt, stop)
            res["tts"].append(r)
            tag = f"mode={mode} {mt}" + (" 첫조각에서끊음" if stop else "")
            print(f"  {tag:<28} 조각 {r['조각수']:>4}  첫조각 {r['첫조각']}s  "
                  f"확산 {r['확산초']}s  {r['총바이트']}B  -> {r['판정']}"
                  + (f"  {r['오류'][:40]}" if r["오류"] else ""))
            time.sleep(0.3)
    else:
        print("\n[2] 건너뜀 (--skip-tts)")

    # ---------------------------------------------------------- 판정
    print("\n" + "-" * 78)
    print("판정")
    for tag, desc, _ in ([] if args.only_tts else conds):
        rs = [r for r in res["llm"][tag] if r.get("확산초") is not None]
        if not rs:
            print(f"  {tag} {desc:<34} 측정 실패"); continue
        sp = sum(r["확산초"] for r in rs) / len(rs)
        print(f"  {tag} {desc:<34} 확산 평균 {sp:.3f}초  -> "
              + ("버퍼링" if sp < BUFFERED_THRESHOLD else "정상"))
    if not args.only_tts:
        print("\n  T1 버퍼링 / T2 정상  -> tool_use JSON 이 원인")
        print("  T1 T2 버퍼링 / T3 정상 -> SDK 가 원인")
        print("  T1 T2 T3 모두 버퍼링  -> 네트워크 경로(보안 프로그램·프록시)가 원인")

    ok_tts = [r for r in res["tts"] if not r["오류"]]
    if res["tts"]:
        print(f"\n  TTS 성공 {len(ok_tts)}/{len(res['tts'])}")
        cut = [r for r in res["tts"] if r["첫조각에서끊음"]]
        nocut = [r for r in res["tts"] if not r["첫조각에서끊음"] and r["모드"]]
        ce = sum(1 for r in cut if "ChunkedEncoding" in r["오류"])
        ne = sum(1 for r in nocut if "ChunkedEncoding" in r["오류"])
        print(f"    첫 조각에서 끊었을 때 ChunkedEncodingError {ce}/{len(cut)}건")
        print(f"    끝까지 받았을 때     ChunkedEncodingError {ne}/{len(nocut)}건")
        if ce and not ne:
            print("    -> 앞선 실패는 내 코드가 첫 조각에서 끊은 탓이다. 서버는 정상.")
        elif ne:
            print("    -> 끝까지 받아도 끊긴다. 서버 또는 네트워크 문제다.")

    # 아무것도 못 쟀으면 기존 결과를 덮어쓰지 않는다.
    # (--only-tts --skip-tts 처럼 둘 다 건너뛴 채 돌리면 지난 측정이 날아간다)
    measured = sum(len(v) for v in res["llm"].values()) + len(res["tts"])
    out = BASE / "laika_stream_diag.json"
    if measured == 0:
        print("\n잰 것이 없어 기록을 남기지 않습니다. 기존 파일을 그대로 둡니다.")
        return 0
    if out.exists():
        keep = out.with_suffix(".json.bak")
        try:
            keep.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"\n이전 결과를 옮겨 두었습니다: {keep.name}")
        except Exception as e:
            print(f"\n이전 결과 보관 실패: {e}")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"기록: {out}")
    plot(res, BASE / "laika_stream_diag.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
