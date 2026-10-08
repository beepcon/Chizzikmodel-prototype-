# -*- coding: utf-8 -*-
"""영어 발음기호(ARPAbet) -> 한글 표기. 3판.

2판에서 외래어 표기법 제3장 제1절(영어) 용례 98개 중 32개가 틀려 고친 것 (2026-10-08)
  - 제1항  유음·비음 앞 p t k 는 받침이 아니다     mattress 맷레스 -> 매트리스
  - 제3항  sh + 모음 은 샤 섀 셔 셰 쇼 슈 시, 자음 앞은 슈   shopping 소핑 -> 쇼핑
  - 제4항  [dz] 는 즈                              odds 옷즈 -> 오즈
  - 제5항  모음 사이 [ŋ] 은 받침 ㅇ                 hanging 해잉 -> 행잉
  - 제6항  받침 ㄹ 뒤 모음 없는 m n 은 름/른         film 필므 -> 필름
  - 제8항  [auə] 는 아워                           tower 타우어 -> 타워
  - 제9항  [d][l][n] + [jə] 는 디어 리어 니어, [iə]+자음은 이어   union 윤은 -> 유니언
  - 제10항 합성어는 단어마다 따로 적는다             bookend 부켄드 -> 북엔드
  - -in 꼬리는 s/z 뒤에서만 '은'                     virgin 버즌 -> 버진
  - 단어 끝 [ɑːr] 는 '아'                           car 카어 -> 카, star 스타어 -> 스타 (386개)
결과: 98개 중 82개 일치 (83.7%). 손교정 사전(tts_dict.json 영단어, cmudict 에 있는 123개)과는
      48개 -> 56개 일치, 2판에서 맞던 것 중 틀려진 것 0개. 방송 문장 770개의 변환 결과는 그대로다.

남은 16개와 이유
  - b d g 받침 (제2항)  zigzag 직잭, kidnap 킷냅, lobster 롭스터, wag 왝, shrub 슈럽, headlight 헷라이트
      표기법대로 하면 good 구드, big 비그, job 조브 처럼 관용 표기와 어긋난다.
      STRICT_VOICED = True 로 두면 표기법을 따른다 (98개 중 88개, 손교정 59개 일치).
  - 약한 모음을 철자로 적는 2판 규칙   mattress 매트레스, sickness 시크네스, Hamlet 햄렛, battalion 바탤리언
  - 표기법은 영국식 발음 기준        mask 매스크, want 완트 (WA_TO_WO 는 98개 기준 변화 없음)
  - cmudict 발음이 다름              mirage 머라지(ER0), whistle 위슬(HH 없음), penguin 펭근(AH0)
  - 합성어 빈도 조건에 걸림          bookmaker 부크메이커

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
    "AY": ["ㅏ", "ㅣ"], "AWER": ["ㅏ", "ㅝ"], "EH": ["ㅔ"], "ER": ["ㅓ"], "EY": ["ㅔ", "ㅣ"],
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
    "DZ": ("ㅈ", "ㅡ", None, None),   # odds 오즈 (표기법 제3장 제4항)
}
W_COMBO = {"ㅏ": "ㅘ", "ㅐ": "ㅙ", "ㅓ": "ㅝ", "ㅔ": "ㅞ", "ㅣ": "ㅟ", "ㅗ": "ㅝ", "ㅜ": "ㅜ"}
Y_COMBO = {"ㅏ": "ㅑ", "ㅐ": "ㅒ", "ㅓ": "ㅕ", "ㅔ": "ㅖ", "ㅗ": "ㅛ", "ㅜ": "ㅠ", "ㅣ": "ㅣ"}
# 3판 실험 스위치. 표기법을 그대로 따르면 관용 표기(굿, 잡, 웹)와 어긋나는 것들이다.
STRICT_VOICED = False   # True: b d g 는 짧은 모음 뒤 끝소리여도 받침으로 안 적는다 (제2항)
WA_TO_WO = False        # True: 철자 a 인 W + AA 를 '워' 로 (want 원트, watch 워치)
LIQ_NASAL = {"L", "R", "M", "N", "NG", "W", "Y"}   # 제1항 2: 이 앞의 p t k 는 받침이 아니다

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
    # D + Z 는 '즈' (odds 오즈), AW + ER 은 '아워' (tower 타워, 제3장 제8항)
    for k in range(len(bare) - 1, 0, -1):
        if bare[k] == "Z" and bare[k - 1] == "D":
            bare[k - 1:k + 1] = ["DZ"]
            ph[k - 1:k + 1] = ["DZ"]
        elif bare[k] == "ER" and bare[k - 1] == "AW":
            bare[k - 1:k + 1] = ["AWER"]
            ph[k - 1:k + 1] = ["AWER" + ph[k - 1][-1:]]
    sp_vowels = [c for c in spelling.lower() if c in "aeiouy"]
    vi = 0                      # 철자 모음을 몇 개 썼는지
    has_o = "o" in spelling.lower()
    out = []
    i = 0
    onset = None
    glide = None
    sh_onset = False            # SH 다음 모음은 샤 섀 셔 셰 쇼 슈 시 (제3항)
    while i < len(bare):
        p = bare[i]
        if p in VOWEL:
            vs = list(VOWEL[p])
            # 약한 모음은 철자를 따라간다
            if p == "AH" and ph[i].endswith("0") and vi < len(sp_vowels):
                vs = [SPELL_V.get(sp_vowels[vi], "ㅓ")]
            elif p == "AA" and has_o:
                vs = ["ㅗ"]
            elif p == "AA" and WA_TO_WO and glide == "W" and vi < len(sp_vowels) \
                    and sp_vowels[vi] == "a":
                vs = ["ㅓ"]
            # [iə] 다음에 자음이 오면 '이어' (Indian 인디언, guardian 가디언)
            if (p == "AH" and ph[i].endswith("0") and i > 0 and bare[i - 1] == "IY"
                    and i + 1 < len(bare)):
                vs = ["ㅓ"]
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
                # -in 은 s/z 소리 뒤에서만 '은' (cousin 커즌). 나머지는 '인' (virgin 버진, cabin 캐빈)
                if (tail == "in" and bare[i] == "IH"
                        and not (i > 0 and bare[i - 1] in ("S", "Z"))):
                    pick = "ㅣ"
                # 니언 리언 디언 의 끝 '언' 은 철자 꼬리(-on, -an)를 따르지 않는다 (union 유니언)
                if i > 0 and bare[i - 1] == "Y":
                    pick = "ㅓ"
                if pick:
                    vs = [pick]
            # [jə] 가 단어 끝이면 철자 a 는 '아' (california 캘리포니아, australia 오스트레일리아)
            if (p == "AH" and ph[i].endswith("0") and i > 0 and bare[i - 1] == "Y"
                    and i + 1 == len(bare)):
                vs = ["ㅏ"] if spelling.lower().endswith("a") else ["ㅓ"]
            if (i > 0 and bare[i - 1] == "ZH" and ph[i].endswith("0")
                    and i + 1 < len(bare) and bare[i + 1] == "N"):
                vs = ["ㅓ"]
            if (i > 0 and bare[i - 1] == "SH" and ph[i].endswith("0")
                    and i + 1 < len(bare) and bare[i + 1] == "N"):
                vs = ["ㅕ"]
            if glide == "W":
                vs[0] = W_COMBO.get(vs[0], vs[0])
            elif glide == "Y" or sh_onset:
                vs[0] = Y_COMBO.get(vs[0], vs[0])
            glide = None
            sh_onset = False
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
            # 단어 끝 R 을 '어' 로 적는 것은 [ɪər] [ɛər] [ʊər] 이다 (engineer 엔지니어, hair 헤어, tour 투어).
            # [ɑːr] 는 '아' 로 끝난다 (car 카, star 스타). 2판에서는 AA 도 '어' 가 붙어 카어 가 됐다 (10-08)
            tail_r = (nxt == "R" and nxt2 is None and p not in ("ER", "AA"))
            if nxt == "R" and (nxt2 is None or nxt2 not in VOWEL):
                i += 1                      # 모음 뒤 R 은 적지 않는다
                r_dropped = True
                nxt, nxt2 = nxt2, (bare[i + 2] if i + 2 < len(bare) else None)
            elif nxt == "R" and nxt2 is None:
                i += 1
                out_tail_r = True
            if nxt is None and bare[i] in VOWEL and i + 1 < len(bare):
                pass
            # [d][l][n] + [jə] 는 자음 쪽에서 디어 리어 니어 로 적으므로 받침으로 가져오지 않는다
            dyn = (nxt in ("D", "L", "N") and nxt2 == "Y" and i + 3 < len(bare)
                   and bare[i + 3] == "AH" and ph[i + 3].endswith("0"))
            if nxt in CONS and (nxt2 is None or nxt2 not in VOWEL) and not dyn:
                cand = CONS[nxt][2]
                # 짧은 모음 + 끝소리 p t k b d g -> 받침 (chat 챗, 업데이트)
                # 3판: 제1항 2 - 뒤 자음이 유음·비음(+반모음)이면 받침이 아니다 (mattress 매트리스)
                #       제2항  - b d g 는 STRICT_VOICED 일 때 받침이 아니다 (zigzag 지그재그)
                if (cand is None and p in SHORT and not r_dropped
                        and CONS[nxt][3] and (nxt2 is None or nxt2 in CONS)
                        and (nxt2 is None or nxt2 not in LIQ_NASAL)
                        and not (STRICT_VOICED and nxt in ("B", "D", "G"))):
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
            if p == "SH":
                if nxt in VOWEL:
                    onset = cho
                    sh_onset = True
                    i += 1
                    continue
                # 자음 앞 [ʃ] 는 '슈' (shrub 슈러브, shrimp 슈림프). 단어 첫머리만 그렇게 한다.
                # 가운데·뒤는 '시' 로 둔다 (mushroom 머시룸 이 표준이고, finished 피니시트 가 자연스럽다)
                if nxt is not None and i == 0:
                    solo = "ㅠ"
            # 모음 사이 [ŋ] 은 앞 음절 받침 ㅇ (hanging 행잉, 제5항)
            if p == "NG" and nxt in VOWEL and out:
                ch = out[-1]
                if "\uac00" <= ch <= "\ud7a3" and (ord(ch) - 0xAC00) % 28 == 0:
                    out[-1] = chr(ord(ch) + JONG.index("ㅇ"))
                onset = "ㅇ"
                i += 1
                continue
            # 받침 ㄹ 뒤, 모음 없는 m n 은 '름/른' (film 필름, helm 헬름, 제6항)
            if (p in ("M", "N") and i > 0 and bare[i - 1] == "L" and nxt not in VOWEL
                    and out and (ord(out[-1]) - 0xAC00) % 28 == JONG.index("ㄹ")):
                out.append(syl("ㄹ", "ㅡ", "ㅁ" if p == "M" else "ㄴ"))
                i += 1
                continue
            # [d] [l] [n] + [jə] 는 디어 리어 니어 (union 유니언, battalion 버탤리언, 제9항)
            if (p in ("D", "L", "N") and nxt == "Y" and i + 2 < len(bare)
                    and bare[i + 2] == "AH" and ph[i + 2].endswith("0")):
                if p == "L" and out and i > 0 and bare[i - 1] in VOWEL:
                    ch = out[-1]
                    if "\uac00" <= ch <= "\ud7a3" and (ord(ch) - 0xAC00) % 28 == 0:
                        out[-1] = chr(ord(ch) + JONG.index("ㄹ"))
                out.append(syl(cho, "ㅣ"))
                onset = None
                i += 2
                continue
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


def _bare(phones):
    return [re.sub(r"\d", "", p) for p in phones]


def split_compound(key, cmu, min_len=3, zipf=None, min_zipf=3.0):
    """따로 설 수 있는 두 단어의 합성어면 (앞, 뒤) 를 돌려준다. 제3장 제10항.

    발음기호가 두 단어를 그대로 이어 붙인 것과 같을 때만 나눈다(강세 제외).
    철자만 맞고 소리가 다른 경우(season = sea + son)는 나누지 않는다.
    zipf 는 단어 빈도 함수(wordfreq.zipf_frequency 의 영어판). 없으면 나누지 않는다.
    두 단어가 모두 흔하고(min_zipf 이상) 합친 말이 두 단어보다 드물 때만 합성어로 본다.
    (빈도 조건이 없으면 september = sept + ember, itself = its + elf 처럼 잘못 나뉜다)"""
    if zipf is None:
        return None
    whole_ph = cmu[key][0]
    whole = _bare(whole_ph)
    for k in range(min_len, len(key) - min_len + 1):
        a, b = key[:k], key[k:]
        # 앞 단어가 R 로 끝나면 나누지 않는다. 단독 표기의 끝 R('카어')이 합성어 안으로 들어온다
        # (carport 카어포트). 끝 R 표기는 2판 그대로다.
        if (a in cmu and b in cmu and _bare(cmu[a][0])[-1] != "R"
                and _bare(cmu[a][0]) + _bare(cmu[b][0]) == whole):
            # 합성어는 뒤 단어에도 강세가 남는다 (bookend EH2, headlight AY2).
            # 강세가 없으면 접미사거나 한 단어다 (looking IH0, person AH0) -> 나누지 않는다
            tail = whole_ph[len(cmu[a][0]):]
            if any(p[-1:] in ("1", "2") for p in tail):
                za, zb = zipf(a), zipf(b)
                if min(za, zb) >= min_zipf and zipf(key) < min(za, zb):
                    return a, b
    return None


def word_to_hangul(word, cmu, zipf=None):
    key = word.lower()
    if key not in cmu:
        return None
    parts = split_compound(key, cmu, zipf=zipf)
    if parts:
        return convert(cmu[parts[0]][0], parts[0]) + convert(cmu[parts[1]][0], parts[1])
    return convert(cmu[key][0], key)


def build_dict(out_path="laika_eng_dict.json"):
    """laika_eng_dict.json 을 다시 만든다. 방송 실행에는 필요 없고 사전을 고칠 때만 쓴다.

    필요한 패키지(만들 때만): pip install cmudict==1.1.3 wordfreq==3.1.1
    2026-10-08 확인: 2판 규칙으로 돌리면 기존 사전 117,250개와 한 글자도 다르지 않게 나온다.
    """
    import json
    import cmudict
    from wordfreq import zipf_frequency
    cmu = cmudict.dict()
    zipf = lambda w: zipf_frequency(w, "en")
    words = {k: word_to_hangul(k, cmu, zipf) for k in sorted(cmu)
             if len(k) >= 3 and re.fullmatch(r"[a-z]+", k)}
    data = {"_설명": [
        "영어 단어를 한글 발음으로 바꾸는 사전입니다. 자동으로 만들었습니다.",
        "출처: CMU 발음사전(cmudict 1.1.3) -> 발음기호를 한글로 옮김 (laika_eng_g2k.py), "
        "합성어 판단에 wordfreq 3.1.1",
        "만든 날: 2026-10-08  규칙 3판",
        "외래어 표기법 제3장 제1절(영어) 용례 98개 중 82개 일치 (83.7%). 2판은 66개 (67.3%)",
        "틀린 발음은 tts_dict.json 의 '영단어' 에 적으면 이 파일보다 먼저 쓰입니다.",
        "3글자 미만 단어는 넣지 않았습니다.",
    ], "단어": words}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    return len(words)


if __name__ == "__main__":
    print(build_dict(), "개를 만들었습니다.")
