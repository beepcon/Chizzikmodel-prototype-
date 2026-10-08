# =============================================================================
# VTube Studio 연결
#
# 지금은 조회만 한다. 표정을 실제로 바꾸는 것은 다음 단계다.
#
# 단독 실행하면 점검 도구로 동작한다:
#     python laika_vts.py                조회 + 그래프 + 매핑 파일 생성
#     python laika_vts.py --port 8002    포트가 다를 때
#     python laika_vts.py --json         원본 응답까지 전부 출력
#
# laika_core 에서 import 해서 쓸 수도 있다:
#     from laika_vts import VTSClient
#     c = VTSClient(); await c.connect(); hk = await c.hotkeys()
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분 (나중에 고치기 쉽도록)
#
#   포트 / 플러그인 이름 / 토큰 위치 : vts_config.json 에서 읽는다. 없으면 만든다.
#   감정 이름 -> 핫키 이름           : vts_emotion_map.json 에서 읽는다.
#                                     이미 있으면 덮어쓰지 않는다 (수정 내용 보존).
#   요청 종류                        : REQUESTS 표에 이름만 추가하면 늘어난다.
#
# 즉 VTS 쪽에서 표정을 추가하거나 이름을 바꿔도 코드를 고칠 필요가 없다.
# JSON 파일만 고치면 된다.
# =============================================================================

import argparse
import asyncio
import json
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE = base_dir()
CONFIG_PATH = BASE / "vts_config.json"
TOKEN_PATH = BASE / "vts_token.json"
MAP_PATH = BASE / "vts_emotion_map.json"
DUMP_PATH = BASE / "vts_hotkeys.json"

# 설정 기본값. 파일이 없을 때만 이 값으로 파일을 만든다.
DEFAULT_CONFIG = {
    "port": 8001,
    "host": "127.0.0.1",
    "plugin_name": "Laika",
    "plugin_developer": "Laika",
    "timeout_sec": 10.0,
}

# 읽기 전용 요청 목록.
# 새 조회를 추가하려면 여기에 한 줄만 넣으면 된다.
# 쓰기 계열(HotkeyTriggerRequest 등)은 의도적으로 넣지 않았다.
REQUESTS = {
    "api_state": "APIStateRequest",
    "stats": "StatisticsRequest",
    "current_model": "CurrentModelRequest",
    "hotkeys": "HotkeysInCurrentModelRequest",
    "expressions": "ExpressionStateRequest",
    "input_params": "InputParameterListRequest",
}


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


def load_config():
    cfg = load_json(CONFIG_PATH)
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
        save_json(CONFIG_PATH, cfg)
        print(f"[설정] 새로 만들었습니다: {CONFIG_PATH.name}")
    else:
        # 파일에 없는 항목은 기본값으로 채운다.
        # 나중에 항목이 늘어나도 기존 파일을 지울 필요가 없다.
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
    return cfg


# ---------------------------------------------------------------- 클라이언트

