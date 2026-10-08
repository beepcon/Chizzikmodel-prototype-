# =============================================================================
# TTS 텍스트 정규화
#
# GPT-SoVITS 는 넘긴 글자를 그대로 읽는다.
# "LP 8000" 을 넘기면 "엘피 팔영영영" 처럼 자릿수를 하나씩 읽어버린다.
# 그래서 합성하기 전에 사람이 읽는 방식으로 바꿔서 넘긴다.
#
#   LP 8000 대 8000   ->  라이프포인트 팔천 대 팔천
#   공격력 2500       ->  공격력 이천오백
#   별 8개            ->  별 여덟 개
#   턴 2 메인 1       ->  턴 이 메인 일
#
# 페르소나에도 같은 지시를 넣지만 LLM 이 항상 지키지는 않는다.
# (감정 enum 도 21문장 중 3개가 목록을 벗어났다)
# 그래서 코드 쪽에서 한 번 더 거른다.
#
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   약어와 읽는 법 : tts_dict.json 에서 읽는다. 없으면 초안을 만든다.
#   이미 있으면 덮어쓰지 않는다. 카드 이름이 늘어도 파일만 고치면 된다.
# =============================================================================

import json
import re
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


DICT_NAME = "tts_dict.json"
# 영어 단어 -> 한글 발음. cmudict 로 자동 생성한 큰 사전이다.
# 정규식에 합치지 않고 단어 단위로 조회한다.
# (117,250 개를 정규식 하나로 합치면 만드는 데만 443초, 메모리 1,037MB 가 든다.
#  조회 방식은 문장당 0.001ms 다. 2026-10-06 측정)
ENG_DICT_NAME = "laika_eng_dict.json"
_ENG_WORD = re.compile(r"[A-Za-z]{2,}")

# 파일이 없을 때만 만드는 초안.
# 방송 내용에 맞춰 계속 늘려 쓰는 파일이다.
DICT_TEMPLATE = {
    "_설명": [
        "TTS 로 읽기 전에 바꿔칠 표기를 적습니다.",
        "왼쪽에 쓰인 그대로 나오면 오른쪽 발음으로 바꿔 읽습니다.",
        "긴 것부터 먼저 바꾸므로 'LP' 와 'LPS' 가 섞여 있어도 됩니다.",
        "대소문자를 구분하지 않습니다.",
        "숫자는 사전과 별개로 자동 변환됩니다. 여기 적을 필요 없습니다.",
        "이 파일만 고치면 코드 수정 없이 발음이 바뀝니다.",
    ],
    "약어": {
        "LP": "라이프포인트",
        "ATK": "공격력",
        "DEF": "수비력",
        "HP": "체력",
        "MP": "마나",
        "EX": "엑스트라",
        "AI": "에이아이",
        "PC": "피시",
        "TTS": "티티에스",
        "API": "에이피아이",
        "GG": "지지",
        "vs": "대",
    },
    "고유명사": {
    },
    # 영어 단어를 한글 발음으로 바꾼다. 여기에 없는 영어는 한 글자씩 읽힌다.
    # 앞뒤에 알파벳이 더 붙은 경우는 바꾸지 않는다 (game 과 gamer 는 다르다).
    "영단어": {
    },
}

# 숫자 뒤에 오면 고유어(하나 둘 셋)로 읽어야 하는 단위.
# "8개" 는 "팔 개" 가 아니라 "여덟 개" 다.
NATIVE_UNITS = [
    "개", "명", "마리", "장", "번", "살", "잔", "병", "그릇", "켤레",
    "벌", "자루", "채", "척", "송이", "포기", "판", "군데", "가지", "달",
]

_SINO_DIGIT = "영일이삼사오육칠팔구"
_SINO_UNIT4 = ["", "십", "백", "천"]
_SINO_BIG = ["", "만", "억", "조"]

_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯",
                "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스무", "서른", "마흔", "쉰",
                "예순", "일흔", "여든", "아흔"]
_NATIVE_TENS_MID = ["", "열", "스물", "서른", "마흔", "쉰",
                    "예순", "일흔", "여든", "아흔"]


