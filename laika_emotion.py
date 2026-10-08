# =============================================================================
# 감정 표현
#
# 문장마다 감정을 받아 VTube Studio 표정 핫키를 켜고 끈다.
#
# 검증에서 확인된 것 (emotion_test.png, 10회 시행)
#   - tool_choice 강제로 도구 사용률 100%
#   - 그런데도 enum 밖 감정이 21문장 중 3개(장난, 장난기) 나왔다
#     -> 매핑에 없으면 default 로 떨어뜨린다. 반드시 필요하다.
#   - 중립이 0회였다 -> 도구 설명에 "변화 없으면 중립" 을 넣는다
#
# 핫키는 ToggleExpression 이라 켜고 끄는 순서가 필요하다.
#   기쁨 -> 놀람 으로 바꾸려면  기쁨 호출(꺼짐) 후 놀람 호출(켜짐).
# 라이카가 꺼진 뒤에도 VTS 에는 표정이 켜진 채로 남으므로,
# 시작할 때 ExpressionStateRequest 로 실제 상태를 한 번 읽어 맞춘다.
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   감정 목록과 핫키 이름 : vts_emotion_map.json 에서 읽는다
#   표정 기능 on/off      : config.json 의 emotion.enabled
#   이 모듈이 실패해도 방송은 계속된다. 전부 예외를 흡수한다.
# =============================================================================

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "map_file": "vts_emotion_map.json",
    "skip_if_same": True,    # 직전과 같은 감정이면 호출하지 않는다 (왕복 절약)
    "timeout_sec": 3.0,      # 표정 호출이 늦어도 발화를 막지 않는다

    # 발화가 끝나고 이 시간 뒤에 중립(맨 얼굴)로 돌아간다.
    #   0 이면 즉시. 큰 값이면 다음 발화까지 표정이 유지된다.
    # 2초인 이유: _play 가 잔향 때문에 재생 후 0.3초를 대기하고,
    # 그동안 아바타 입은 아직 움직인다. 표정이 먼저 사라지면 어색하다.
    "reset_after_sec": 2.0,
}