class VTSClient:
    """VTube Studio API 클라이언트 (조회 전용).

    표정을 실제로 바꾸는 기능은 아직 넣지 않았다.
    넣을 때는 trigger_hotkey 를 이 클래스에 추가하면 되고,
    나머지 코드는 건드릴 필요가 없다."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.ws = None
        self._req_id = 0

    @property
    def url(self):
        return f"ws://{self.cfg['host']}:{self.cfg['port']}"

    # ------------------------------------------------ 저수준

    async def _send(self, message_type, data=None):
        import websockets  # noqa: F401  (연결 시점에 이미 import 되어 있다)

        self._req_id += 1
        payload = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"laika-{self._req_id}",
            "messageType": message_type,
        }
        if data is not None:
            payload["data"] = data

        await self.ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=self.cfg["timeout_sec"])
        resp = json.loads(raw)

        if resp.get("messageType") == "APIError":
            d = resp.get("data", {})
            raise RuntimeError(f"APIError {d.get('errorID')}: {d.get('message')}")
        return resp.get("data", {})

    # ------------------------------------------------ 연결과 인증

    async def connect(self):
        import websockets

        self.ws = await websockets.connect(self.url,
                                           open_timeout=self.cfg["timeout_sec"])
        await self._authenticate()
        return self

    async def _authenticate(self):
        """토큰을 파일에 저장해두고 재사용한다.
        저장하지 않으면 실행할 때마다 VTS 에서 허용 팝업이 뜬다."""
        saved = load_json(TOKEN_PATH, {}) or {}
        token = saved.get("token")

        if token:
            try:
                d = await self._send("AuthenticationRequest", {
                    "pluginName": self.cfg["plugin_name"],
                    "pluginDeveloper": self.cfg["plugin_developer"],
                    "authenticationToken": token,
                })
                if d.get("authenticated"):
                    return True
                print("[인증] 저장된 토큰이 거부되었습니다. 새로 요청합니다.")
            except Exception as e:
                print(f"[인증] 저장된 토큰 사용 실패: {e}. 새로 요청합니다.")

        print("[인증] VTube Studio 화면에 허용 팝업이 뜹니다. '허용'을 눌러주세요.")
        d = await self._send("AuthenticationTokenRequest", {
            "pluginName": self.cfg["plugin_name"],
            "pluginDeveloper": self.cfg["plugin_developer"],
        })
        token = d.get("authenticationToken")
        if not token:
            raise RuntimeError("토큰을 받지 못했습니다. 팝업에서 거부하셨을 수 있습니다.")

        save_json(TOKEN_PATH, {"token": token})
        print(f"[인증] 토큰을 저장했습니다: {TOKEN_PATH.name}")

        d = await self._send("AuthenticationRequest", {
            "pluginName": self.cfg["plugin_name"],
            "pluginDeveloper": self.cfg["plugin_developer"],
            "authenticationToken": token,
        })
        if not d.get("authenticated"):
            raise RuntimeError(f"인증 실패: {d.get('reason')}")
        return True

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    # ------------------------------------------------ 조회

    async def query(self, name):
        """REQUESTS 표의 이름으로 조회한다."""
        if name not in REQUESTS:
            raise KeyError(f"알 수 없는 조회: {name} (가능: {list(REQUESTS)})")
        return await self._send(REQUESTS[name])

    async def hotkeys(self):
        d = await self.query("hotkeys")
        return d.get("availableHotkeys", [])

    async def expressions(self):
        d = await self.query("expressions")
        return d.get("expressions", [])


# ---------------------------------------------------------------- 매핑 파일

def build_emotion_map(hotkeys):
    """감정 이름 -> 핫키 이름 매핑 파일을 만든다.

    이미 파일이 있으면 손대지 않는다. Joshep 이 고친 내용이 날아가면 안 된다.
    없을 때만 후보를 채워 넣은 초안을 만든다."""
    existing = load_json(MAP_PATH)
    if existing is not None:
        print(f"[매핑] 기존 파일을 유지합니다: {MAP_PATH.name} "
              f"(항목 {len(existing.get('map', {}))}개)")
        return existing

    names = [h.get("name", "") for h in hotkeys]
    draft = {
        "_설명": [
            "라이카가 쓸 감정 이름을 VTS 핫키 이름에 연결합니다.",
            "왼쪽(감정)은 LLM 이 출력할 이름, 오른쪽은 VTS 핫키 이름입니다.",
            "빈 문자열이면 그 감정에서는 표정을 바꾸지 않습니다.",
            "이 파일만 고치면 코드 수정 없이 표정이 바뀝니다.",
        ],
        "_사용가능한_핫키": names,
        "default": "",
        "map": {e: "" for e in
                ["중립", "기쁨", "슬픔", "놀람", "화남", "부끄러움", "졸림", "생각"]},
    }
    save_json(MAP_PATH, draft)
    print(f"[매핑] 초안을 만들었습니다: {MAP_PATH.name}  ← 오른쪽 값을 채워주세요")
    return draft


# ---------------------------------------------------------------- 출력

def print_report(info):
    hotkeys = info["hotkeys"]
    exprs = info["expressions"]
    model = info["current_model"]
    state = info["api_state"]

    print(f"\n{'=' * 78}")
    print("VTube Studio 점검 결과")
    print("=" * 78)
    print(f"  VTS 버전      : {state.get('vTubeStudioVersion', '?')}")
    print(f"  API 활성      : {state.get('active')}")
    print(f"  모델 로드됨   : {model.get('modelLoaded')}")
    print(f"  모델 이름     : {model.get('modelName', '(없음)')}")

    print(f"\n[핫키] {len(hotkeys)}개")
    if not hotkeys:
        print("  등록된 핫키가 없습니다. VTS 에서 표정 핫키를 만들어야 합니다.")
    else:
        # keyCombination 은 보안상 항상 빈 배열로 오므로 표시하지 않는다.
        # 키보드 배정 여부는 API 로 알 수 없고 VTS 화면에서 직접 봐야 한다.
        print(f"  {'이름':<22}{'종류':<20}{'화면버튼':<10}파일")
        print("  " + "-" * 74)
        for h in hotkeys:
            btn = h.get("onScreenButtonID", -1)
            btn_s = "없음" if btn in (-1, None) else str(btn)
            print(f"  {h.get('name', ''):<22}{h.get('type', ''):<20}"
                  f"{btn_s:<10}{h.get('file', '')}")

        on_btn = [h for h in hotkeys if h.get("onScreenButtonID", -1) not in (-1, None)]
        toggles = [h for h in hotkeys if h.get("type") == "ToggleExpression"]
        print(f"\n  화면 버튼 배정  : {len(on_btn)}개"
              + ("  (마우스 클릭 전용이라 게임 키 입력과 무관합니다)" if on_btn else ""))
        for h in on_btn:
            print(f"     - 버튼 {h.get('onScreenButtonID')} : {h.get('name')}")
        print(f"  ToggleExpression : {len(toggles)}개"
              + ("  ← 토글이라 끄지 않으면 계속 유지됩니다" if toggles else ""))
        print("\n  [확인 필요] 키보드 조합은 API 로 알 수 없습니다(보안상 항상 빈 값).")
        print("             VTS 설정의 'use keyboard hotkeys' 와 각 핫키의")
        print("             key combination 칸을 화면에서 직접 확인하세요.")

    print(f"\n[표정 상태] {len(exprs)}개")
    if exprs:
        print(f"  {'파일':<34}{'활성':<8}{'자동종료':<10}남은시간")
        print("  " + "-" * 74)
        for e in exprs:
            print(f"  {e.get('file', ''):<34}{str(e.get('active')):<8}"
                  f"{str(e.get('autoStop')):<10}{e.get('secondsRemaining', '')}")
    else:
        print("  표정 파일이 없습니다.")


def plot_report(info, out_path):
    """조회 결과를 그림으로 남긴다. 핫키가 많아지면 표만으로는 안 보인다."""
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

    hotkeys = info["hotkeys"]
    exprs = info["expressions"]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    # 1) 종류별 개수
    types = {}
    for h in hotkeys:
        types[h.get("type", "?")] = types.get(h.get("type", "?"), 0) + 1
    if types:
        ks = list(types)
        axes[0].barh(range(len(ks)), [types[k] for k in ks], color="#4c72b0")
        axes[0].set_yticks(range(len(ks)))
        axes[0].set_yticklabels(ks, fontsize=8)
        axes[0].invert_yaxis()
        for i, k in enumerate(ks):
            axes[0].text(types[k], i, f" {types[k]}", va="center", fontsize=8)
    else:
        axes[0].text(0.5, 0.5, "핫키 없음", ha="center", transform=axes[0].transAxes)
    axes[0].set_title("핫키 종류별 개수", fontsize=11)
    axes[0].grid(axis="x", alpha=0.3)

    # 2) 화면 버튼 배정 — 마우스 클릭 전용이라 게임 키 입력과 무관하다.
    #    키보드 조합은 API 로 알 수 없어(항상 빈 값) 그리지 않는다.
    on_btn = sum(1 for h in hotkeys
                 if h.get("onScreenButtonID", -1) not in (-1, None))
    none_btn = len(hotkeys) - on_btn
    axes[1].bar(["화면 버튼 있음\n(클릭 전용)", "화면 버튼 없음\n(API 로만 실행)"],
                [on_btn, none_btn], color=["#55a868", "#4c72b0"])
    for i, v in enumerate([on_btn, none_btn]):
        axes[1].text(i, v, str(v), ha="center", va="bottom", fontsize=11)
    axes[1].set_title("화면 버튼 배정\n(키보드 조합은 API 로 조회 불가)", fontsize=10)
    axes[1].grid(axis="y", alpha=0.3)

    # 3) 표정 활성 상태
    if exprs:
        names = [e.get("file", "")[:18] for e in exprs]
        vals = [1 if e.get("active") else 0 for e in exprs]
        axes[2].barh(range(len(names)), vals,
                     color=["#dd8452" if v else "#cccccc" for v in vals])
        axes[2].set_yticks(range(len(names)))
        axes[2].set_yticklabels(names, fontsize=7)
        axes[2].invert_yaxis()
        axes[2].set_xlim(0, 1.2)
        axes[2].set_xticks([0, 1])
        axes[2].set_xticklabels(["꺼짐", "켜짐"])
    else:
        axes[2].text(0.5, 0.5, "표정 없음", ha="center", transform=axes[2].transAxes)
    axes[2].set_title("표정 활성 상태", fontsize=11)

    model_name = info["current_model"].get("modelName", "(모델 없음)")
    fig.suptitle(f"VTube Studio 점검 — {model_name}", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    print(f"→ 그래프: {out_path}")


# ---------------------------------------------------------------- 진입점

async def inspect(cfg, show_json=False):
    client = VTSClient(cfg)
    try:
        await client.connect()
    except Exception as e:
        print(f"\n[연결 실패] {type(e).__name__}: {e}")
        print(f"  주소: {client.url}")
        print("  확인할 것:")
        print("   1) VTube Studio 가 실행 중인지")
        print("   2) VTS 설정에서 'Allow Plugin API access' 가 켜져 있는지")
        print(f"   3) 포트가 맞는지 (다르면 {CONFIG_PATH.name} 의 port 를 고치세요)")
        return None

    info = {}
    for name in REQUESTS:
        try:
            info[name] = await client.query(name)
        except Exception as e:
            print(f"[조회 실패] {name}: {type(e).__name__}: {e}")
            info[name] = {}

    info["hotkeys"] = info.get("hotkeys", {}).get("availableHotkeys", []) \
        if isinstance(info.get("hotkeys"), dict) else []
    info["expressions"] = info.get("expressions", {}).get("expressions", []) \
        if isinstance(info.get("expressions"), dict) else []

    await client.close()

    print_report(info)
    if show_json:
        print("\n[원본 응답]")
        print(json.dumps(info, ensure_ascii=False, indent=2))

    save_json(DUMP_PATH, info)
    print(f"\n→ 저장: {DUMP_PATH.name}")

    build_emotion_map(info["hotkeys"])
    plot_report(info, BASE / "vts_report.png")
    return info


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, help=f"기본 {cfg['port']}")
    ap.add_argument("--host", help=f"기본 {cfg['host']}")
    ap.add_argument("--json", action="store_true", help="원본 응답도 출력")
    args = ap.parse_args()

    if args.port:
        cfg["port"] = args.port
    if args.host:
        cfg["host"] = args.host

    asyncio.run(inspect(cfg, show_json=args.json))


if __name__ == "__main__":
    main()
