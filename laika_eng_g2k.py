# -*- coding: utf-8 -*-
"""영어 발음기호(ARPAbet) -> 한글 표기. 2판.

1판에서 틀린 28개를 보고 고친 것
  - 모음 뒤 R 은 적지 않는다          start 스타르트 -> 스타트
  - ER 은 '어'                        server 서버
  - 약한 모음(AH0)은 철자를 따라간다  system 시스텀 -> 시스템
  - AA 는 철자가 o 면 '오'            boss 바스 -> 보스
  - 짧은 모음 뒤 끝소리 p t k b d g 는 받침   chat 채트 -> 챗
  - 자음 + W/Y 는 한 글자로 합친다    quest 크웨스트 -> 퀘스트
"""
import re

CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
JONG = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ',
        'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']

def syl(cho, jung, jong=''):
    return chr(0xAC00 + (CHO.index(cho) * 21 + JUNG.index(jung)) * 28 + JONG.index(jong))

VOWEL = {
    "AA": ["ㅏ"], "AE": ["ㅐ"], "AH": ["ㅓ"], "AO": ["ㅗ"], "AW": ["ㅏ", "ㅜ"],
    "AY": ["ㅏ", "ㅣ"], "EH": ["ㅔ"], "ER": ["ㅓ"], "EY": ["ㅔ", "ㅣ"],
    "IH": ["ㅣ"], "IY": ["ㅣ"], "OW": ["ㅗ"], "OY": ["ㅗ", "ㅣ"],
    "UH": ["ㅜ"], "UW": ["ㅜ"],
}
SHORT = {"AA", "AE", "AH", "EH", "IH", "UH", "AO"}       # 뒤 끝소리를 받침으로
CONS = {
    "B": ("ㅂ", "ㅡ", None, "ㅂ"), "CH": ("ㅊ", "ㅣ", None, None),
    "D": ("ㄷ", "ㅡ", None, "ㅅ"), "DH": ("ㄷ", "ㅡ", None, None),
    "F": ("ㅍ", "ㅡ", None, None), "G": ("ㄱ", "ㅡ", None, "ㄱ"),
    "HH": ("ㅎ", "ㅡ", None, None), "JH": ("ㅈ", "ㅣ", None, None),
    "K": ("ㅋ", "ㅡ", None, "ㄱ"), "L": ("ㄹ", "ㅡ", "ㄹ", "ㄹ"),
    "M": ("ㅁ", "ㅡ", "ㅁ", "ㅁ"), "N": ("ㄴ", "ㅡ", "ㄴ", "ㄴ"),
    "NG": ("ㅇ", "ㅡ", "ㅇ", "ㅇ"), "P": ("ㅍ", "ㅡ", None, "ㅂ"),
    "R": ("ㄹ", "ㅡ", None, None), "S": ("ㅅ", "ㅡ", None, None),
    "SH": ("ㅅ", "ㅣ", None, None), "T": ("ㅌ", "ㅡ", None, "ㅅ"),
    "TH": ("ㅅ", "ㅡ", None, None), "V": ("ㅂ", "ㅡ", None, None),
    "W": ("ㅇ", "ㅜ", None, None), "Y": ("ㅇ", "ㅣ", None, None),
    "Z": ("ㅈ", "ㅡ", None, None), "ZH": ("ㅈ", "ㅣ", None, None),
    "TS": ("ㅊ", "ㅡ", None, None),   # 뒤에 모음이 오면 convert 에서 ㅈ 으로 바꾼다
}
W_COMBO = {"ㅏ": "ㅘ", "ㅐ": "ㅙ", "ㅓ": "ㅝ", "ㅔ": "ㅞ", "ㅣ": "ㅟ", "ㅗ": "ㅝ", "ㅜ": "ㅜ"}
Y_COMBO = {"ㅏ": "ㅑ", "ㅐ": "ㅒ", "ㅓ": "ㅕ", "ㅔ": "ㅖ", "ㅗ": "ㅛ", "ㅜ": "ㅠ", "ㅣ": "ㅣ"}
# 철자 모음 -> 약한 모음(AH0)을 적을 때 쓰는 한글 모음
SPELL_V = {"a": "ㅏ", "e": "ㅔ", "i": "ㅣ", "o": "ㅓ", "u": "ㅓ", "y": "ㅣ"}