# 매핑 파일이 없을 때만 만드는 초안.
# laika_vts.py / laika_emotion_test.py 와 같은 형식이다.
MAP_TEMPLATE = {
    "_설명": [
        "라이카가 쓸 감정 이름을 VTS 핫키 이름에 연결합니다.",
        "왼쪽(감정)은 LLM 이 출력할 이름, 오른쪽은 VTS 핫키 이름입니다.",
        "핫키는 토글이라 끄려면 같은 핫키를 한 번 더 눌러야 합니다.",
        "오른쪽이 빈 문자열이면 켜져 있는 표정을 끄고 맨 얼굴로 돌아갑니다.",
        "default 는 목록에 없는 감정이 왔을 때 대신 쓸 감정 이름입니다.",
        "핫키 이름은 vts_hotkeys.json 에서 그대로 복사하세요.",
        "  이름 끝에 공백이 있는 경우가 있습니다. 지우면 못 찾습니다.",
        "이 파일만 고치면 코드 수정 없이 표정이 바뀝니다.",
    ],
    "_사용가능한_핫키": [],
    "default": "중립",
    "map": {e: "" for e in
            ["중립", "기쁨", "슬픔", "놀람", "화남", "부끄러움", "졸림", "생각"]},
}


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class EmotionMap:
    """감정 이름 -> VTS 핫키 이름. 파일에서 읽고, 없으면 초안을 만든다."""

    def __init__(self, cfg=None, log=None):
        raw = (cfg or {}).get("emotion") or {}
        self.cfg = dict(DEFAULTS)
        self.cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        self.log = log

        self.path = base_dir() / self.cfg["map_file"]
        self.data = self._load()

    def _say(self, kind, msg):
        if self.log:
            self.log(kind, msg)

    def _load(self):
        if not self.path.exists():
            self.path.write_text(
                json.dumps(MAP_TEMPLATE, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self._say("system", f"[표정] 매핑 초안을 만들었습니다: {self.path.name}")
            return dict(MAP_TEMPLATE)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            self._say("error", f"[표정] 매핑 읽기 실패, 기본값 사용: {e}")
            return dict(MAP_TEMPLATE)

    def reload(self):
        """방송 중에 파일을 고쳐도 반영되게 한다."""
        self.data = self._load()

    @property
    def emotions(self):
        """도구 스키마의 enum 에 들어갈 목록."""
        return list((self.data.get("map") or {}).keys())

    @property
    def default(self):
        d = self.data.get("default") or ""
        return d if d in self.emotions else (self.emotions[0] if self.emotions else "")

    def hotkey(self, emotion):
        """감정 이름을 핫키 이름으로 바꾼다.

        반환: (핫키이름, 해석된감정, 알려진감정인가)

        핫키 이름이 빈 문자열인 것은 "표정 없음" 을 뜻한다.
        토글 구조라 이 경우 켜져 있는 표정을 꺼서 맨 얼굴로 돌아가야 한다.
        그래서 '매핑에 있지만 값이 빈 것'(=끄라는 지시) 과
        '매핑에 아예 없는 것'(=어쩔 줄 모름) 을 구분해서 알려준다.

        검증에서 enum 밖 감정이 실제로 왔으므로(장난, 장난기),
        목록에 없으면 default 로 떨어뜨린다."""
        m = self.data.get("map") or {}

        if emotion in m:
            return m[emotion], emotion, True

        d = self.default
        if d in m:
            return m[d], d, True

        return "", emotion, False

    def is_configured(self):
        """핫키 이름이 하나라도 채워져 있는지."""
        return any((self.data.get("map") or {}).values())


def build_tool(emotions, default_name="중립"):
    """문장별 감정을 받는 도구 정의.

    enum 을 넣어도 벗어나는 경우가 있었으므로(검증 3/21건),
    설명에도 목록을 벗어나지 말라고 명시한다."""
    names = ", ".join(emotions)
    return {
        "name": "speak",
        "description": (
            "라이카가 말할 내용을 문장 단위로 나누어 전달한다. "
            "각 문장마다 그 문장을 말할 때의 감정을 함께 지정한다.\n"
            f"감정은 반드시 다음 중에서만 고른다: {names}\n"
            f"감정 변화가 뚜렷하지 않으면 '{default_name}' 을 쓴다. "
            "문장마다 억지로 다른 감정을 넣지 않는다."
        ),
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
                                "description": "실제로 말할 문장. 감정 표시나 특수문자를 넣지 않는다.",
                            },
                        },
                        "required": ["emotion", "text"],
                    },
                },
            },
            "required": ["sentences"],
        },
    }


