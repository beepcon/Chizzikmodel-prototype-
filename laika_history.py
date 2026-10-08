# =============================================================================
# 대화 히스토리
#
# 기존 구조의 문제 네 가지를 고친다.
#
#   1) history[-20:] 의 20 은 턴이 아니라 메시지 개수였다. 실제 보존은 10턴.
#   2) 방송을 껐다 켜면 LaikaCore 가 새로 만들어져 전부 사라졌다.
#   3) API 호출이 실패하면 user 만 들어가고 assistant 가 빠져 짝이 깨졌다.
#      그 상태에서 슬라이스가 assistant 부터 시작하면 API 가 400 을 낸다.
#   4) 화면(비전) 이 채팅과 같은 통에 쌓여 서로를 밀어냈다.
#
# 해결
#   - 턴 단위로 세고, 개수는 config 에서 읽는다
#   - 세션을 파일로 저장하고 시작할 때 복원한다
#   - 턴을 (user, assistant) 쌍으로만 저장한다. 실패한 턴은 아예 안 들어간다
#   - 채팅과 비전을 별도 통에 담고, 전송할 때만 합친다
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   보존 턴 수 / 저장 여부 / 저장 위치 : config.json 의 history 항목에서 읽는다
#   항목이 없으면 DEFAULTS 로 채운다. 나중에 항목이 늘어도 기존 파일은 그대로 쓴다.
# =============================================================================

import json
import sys
import time
from pathlib import Path

DEFAULTS = {
    "chat_turns": 20,        # 채팅/음성 대화를 몇 턴 기억할지
    "vision_turns": 3,       # 화면 반응을 몇 턴 기억할지 (별도 통)
    "persist": True,         # 방송을 껐다 켜도 이어갈지
    "dir": "sessions",       # 저장 폴더
    "resume_within_hours": 12,   # 이 시간 안의 세션만 이어받는다
}


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class History:
    """턴 단위 대화 기록.

    한 턴 = {"user": <content>, "assistant": <str>, "kind": "chat"|"vision", "t": <float>}
    user 의 content 는 문자열이거나 Anthropic 형식의 블록 리스트다.
    """

    def __init__(self, cfg=None, log=None):
        raw = (cfg or {}).get("history") or {}
        self.cfg = dict(DEFAULTS)
        self.cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        self.log = log

        self.chat = []       # [turn]
        self.vision = []     # [turn]
        self.session_path = None

    # ------------------------------------------------------------ 저장 위치

    def _dir(self) -> Path:
        d = base_dir() / self.cfg["dir"]
        d.mkdir(exist_ok=True)
        return d

    def _say(self, kind, msg):
        if self.log:
            self.log(kind, msg)

    # ------------------------------------------------------------ 추가

    def add(self, user_content, assistant_text, kind="chat"):
        """한 턴을 통째로 추가한다.

        쌍으로만 넣기 때문에 짝이 깨지지 않는다.
        assistant 가 없는 턴(=API 실패)은 애초에 여기까지 오지 않는다."""
        turn = {"user": user_content, "assistant": assistant_text,
                "kind": kind, "t": time.time()}

        box = self.vision if kind == "vision" else self.chat
        box.append(turn)

        # 통이 무한정 커지지 않도록 여유분만 남기고 잘라낸다.
        # 전송용 슬라이스와 별개로, 메모리에서도 정리해야 한다.
        limit = self.cfg["vision_turns"] if kind == "vision" else self.cfg["chat_turns"]
        keep = max(limit * 3, limit + 10)
        if len(box) > keep:
            del box[:-keep]

        self.save()
        return turn

    # ------------------------------------------------------------ 전송용

    def messages(self):
        """Anthropic messages 형식으로 만든다.

        채팅과 화면을 각자 잘라낸 뒤 시간순으로 섞는다.
        이렇게 하면 화면 반응이 많아도 채팅 기억을 밀어내지 않는다.

        반드시 user 로 시작하고 user/assistant 가 번갈아 나온다."""
        chat = self.chat[-self.cfg["chat_turns"]:] if self.cfg["chat_turns"] else []
        vis = self.vision[-self.cfg["vision_turns"]:] if self.cfg["vision_turns"] else []

        turns = sorted(chat + vis, key=lambda x: x["t"])

        out = []
        for t in turns:
            out.append({"role": "user", "content": t["user"]})
            out.append({"role": "assistant", "content": t["assistant"]})
        return out

    def stats(self):
        return {
            "채팅_보관": len(self.chat),
            "채팅_전송": min(len(self.chat), self.cfg["chat_turns"]),
            "화면_보관": len(self.vision),
            "화면_전송": min(len(self.vision), self.cfg["vision_turns"]),
            "전송_메시지수": len(self.messages()),
        }

    # ------------------------------------------------------------ 저장과 복원

    def save(self):
        if not self.cfg["persist"]:
            return
        try:
            if self.session_path is None:
                stamp = time.strftime("%Y%m%d_%H%M%S")
                self.session_path = self._dir() / f"session_{stamp}.json"

            payload = {
                "저장시각": time.strftime("%Y-%m-%d %H:%M:%S"),
                "마지막활동": time.time(),
                "chat": self.chat,
                "vision": self.vision,
            }
            self.session_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            # 저장 실패로 방송이 멈추면 안 된다
            self._say("error", f"[히스토리] 저장 실패: {type(e).__name__}: {e}")

    def resume(self):
        """가장 최근 세션을 이어받는다.

        너무 오래된 세션까지 이어받으면 어제 대화에 오늘 답하는 꼴이 되므로
        resume_within_hours 안의 것만 쓴다."""
        if not self.cfg["persist"]:
            return False
        try:
            files = sorted(self._dir().glob("session_*.json"))
            if not files:
                return False

            latest = files[-1]
            data = json.loads(latest.read_text(encoding="utf-8"))

            age_h = (time.time() - float(data.get("마지막활동", 0))) / 3600
            if age_h > self.cfg["resume_within_hours"]:
                self._say("system",
                          f"[히스토리] 마지막 세션이 {age_h:.1f}시간 전이라 새로 시작합니다.")
                return False

            self.chat = list(data.get("chat", []))
            self.vision = list(data.get("vision", []))
            self.session_path = latest

            self._say("system",
                      f"[히스토리] 이어받음: 채팅 {len(self.chat)}턴, "
                      f"화면 {len(self.vision)}턴 ({age_h:.1f}시간 전)")
            return True
        except Exception as e:
            self._say("error", f"[히스토리] 복원 실패: {type(e).__name__}: {e}")
            return False

    def clear(self):
        """기억을 비운다. 파일은 지우지 않고 새 세션을 시작한다."""
        self.chat, self.vision = [], []
        self.session_path = None
        self._say("system", "[히스토리] 비웠습니다.")