def convert(phones, spelling=""):
    ph = [p for p in phones]
    bare = [re.sub(r"\d", "", p) for p in ph]
    # T + S 는 '트스' 가 아니라 '츠' 로 읽는다 (shorts 쇼츠, pizza 피자)
    for k in range(len(bare) - 1, 0, -1):
        if bare[k] == "S" and bare[k - 1] == "T":
            bare[k - 1:k + 1] = ["TS"]
            ph[k - 1:k + 1] = ["TS"]
    sp_vowels = [c for c in spelling.lower() if c in "aeiouy"]
    vi = 0                      # 철자 모음을 몇 개 썼는지
    has_o = "o" in spelling.lower()
    out = []
    i = 0
    onset = None
    glide = None
    while i < len(bare):
        p = bare[i]
        if p in VOWEL:
            vs = list(VOWEL[p])
            # 약한 모음은 철자를 따라간다
            if p == "AH" and ph[i].endswith("0") and vi < len(sp_vowels):
                vs = [SPELL_V.get(sp_vowels[vi], "ㅓ")]
            elif p == "AA" and has_o:
                vs = ["ㅗ"]
            if vi < len(sp_vowels):
                vi += 1
            # -tion / -sion 은 '션' 으로 적는다 (hibernation 하이버네이션)
            # 단어 끝의 약한 모음 + L/N 은 철자 꼬리를 따라간다
            #   -le  테이블, 타이틀      -el  레벨, 모델
            #   -al  튜토리얼            -on/-en/-in  버튼, 리슨
            if (ph[i].endswith("0") and i + 2 == len(bare)
                    and bare[i + 1] in ("L", "N")):
                tail = spelling.lower()[-2:]
                pick = {"le": "ㅡ", "el": "ㅔ", "al": "ㅓ", "ol": "ㅗ", "il": "ㅡ",
                        "on": "ㅡ", "en": "ㅡ", "in": "ㅡ"}.get(tail)
                if pick:
                    vs = [pick]
            if (i > 0 and bare[i - 1] == "ZH" and ph[i].endswith("0")
                    and i + 1 < len(bare) and bare[i + 1] == "N"):
                vs = ["ㅓ"]
            if (i > 0 and bare[i - 1] == "SH" and ph[i].endswith("0")
                    and i + 1 < len(bare) and bare[i + 1] == "N"):
                vs = ["ㅕ"]
            if glide == "W":
                vs[0] = W_COMBO.get(vs[0], vs[0])
            elif glide == "Y":
                vs[0] = Y_COMBO.get(vs[0], vs[0])
            glide = None
            cho = onset or "ㅇ"
            onset = None
            if p == "ER" and i + 1 < len(bare) and bare[i + 1] in VOWEL:
                onset_after_er = True          # 어 + ㄹ 로 잇는다
            else:
                onset_after_er = False
            jong = ''
            nxt = bare[i + 1] if i + 1 < len(bare) else None
            nxt2 = bare[i + 2] if i + 2 < len(bare) else None
            r_dropped = False
            tail_r = (nxt == "R" and nxt2 is None and p != "ER")
            if nxt == "R" and (nxt2 is None or nxt2 not in VOWEL):
                i += 1                      # 모음 뒤 R 은 적지 않는다
                r_dropped = True
                nxt, nxt2 = nxt2, (bare[i + 2] if i + 2 < len(bare) else None)
            elif nxt == "R" and nxt2 is None:
                i += 1
                out_tail_r = True
            if nxt is None and bare[i] in VOWEL and i + 1 < len(bare):
                pass
            if nxt in CONS and (nxt2 is None or nxt2 not in VOWEL):
                cand = CONS[nxt][2]
                # 짧은 모음 + 끝소리 p t k b d g -> 받침 (chat 챗, 업데이트)
                if (cand is None and p in SHORT and not r_dropped
                        and CONS[nxt][3] and (nxt2 is None or nxt2 in CONS)):
                    cand = CONS[nxt][3]
                if cand:
                    jong = cand
                    i += 1
                    # 모음 사이의 L 은 받침과 초성 둘 다 쓴다 (hello 헬로)
                    if nxt == "L" and nxt2 in VOWEL:
                        onset = "ㄹ"
            if len(vs) == 1:
                out.append(syl(cho, vs[0], jong))
            else:
                out.append(syl(cho, vs[0]))
                out.append(syl("ㅇ", vs[1], jong))
            if onset_after_er:
                onset = "ㄹ"
            if tail_r:
                out.append(syl("ㅇ", "ㅓ"))      # 단어 끝 R -> 어 (engineer 엔지니어)
            i += 1
            continue
        if p in CONS:
            nxt = bare[i + 1] if i + 1 < len(bare) else None
            if p == "R" and nxt is None and out:
                out.append(syl("ㅇ", "ㅓ"))   # 끝의 R -> 어
                i += 1
                continue
            if p in ("W", "Y") and nxt in VOWEL:
                glide = p
                i += 1
                continue
            cho, solo, _, _ = CONS[p]
            if p == "TS" and nxt in VOWEL:
                cho = "ㅈ"                      # pizza 피자
            # 자음 + W/Y + 모음 이면 한 글자로 합친다 (quest -> 퀘)
            if (nxt in ("W", "Y") and i + 2 < len(bare) and bare[i + 2] in VOWEL
                    and (nxt == "Y" or cho in ("ㅋ", "ㄱ", "ㅎ"))):
                # quest 퀘스트, queen 퀸 은 합치고 twitch 트위치, sweet 스위트 는 안 합친다
                onset = cho
                i += 1
                continue
            if nxt in VOWEL:
                onset = cho
                # 모음 사이의 L 은 앞 글자 받침으로도 적는다 (hello 헬로)
                if p == "L" and out and i > 0 and bare[i - 1] in VOWEL:
                    ch = out[-1]
                    if "\uac00" <= ch <= "\ud7a3" and (ord(ch) - 0xAC00) % 28 == 0:
                        out[-1] = chr(ord(ch) + JONG.index("ㄹ"))
            elif nxt == "L" and i + 2 < len(bare) and bare[i + 2] in VOWEL:
                out.append(syl(cho, solo, "ㄹ"))   # 프 + ㄹ -> 플
                onset = "ㄹ"
                i += 1
            else:
                out.append(syl(cho, solo))
            i += 1
            continue
        i += 1
    return "".join(out)


def word_to_hangul(word, cmu):
    key = word.lower()
    if key not in cmu:
        return None
    return convert(cmu[key][0], key)