class ExpressionController:
    """표정 핫키를 켜고 끈다. 토글이므로 현재 켜진 것을 추적한다.

    VTS 연결은 별도 스레드의 asyncio 루프에서 돌린다.
    발화 스레드가 표정 호출 때문에 멈추면 안 되기 때문이다."""

    def __init__(self, emap: EmotionMap, cfg=None, log=None):
        self.emap = emap
        self.log = log
        self.cfg = emap.cfg
        self.vts_cfg = (cfg or {}).get("vts") or {}

        self.client = None
        self.active = None          # 현재 켜져 있는 핫키 이름
        self.last_emotion = None
        self.connected = False

        self._loop = None
        self._thread = None
        self._lock = threading.Lock()
        self._reset_timer = None    # 발화 후 중립 복귀 예약

    def _say(self, kind, msg):
        if self.log:
            self.log(kind, msg)

    # ------------------------------------------------------------ 수명주기

    def start(self):
        """연결하고 현재 표정 상태를 읽어 맞춘다. 실패해도 방송은 계속된다."""
        if not self.cfg["enabled"]:
            self._say("system", "[표정] 꺼져 있습니다. (config 의 emotion.enabled)")
            return False
        if not self.emap.is_configured():
            self._say("system",
                      f"[표정] {self.emap.path.name} 에 핫키 이름이 비어 있어 건너뜁니다.")
            return False

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        try:
            fut = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
            self.connected = fut.result(timeout=15)
        except Exception as e:
            # 원인을 추측하지 않으려면 어느 파이썬으로 돌고 있는지가 필요하다.
            # 패키지가 없다는 오류는 대개 다른 환경으로 실행됐다는 뜻이다.
            self._say("error", f"[표정] 연결 실패: {type(e).__name__}: {e}")
            self._say("error", f"[표정] 실행 파이썬: {sys.executable}")
            if isinstance(e, ModuleNotFoundError):
                self._say("error",
                          f"[표정] 위 파이썬에 '{e.name}' 이 없습니다. "
                          f"그 파이썬으로 설치하세요: \"{sys.executable}\" -m pip install {e.name}")
            else:
                import traceback
                for line in traceback.format_exc().strip().splitlines()[-6:]:
                    self._say("error", f"[표정]   {line}")
            self.connected = False
        return self.connected

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        from laika_vts import VTSClient, load_config

        vcfg = load_config()
        vcfg.update({k: v for k, v in self.vts_cfg.items() if k in vcfg})

        self.client = VTSClient(vcfg)
        await self.client.connect()

        # --- a안: 시작 시 실제 상태를 한 번 읽어 맞춘다 ---
        # 라이카가 꺼진 뒤에도 VTS 에는 표정이 켜진 채로 남는다.
        # 꺼져 있다고 가정하면 다음 호출이 반대로 동작한다.
        await self._sync_state()
        self._say("system", f"[표정] VTS 연결됨. 현재 표정: {self.active or '없음'}")
        return True

    async def _sync_state(self):
        """켜져 있는 표정을 찾아 active 에 넣는다.

        표정 파일(exp3.json) 과 핫키 이름은 다르므로, 핫키 목록의 file 로 맞춘다."""
        try:
            exprs = await self.client.expressions()
            hotkeys = await self.client.hotkeys()
        except Exception as e:
            self._say("error", f"[표정] 상태 조회 실패: {e}")
            return

        on_files = [e.get("file") for e in exprs if e.get("active")]
        if not on_files:
            self.active = None
            return

        by_file = {h.get("file"): h.get("name") for h in hotkeys}
        for f in on_files:
            if f in by_file:
                self.active = by_file[f]
                return
        # 핫키에 연결되지 않은 표정이 켜져 있으면 끌 방법이 없다
        self._say("system",
                  f"[표정] 핫키에 없는 표정이 켜져 있습니다: {on_files}. "
                  "VTS 에서 직접 꺼주세요.")

    def stop(self):
        if self._loop is None:
            return
        self.cancel_reset()
        try:
            if self.connected and self.active:
                # 켜둔 채로 끝나면 다음 실행 때 어긋나므로 꺼둔다
                fut = asyncio.run_coroutine_threadsafe(
                    self._trigger(self.active), self._loop)
                fut.result(timeout=3)
                self.active = None
        except Exception:
            pass
        try:
            if self.client:
                asyncio.run_coroutine_threadsafe(
                    self.client.close(), self._loop).result(timeout=3)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        self.connected = False

    # ------------------------------------------------------------ 표정 전환

    async def _trigger(self, hotkey_name):
        """핫키 하나를 실행한다. 이름으로 부른다."""
        await self.client._send("HotkeyTriggerRequest",
                                {"hotkeyID": hotkey_name})

    def press_hotkey(self, name):
        """핫키 하나를 눌러 토글한다. active 추적도 같이 갱신한다.

        점검 창에서 표정을 눈으로 확인할 때 쓴다.
        점검 창이 따로 연결해서 누르면 여기서 추적하는 active 와 어긋나,
        다음 발화의 [끄기]->[켜기] 순서가 반대로 동작한다.
        그래서 방송 중에는 반드시 이 경로로 눌러야 한다.

        반환: (성공여부, 메시지)"""
        if not self.connected:
            return False, "[표정] VTS 에 연결되어 있지 않습니다."

        self.cancel_reset()
        with self._lock:
            try:
                if self.active == name:
                    asyncio.run_coroutine_threadsafe(
                        self._trigger(name), self._loop
                    ).result(timeout=self.cfg["timeout_sec"])
                    self.active = None
                    self.last_emotion = None
                    return True, f"[표정] '{name}' 를 껐습니다."

                if self.active:
                    asyncio.run_coroutine_threadsafe(
                        self._trigger(self.active), self._loop
                    ).result(timeout=self.cfg["timeout_sec"])
                    self.active = None

                asyncio.run_coroutine_threadsafe(
                    self._trigger(name), self._loop
                ).result(timeout=self.cfg["timeout_sec"])
                self.active = name
                self.last_emotion = None
                return True, f"[표정] '{name}' 를 켰습니다."
            except Exception as e:
                return False, f"[표정] '{name}' 실행 실패: {type(e).__name__}: {e}"

    def all_off(self):
        """켜져 있는 표정을 끈다. 점검 창의 '전부 끄기'.

        토글이라 실제로 켜진 것은 항상 0개 또는 1개다."""
        if not self.connected:
            return False, "[표정] VTS 에 연결되어 있지 않습니다."

        self.cancel_reset()
        with self._lock:
            if not self.active:
                return True, "[표정] 켜져 있는 표정이 없습니다."
            name = self.active
            try:
                asyncio.run_coroutine_threadsafe(
                    self._trigger(name), self._loop
                ).result(timeout=self.cfg["timeout_sec"])
                self.active = None
                self.last_emotion = None
                return True, f"[표정] '{name}' 를 껐습니다."
            except Exception as e:
                return False, f"[표정] 끄기 실패: {type(e).__name__}: {e}"

    def set(self, emotion):
        """감정에 맞는 표정으로 바꾼다. 실패해도 발화를 막지 않는다.

        핫키는 토글이다. 끄려면 같은 핫키를 한 번 더 눌러야 한다.
        따라서 전환은 항상 [켜진 것 끄기] -> [새 것 켜기] 두 번이다.
        중립처럼 핫키가 빈 경우는 [켜진 것 끄기] 만 하고 끝낸다.

        반환: (적용된 감정, 걸린 초) — 건너뛰면 (None, 0.0)"""
        if not self.connected:
            return None, 0.0

        # 이전 발화가 걸어둔 중립 복귀 예약을 취소한다.
        # 취소하지 않으면 새 표정을 켠 직후에 그 타이머가 꺼버린다.
        self.cancel_reset()

        hotkey, resolved, known = self.emap.hotkey(emotion)

        # 매핑에 아예 없고 default 도 없으면 판단할 근거가 없다. 그냥 둔다.
        if not hotkey and not known:
            return None, 0.0

        with self._lock:
            # 이미 그 표정이면 다시 누를 필요가 없다.
            # 빈 핫키일 때는 켜진 것이 없으면 할 일이 없다.
            if self.cfg["skip_if_same"] and hotkey == (self.active or ""):
                return resolved, 0.0

            t0 = time.time()
            try:
                # 토글이므로 켜져 있는 것을 한 번 더 눌러 끈다
                if self.active:
                    asyncio.run_coroutine_threadsafe(
                        self._trigger(self.active), self._loop
                    ).result(timeout=self.cfg["timeout_sec"])
                    self.active = None

                # 빈 핫키(중립)면 여기서 끝. 맨 얼굴이 된다.
                if hotkey:
                    asyncio.run_coroutine_threadsafe(
                        self._trigger(hotkey), self._loop
                    ).result(timeout=self.cfg["timeout_sec"])
                    self.active = hotkey

                self.last_emotion = resolved
                return resolved, time.time() - t0
            except Exception as e:
                self._say("error", f"[표정] 전환 실패({emotion}): {type(e).__name__}: {e}")
                return None, time.time() - t0

    # ------------------------------------------------------------ 중립 복귀

    def cancel_reset(self):
        """예약된 중립 복귀를 취소한다."""
        t = self._reset_timer
        if t is not None:
            t.cancel()
            self._reset_timer = None

    def schedule_reset(self):
        """발화가 끝났으니 잠시 뒤 맨 얼굴로 돌아간다.

        발화 스레드를 붙잡지 않도록 타이머 스레드에서 처리한다.
        대기 중에 다음 발화가 시작되면 set() 이 이 예약을 취소한다."""
        if not self.connected:
            return
        self.cancel_reset()

        delay = float(self.cfg.get("reset_after_sec", 2.0))
        if delay <= 0:
            self.reset_now()
            return

        t = threading.Timer(delay, self.reset_now)
        t.daemon = True
        self._reset_timer = t
        t.start()

    def reset_now(self):
        """켜져 있는 표정을 끄고 맨 얼굴로 만든다."""
        self._reset_timer = None
        if not self.connected or not self.active:
            return
        with self._lock:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._trigger(self.active), self._loop
                ).result(timeout=self.cfg["timeout_sec"])
                self.active = None
                self.last_emotion = None
            except Exception as e:
                self._say("error", f"[표정] 중립 복귀 실패: {type(e).__name__}: {e}")