def sino(n: int) -> str:
    """한자어 수 읽기. 8000 -> 팔천, 6200 -> 육천이백"""
    if n == 0:
        return "영"
    if n < 0:
        return "마이너스 " + sino(-n)

    groups = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000

    out = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        s = ""
        for pos in range(3, -1, -1):
            d = (g // (10 ** pos)) % 10
            if d == 0:
                continue
            # 십/백/천 앞의 1 은 읽지 않는다. 15 -> 십오 (일십오 아님)
            s += ("" if (d == 1 and pos > 0) else _SINO_DIGIT[d]) + _SINO_UNIT4[pos]
        out.append(s + _SINO_BIG[i])
    return "".join(out)


def native(n: int) -> str:
    """고유어 수 읽기. 단위 앞 형태로 낸다. 8 -> 여덟, 21 -> 스물한

    99 를 넘으면 한자어로 넘긴다. 실제로 그렇게 읽는다."""
    if n <= 0 or n > 99:
        return sino(n)
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _NATIVE_ONES[ones]
    if ones == 0:
        return _NATIVE_TENS[tens]
    return _NATIVE_TENS_MID[tens] + _NATIVE_ONES[ones]


def _read_number(num_str, following):
    """숫자 하나를 상황에 맞게 읽는다.

    뒤에 고유어 단위가 붙으면 고유어로, 아니면 한자어로 읽는다.
    "8개" 는 "여덟 개" 로, 단위와 띄어 읽어야 자연스럽다."""
    try:
        n = int(num_str)
    except ValueError:
        return num_str

    nxt = following.lstrip()
    for u in NATIVE_UNITS:
        if nxt.startswith(u):
            # 단위와 붙으면 "여덟개" 로 뭉쳐 읽힌다. 한 칸 띄운다.
            return native(n) + ("" if following[:1].isspace() else " ")
    return sino(n)


class TTSNormalizer:
    def __init__(self, log=None):
        self.log = log
        self.path = base_dir() / DICT_NAME
        self.data = self._load()
        self.eng = self._load_eng()
        self._build()

    def _say(self, kind, msg):
        if self.log:
            self.log(kind, msg)

    def _load(self):
        if not self.path.exists():
            self.path.write_text(
                json.dumps(DICT_TEMPLATE, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self._say("system", f"[발음] 사전 초안을 만들었습니다: {self.path.name}")
            return json.loads(json.dumps(DICT_TEMPLATE))
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            self._say("error", f"[발음] 사전 읽기 실패, 기본값 사용: {e}")
            return json.loads(json.dumps(DICT_TEMPLATE))

    def _load_eng(self):
        """큰 영단어 사전을 읽는다. 없으면 빈 사전으로 둔다."""
        path = base_dir() / ENG_DICT_NAME
        if not path.exists():
            self._say("system", f"[발음] {ENG_DICT_NAME} 이 없습니다. 영단어는 한 글자씩 읽힙니다.")
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            words = raw.get("단어") if isinstance(raw, dict) else None
            if words is None:
                words = raw if isinstance(raw, dict) else {}
            return {k.lower(): v for k, v in words.items() if k and v}
        except Exception as e:
            self._say("error", f"[발음] 영단어 사전 읽기 실패: {e}")
            return {}

    def reload(self):
        """방송 중에 사전을 고쳐도 반영되게 한다."""
        self.data = self._load()
        self.eng = self._load_eng()
        self._build()

    def _build(self):
        """사전을 하나의 정규식으로 합친다.

        긴 것부터 매칭해야 'LP' 가 'LPS' 를 잘라먹지 않는다."""
        pairs = {}
        eng = set()
        for section in ("약어", "고유명사", "영단어"):
            for k, v in (self.data.get(section) or {}).items():
                if k and v:
                    pairs[k] = v
                    if section == "영단어":
                        eng.add(k.lower())
        self.pairs = pairs
        self.eng_keys = eng

        if not pairs:
            self.rx = None
            return
        keys = sorted(pairs, key=len, reverse=True)
        parts = []
        for k in keys:
            pat = re.escape(k)
            # 알파벳으로 된 항목은 앞뒤에 알파벳이 더 붙으면 바꾸지 않는다.
            #   영단어: game 이 gamer 를 잘라먹으면 "게임r" 이 된다
            #   약어  : MP 가 computer 안의 mp 에 걸려 "씨오마나유티이아르" 가 된다
            #           (2026-10-07 확인)
            # 숫자는 경계로 보지 않는다. "2000LP" 는 지금처럼 바뀌어야 한다.
            if re.fullmatch(r"[A-Za-z ]+", k):
                pat = r"(?<![A-Za-z])" + pat + r"(?![A-Za-z])"
            parts.append(pat)
        self.rx = re.compile("|".join(parts), re.IGNORECASE)

    # ------------------------------------------------------------ 변환

    def normalize(self, text):
        """TTS 에 넘기기 전 마지막으로 거른다."""
        if not text:
            return text

        out = text

        # 1) 사전 치환 (대소문자 무시)
        #    "2000LP" 처럼 숫자에 붙어 있으면 "이천라이프포인트" 로 뭉친다.
        #    앞뒤가 글자나 숫자면 한 칸 띄워 준다.
        if self.rx is not None:
            lower = {k.lower(): v for k, v in self.pairs.items()}

            def sub_dict(m):
                val = lower.get(m.group(0).lower(), m.group(0))
                before = out[m.start() - 1: m.start()]
                after = out[m.end(): m.end() + 1]
                if before and (before.isalnum() or "\uac00" <= before <= "\ud7a3"):
                    val = " " + val
                if after and (after.isdigit() or "\uac00" <= after <= "\ud7a3"):
                    # 조사(이/가/를/은/는...)가 붙는 경우는 띄우면 어색하다
                    if not _is_particle(after):
                        val = val + " "
                return val

            out = self.rx.sub(sub_dict, out)

        # 2) 자릿수 구분 쉼표 제거. 8,000 -> 8000
        out = re.sub(r"(?<=\d),(?=\d{3}\b)", "", out)

        # 3) 소수점. 3.5 -> 삼 점 오
        out = re.sub(
            r"(\d+)\.(\d+)",
            lambda m: f"{sino(int(m.group(1)))} 점 "
                      + " ".join(_SINO_DIGIT[int(d)] for d in m.group(2)),
            out)

        # 4) 정수. 뒤에 오는 단위를 보고 읽는 방식을 고른다
        def repl(m):
            return _read_number(m.group(0), out[m.end():m.end() + 4])

        out = re.sub(r"\d+", repl, out)

        # 5) 숫자 뒤 기호. TTS 가 못 읽는 것들이다
        out = re.sub(r"\s*%", " 퍼센트", out)
        out = re.sub(r"(?<=[가-힣\d])\s*[~∼](?=[가-힣\d])", "에서 ", out)

        # 6) 남은 영어 단어는 큰 사전에서 찾아 한글로 바꾼다
        if self.eng:
            out = _ENG_WORD.sub(
                lambda m: self.eng.get(m.group(0).lower(), m.group(0)), out)

        # 7) 그래도 남은 알파벳은 한 글자씩 한글 음으로. 사전에 없는 약어 대비
        out = re.sub(r"[A-Za-z]{1,6}", lambda m: _spell(m.group(0)), out)

        # 8) 공백 정리
        out = re.sub(r"\s{2,}", " ", out).strip()
        return out


_ALPHA = {
    "a": "에이", "b": "비", "c": "씨", "d": "디", "e": "이", "f": "에프",
    "g": "지", "h": "에이치", "i": "아이", "j": "제이", "k": "케이",
    "l": "엘", "m": "엠", "n": "엔", "o": "오", "p": "피", "q": "큐",
    "r": "아르", "s": "에스", "t": "티", "u": "유", "v": "브이",
    "w": "더블유", "x": "엑스", "y": "와이", "z": "제트",
}


def _spell(s):
    return "".join(_ALPHA.get(c.lower(), c) for c in s)


# 치환어 뒤에 바로 붙는 조사. 이 앞에서는 띄우지 않는다.
_PARTICLES = set("이가은는을를도만의에와과로랑야여라며서까부터")


def _is_particle(ch):
    return ch in _PARTICLES
