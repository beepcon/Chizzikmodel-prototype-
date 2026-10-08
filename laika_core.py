# =============================================================================
# 라이카 코어 - 방송 파이프라인
#
# GUI 와 분리되어 있다. GUI 는 이 클래스를 만들고 콜백만 받는다.
# 콜백은 다른 스레드에서 호출되므로, GUI 쪽에서 큐에 넣어 안전하게 처리한다.
# =============================================================================

import os
import sys

# torch 와 ctranslate2 가 각자 OpenMP 런타임을 들고 와서 충돌한다.
# 어떤 라이브러리보다 먼저 설정돼야 하므로 최상단에 둔다.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from pathlib import Path


def find_cuda_dll_dirs():
    """설치된 nvidia 패키지에서 CUDA DLL 폴더를 찾는다.
    환경마다 site-packages 위치가 달라 경로를 고정하면 깨지므로 직접 탐색한다."""
    found, roots = [], []
    try:
        import site
        roots += [Path(p) for p in site.getsitepackages()]
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages")

    for root in roots:
        nvidia = root / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in ("cublas", "cudnn"):
            bin_dir = nvidia / sub / "bin"
            if bin_dir.is_dir() and str(bin_dir) not in found:
                found.append(str(bin_dir))
    return found


for _d in find_cuda_dll_dirs():
    try:
        os.add_dll_directory(_d)
    except Exception:
        pass
    # add_dll_directory 를 무시하는 로더가 있어서 PATH 에도 넣는다
    os.environ["PATH"] = _d + os.pathsep + os.environ["PATH"]

import asyncio
import base64
import collections
import io
import re
import subprocess
import threading
import time


# ============================ DEBUG SAVE 시작 ============================
# 마이크가 실제로 STT 에 넘긴 배열을 그대로 파일로 남긴다.
# 오인식의 원인이 (1) 들어간 소리 자체가 깨진 것인지
# (2) 소리는 멀쩡한데 Whisper 설정이 잘못 해석한 것인지 구분하기 위한 것이다.
#
# 끄려면 아래 DEBUG_SAVE 를 False 로 바꾼다.
# 완전히 제거하려면 "DEBUG SAVE 시작 ~ 끝" 으로 표시된 블록 두 곳만 지우면 된다.
#   1) 이 블록
#   2) _mic_thread 안의 블록
DEBUG_SAVE = True
DEBUG_DIR_NAME = "debug_audio"

# TTS 서버 대기시간. (연결, 응답) 초.
# 120초 하나였을 때, 서버가 멈춘 09-22 09:38 방송에서 2분을 말없이 기다렸다.
# 응답 15초는 가장 느렸던 첫 소리(mode 0 최대 5.639초)의 두 배 이상이다.
TTS_STREAM_TIMEOUT = (5, 15)
TTS_LOCK_WAIT = 20.0          # 앞 문장 합성이 끝나기를 기다리는 한도
# 기존(논스트리밍) 합성은 문장을 통째로 만들어 보내므로 더 오래 걸린다.
# 실측 합성 시간은 1.38~5.51초였다 (laika_english_tts_test.json, 14건).
TTS_PLAIN_TIMEOUT = (5, 60)
# 서버가 멈췄을 때 라이카가 스스로 껐다 켜는 횟수 한도(방송 한 번 기준).
# 무한히 재시작하면 모델 로딩(수십 초)이 반복되어 방송이 더 끊긴다.
TTS_RESTART_MAX = 2
# 되살아난 뒤 시청자에게 내보낼 한마디. config.json tts.apology 로 바꿀 수 있다.
TTS_APOLOGY = "죄송해요, 잠깐 목이 막혔어요."


def _tts_fail_reason(e):
    """실패 원인을 사람이 읽을 말로 바꾼다. 무엇을 해야 하는지까지 적는다."""
    import requests
    name = type(e).__name__
    if isinstance(e, requests.exceptions.ConnectionError):
        return ("서버에 연결되지 않습니다. TTS 서버가 꺼져 있습니다. "
                f"서버를 켜 주세요. ({name})")
    if isinstance(e, requests.exceptions.ReadTimeout):
        return (f"연결은 됐는데 {TTS_STREAM_TIMEOUT[1]}초 동안 소리가 오지 않았습니다. "
                f"서버가 멈췄습니다. TTS 서버를 껐다 켜 주세요. ({name})")
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return (f"{TTS_STREAM_TIMEOUT[0]}초 안에 연결되지 않았습니다. "
                f"TTS 서버를 껐다 켜 주세요. ({name})")
    if isinstance(e, requests.exceptions.HTTPError):
        code = getattr(getattr(e, "response", None), "status_code", "?")
        return f"서버가 오류로 답했습니다(HTTP {code}). 보낸 글이나 설정값을 봐야 합니다. ({name})"
    return f"{name}: {e}"


def _base_dir():
    """프로그램 폴더. exe 로 묶였을 때도 옆 파일을 찾을 수 있어야 한다."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _debug_dir():
    d = _base_dir() / DEBUG_DIR_NAME
    d.mkdir(exist_ok=True)
    return d


# ============================ STT 어휘 ============================
# Whisper 가 자주 틀리는 고유명사를 initial_prompt 로 넘겨 인식을 유도한다.
# 로그에서 확인된 오인식 예: 카구야 -> 카구라시, 라이카 -> 라이크하여
#
# 파일만 고치면 코드 수정 없이 어휘가 바뀐다.
# laika_debug_view.py 도 같은 파일을 읽으므로 검증과 실사용이 같은 조건이 된다.

VOCAB_NAME = "stt_vocab.txt"

VOCAB_TEMPLATE = """\
# Whisper 가 자주 틀리는 고유명사를 한 줄에 하나씩 적는다.
# 이 목록은 initial_prompt 로 들어가 인식 후보를 유도한다.
#
# '#' 로 시작하는 줄과 빈 줄은 무시된다.
# 이 파일만 고치면 코드 수정 없이 어휘가 바뀐다.
#
# 주의: initial_prompt 는 강제가 아니라 힌트다.
#       너무 많이 넣으면 오히려 엉뚱한 단어가 끼어든다.
#       30개 이내로 유지하고, 실제로 틀린 것만 넣는 편이 낫다.

라이카
카구야
"""


def load_stt_vocab(path=None):
    """어휘 목록을 읽는다. 파일이 없으면 초안을 만든다."""
    p = Path(path) if path else (_base_dir() / VOCAB_NAME)
    try:
        if not p.exists():
            p.write_text(VOCAB_TEMPLATE, encoding="utf-8")
        terms = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
        return terms
    except Exception:
        return []


def build_initial_prompt(terms, prefix="한국어 대화입니다."):
    """어휘 목록을 initial_prompt 문자열로 만든다.
    비어 있으면 None 을 돌려준다. (인자 자체를 넘기지 않기 위함)"""
    if not terms:
        return None
    return f"{prefix} {', '.join(terms)}."


def debug_save_audio(audio, rate, stt_cfg, text=None, error=None, log=None):
    """전사에 넘긴 배열을 wav 로, 측정값을 json 으로 저장한다.

    파일 쓰기가 마이크 읽기 루프를 막지 않도록 별도 스레드에서 실행한다.
    저장 실패는 방송에 영향을 주면 안 되므로 로그만 남기고 넘어간다."""
    if not DEBUG_SAVE:
        return

    def worker():
        try:
            import numpy as np
            import soundfile as sf

            stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
            d = _debug_dir()
            wav_path = d / f"{stamp}.wav"
            json_path = d / f"{stamp}.json"

            a = np.asarray(audio, dtype="float32").flatten()

            # 32bit float 로 저장해야 원본 배열이 손실 없이 되살아난다.
            # (PCM_16 으로 저장하면 재현 검증 때 값이 미세하게 달라진다)
            sf.write(str(wav_path), a, rate, subtype="FLOAT")

            peak = float(np.abs(a).max()) if a.size else 0.0
            rms = float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0
            meta = {
                "파일": wav_path.name,
                "저장시각": time.strftime("%Y-%m-%d %H:%M:%S"),
                "샘플수": int(a.size),
                "샘플레이트": int(rate),
                "길이초": round(a.size / rate, 3) if rate else 0.0,
                "peak": round(peak, 6),
                "rms": round(rms, 6),
                "평균절대값": round(float(np.abs(a).mean()), 6) if a.size else 0.0,
                "설정_threshold": stt_cfg.get("threshold"),
                "설정_silence_sec": stt_cfg.get("silence_sec"),
                "설정_min_speech_sec": stt_cfg.get("min_speech_sec"),
                "설정_model_size": stt_cfg.get("model_size"),
                "설정_device": stt_cfg.get("device"),
                "전사결과": text,
                "전사오류": error,
            }
            with open(json_path, "w", encoding="utf-8") as f:
                import json as _json
                _json.dump(meta, f, ensure_ascii=False, indent=2)

            if log:
                log("system", f"[디버그] 저장: {wav_path.name} "
                              f"({meta['길이초']}초, peak {peak:.4f})")
        except Exception as e:
            if log:
                log("error", f"[디버그] 저장 실패: {type(e).__name__}: {e}")

    threading.Thread(target=worker, daemon=True).start()
# ============================ DEBUG SAVE 끝 ==============================


class LaikaCore:
    """방송 파이프라인 전체를 담당한다.

    on_log(kind, text)   : 로그 한 줄. kind 는 system/host/chat/laika/error
    on_status(key, value): 상태 변화. key 는 tts/chzzk/mic/vision/busy
    """

    def __init__(self, cfg: dict, on_log=None, on_status=None):
        self.cfg = cfg
        # 화면 로그를 파일로도 남긴다.
        # 09-22 방송에서 스트리밍이 왜 절반이나 되돌아갔는지 확인하려 했을 때,
        # 화면 로그가 어디에도 안 남아 원인을 좁힐 수 없었다.
        self._log_file = None
        try:
            lf = (cfg.get("laika_log") or {})
            if lf.get("enabled", True):
                path = _base_dir() / (lf.get("file") or "laika.log")
                if path.exists() and path.stat().st_size > (
                        float(lf.get("max_mb", 20)) * 1024 * 1024):
                    path.replace(path.with_suffix(path.suffix + ".1"))
                self._log_file = open(path, "a", encoding="utf-8")
                self._log_file.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 시작 =====\n")
                self._log_file.flush()
        except Exception:
            self._log_file = None

        def _log_both(kind, text, _ui=on_log):
            if _ui is not None:
                try:
                    _ui(kind, text)
                except Exception:
                    pass
            if self._log_file is not None:
                try:
                    self._log_file.write(
                        f"{time.strftime('%H:%M:%S')}\t{kind}\t{text}\n")
                    self._log_file.flush()
                except Exception:
                    pass

        self._log = _log_both
        self._status = on_status or (lambda k, v: None)

        self.running = False
        self.loop = None
        self.thread = None
        self.server_proc = None

        self.chat_queue = collections.deque(maxlen=cfg["chat"]["queue_size"])
        self.host_queue = collections.deque()
        self.vision_queue = collections.deque(maxlen=1)

        # 히스토리 / 표정 / 측정.
        # 셋 다 실패해도 방송은 계속되어야 하므로 여기서는 만들기만 한다.
        from laika_history import History
        from laika_emotion import EmotionMap, ExpressionController
        from laika_metrics import Metrics
        from laika_tts_norm import TTSNormalizer
        from laika_filter import WordFilter

        self.history = History(cfg, log=self._log)
        # 언어 필터. 목록은 word_filter.txt / 예외는 word_allow.txt 에서 읽는다.
        # 기본값은 입력만 차단하고 출력은 기록만 한다(laika_filter.py 머리말 참고).
        self.word_filter = WordFilter(cfg, log=self._log)
        self.emotion_map = EmotionMap(cfg, log=self._log)
        self.expression = ExpressionController(self.emotion_map, cfg, log=self._log)
        self.metrics = Metrics(cfg, log=self._log)
        self.tts_norm = TTSNormalizer(log=self._log)

        # GPT-SoVITS 는 요청이 겹치면 멈춘다.
        # TTS.py 1065~1086행이 요청마다 공유 상태(t2s_model.model.infer_panel)를
        # 갈아 끼우고, api_v2.py 572행은 workers=1 이다.
        # (2026-09-22: 첫 문장 스트리밍과 둘째 문장 선합성을 동시에 보냈더니
        #  tts_server.log 에 두 요청이 뒤엉킨 채 끝나지 않았다)
        # 그래서 서버로 가는 합성 요청은 한 번에 하나만 보낸다.
        self._tts_lock = threading.Lock()
        self._tts_restarts = 0          # 이번 방송에서 껐다 켠 횟수
        self._tts_apology_due = False   # 되살아난 뒤 사과 한마디가 밀려 있는가

        self.speaking = threading.Event()   # 라이카가 말하는 중
        self.mic_on = threading.Event()     # 마이크 토글 (기본 꺼짐)
        self.vision_on = threading.Event()

        self.client = None
        self.stt_model = None
        self.cable_device = None
        self._stop_flag = threading.Event()

    # ---------------------------------------------------------------- 준비

    def preflight(self):
        """실행 전에 걸러야 나중에 엉뚱한 에러로 헤매지 않는다.
        문제 목록을 반환하며, 비어 있으면 실행 가능한 상태다."""
        problems = []
        c = self.cfg

        if not c.get("api_key", "").strip():
            problems.append("Anthropic API 키가 비어 있습니다.")

        sovits = Path(c.get("sovits_dir", ""))
        if not c.get("sovits_dir"):
            problems.append("GPT-SoVITS 폴더가 지정되지 않았습니다.")
        elif not sovits.exists():
            problems.append(f"GPT-SoVITS 폴더가 없습니다: {sovits}")
        elif not (sovits / "api_v2.py").exists():
            problems.append("선택한 폴더에 api_v2.py 가 없습니다.")
        elif not (sovits / "runtime" / "python.exe").exists():
            problems.append("선택한 폴더에 runtime\\python.exe 가 없습니다.")

        ref = c.get("ref_audio", "")
        if not ref:
            problems.append("참조 음성 파일이 지정되지 않았습니다.")
        elif not Path(ref).exists():
            problems.append(f"참조 음성 파일이 없습니다: {ref}")

        if not c.get("ref_text", "").strip():
            problems.append("참조 음성의 대사가 비어 있습니다.")

        for aux in c.get("aux_ref_audio", []):
            if aux and not Path(aux).exists():
                problems.append(f"보조 참조 음성이 없습니다: {aux}")

        return problems

    # ---------------------------------------------------------------- 서버

    def _server_alive(self) -> bool:
        import requests
        url = self.cfg["sovits_url"].rsplit("/", 1)[0] + "/docs"
        try:
            return requests.get(url, timeout=2).status_code < 500
        except Exception:
            return False

    def _tts_recover(self) -> bool:
        """멈춘 TTS 서버를 껐다 켠다.

        화면 로그에만 알린다. 이 시점에는 서버가 죽어 있어서 음성이 안 나간다.
        되살아나면 _tts_apology_due 를 세워 두고, 다음 발화 앞에 한마디 붙인다.
        한도는 TTS_RESTART_MAX. 방송 중 성공할 때마다 0 으로 돌아간다."""
        if self._tts_restarts >= TTS_RESTART_MAX:
            self._log("error",
                      f"[TTS] 이미 {TTS_RESTART_MAX}번 껐다 켰습니다. "
                      "직접 TTS 서버를 확인해 주세요.")
            return False
        self._tts_restarts += 1
        self._log("system",
                  f"[TTS] 서버가 멈춰서 껐다 켜는 중입니다 "
                  f"({self._tts_restarts}/{TTS_RESTART_MAX}). 잠시 소리가 끊깁니다.")
        self._status("tts", "껐다 켜는 중")
        try:
            self._kill_server()
        except Exception as e:
            self._log("error", f"[TTS] 서버 종료 실패: {type(e).__name__}: {e}")
        time.sleep(1.0)
        try:
            ok = self._start_server()
        except Exception as e:
            self._log("error", f"[TTS] 서버 시작 실패: {type(e).__name__}: {e}")
            ok = False
        if ok:
            self._log("system", "[TTS] 서버가 다시 켜졌습니다.")
            self._tts_apology_due = True
        else:
            self._log("error",
                      "[TTS] 서버를 되살리지 못했습니다. 직접 켜 주세요.")
        return ok

    def _start_server(self) -> bool:
        if self._server_alive():
            self._log("system", "이미 실행 중인 TTS 서버를 사용합니다.")
            self._status("tts", "연결됨")
            return True

        sovits = Path(self.cfg["sovits_dir"])
        self._log("system", "TTS 서버를 시작합니다...")
        self._status("tts", "시작 중")

        cmd = [
            str(sovits / "runtime" / "python.exe"), "api_v2.py",
            "-a", "127.0.0.1", "-p", "9880",
            "-c", r"GPT_SoVITS\configs\tts_infer.yaml",
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        # 서버 출력을 파일로 남긴다.
        # 예전에는 DEVNULL 로 버렸는데, api_v2.py 의 try/except 는
        # StreamingResponse 의 생성기 몸통에서 난 예외를 잡지 못한다.
        # 그 예외는 서버 콘솔로만 가므로, 버리면 원인을 영영 못 본다.
        # (2026-09-17 TTS 스트리밍 실패 9건의 원인을 이것 때문에 못 봤다)
        #
        # PIPE 를 쓰면 안 된다. 읽어주는 쪽이 없으면 버퍼가 차서 서버가 멈춘다.
        # 파일 핸들을 직접 넘기면 OS 가 쓰므로 그럴 일이 없다.
        #
        # t2s_model.py 533/701/889행의 tqdm(range(1500)) 이 파일로 가면
        # 진행바가 그대로 쌓인다. 그래서 크기 상한과 교체가 필요하다.
        log_cfg = dict(self.cfg.get("server_log") or {})
        self._server_log = None
        if log_cfg.get("enabled", True):
            try:
                path = _base_dir() / (log_cfg.get("file") or "tts_server.log")
                cap = float(log_cfg.get("max_mb", 50)) * 1024 * 1024
                if cap > 0 and path.exists() and path.stat().st_size >= cap:
                    old_path = path.with_suffix(path.suffix + ".1")
                    try:
                        if old_path.exists():
                            old_path.unlink()
                        path.rename(old_path)
                        self._log("system",
                                  f"서버 로그가 {cap/1024/1024:.0f}MB 를 넘어 "
                                  f"{old_path.name} 으로 옮기고 새로 씁니다.")
                    except Exception as e:
                        self._log("error", f"서버 로그 교체 실패: {e}")
                self._server_log = open(path, "a", encoding="utf-8",
                                        errors="replace", buffering=1)
                self._server_log.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 서버 시작 =====\n")
                self._log("system", f"서버 로그: {path.name}")
            except Exception as e:
                self._log("error", f"서버 로그 열기 실패, 출력을 버립니다: {e}")
                self._server_log = None

        # stdout 을 utf-8 로 강제한다.
        #
        # GPT-SoVITS 는 안내 문구를 i18n 으로 찍는데, tools/i18n/i18n.py 33행이
        # 번역을 못 찾으면 중국어 원문을 그대로 돌려준다.
        # ko_KR.json 에는 스트리밍 관련 6개 문구(TTS.py 1069/1072/1081/1085/
        # 1089/1093행)의 번역이 없어서 중국어가 그대로 print 된다.
        # 한국어 Windows 의 stdout 은 cp949 라 그 글자를 못 써서
        # UnicodeEncodeError 가 나고, 그것이 StreamingResponse 의 생성기 안에서
        # 터지면 응답이 그대로 끊긴다.
        # (2026-09-17 측정: 스트리밍 9건 전부 실패. 1081행 6건 + 1093행 3건)
        #
        # utf-8 로 두면 중국어도 써지므로 그 예외가 사라진다.
        # 서버 쪽 파일은 건드리지 않는다.
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"

        out = self._server_log if self._server_log else subprocess.DEVNULL
        try:
            self.server_proc = subprocess.Popen(
                cmd, cwd=str(sovits), creationflags=flags, env=env,
                stdout=out, stderr=subprocess.STDOUT,
            )
        except Exception as e:
            self._log("error", f"TTS 서버 실행 실패: {e}")
            self._status("tts", "실패")
            return False

        self._status("tts", "모델 로딩 중")
        start = time.time()
        while time.time() - start < 300:
            if self._stop_flag.is_set():
                return False
            if self._server_alive():
                self._log("system", f"TTS 서버 준비 완료 ({time.time() - start:.0f}초)")
                self._status("tts", "연결됨")
                return True
            time.sleep(2)

        self._log("error", "TTS 서버 시작 시간 초과")
        self._status("tts", "실패")
        return False

    # ---------------------------------------------------------------- 오디오

    def _resolve_output_device(self):
        """CABLE Input 을 이름으로 찾는다.
        장치 번호는 다른 프로그램 설치/제거로 바뀌므로 이름 탐색이 안전하다."""
        import sounddevice as sd

        idx = self.cfg["audio"].get("cable_device_index")
        if idx not in (None, "", -1):
            return int(idx)

        name = (self.cfg["audio"].get("cable_device_name") or "CABLE Input").lower()
        for i, dev in enumerate(sd.query_devices()):
            if name in dev["name"].lower() and dev["max_output_channels"] > 0:
                self._log("system", f"출력 장치: [{i}] {dev['name']}")
                return i

        self._log("error", f"'{name}' 장치를 찾지 못해 기본 스피커로 출력합니다.")
        return None

    # ---------------------------------------------------------------- 응답

    def _llm_reply(self, username, message, image_b64=None, timer=None):
        """응답을 만든다.

        반환: [{"emotion": str, "text": str}, ...]
        표정 기능이 꺼져 있거나 도구를 못 쓰면 감정 없이 문장만 담아 돌려준다.
        호출한 쪽은 항상 이 형식만 다루면 된다.

        기존 구조에서 바뀐 점
          - 문장과 감정을 tool use 로 함께 받는다
          - user 만 넣고 assistant 를 못 넣어 짝이 깨지는 일이 없다.
            History.add 는 쌍이 다 모였을 때만 호출한다.
          - 화면 턴은 채팅과 다른 통에 쌓여 서로를 밀어내지 않는다"""
        kind = "vision" if image_b64 else "chat"

        if image_b64:
            content = [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": f"[{username}] {message}"},
            ]
        else:
            content = f"[{username}] {message}"

        messages = self.history.messages() + [{"role": "user", "content": content}]

        kwargs = {
            "model": self.cfg.get("model", "claude-sonnet-4-6"),
            "max_tokens": self.cfg.get("max_tokens", 200),
            "system": self.cfg["persona"],
            "messages": messages,
        }

        # 표정이 켜져 있고 매핑이 채워져 있을 때만 도구를 붙인다.
        # 핫키를 아직 안 만들었어도 그냥 예전처럼 동작한다.
        use_tool = bool(self.emotion_map.cfg["enabled"]
                        and self.emotion_map.is_configured()
                        and self.emotion_map.emotions)
        if use_tool:
            from laika_emotion import build_tool
            kwargs["tools"] = [build_tool(self.emotion_map.emotions,
                                          self.emotion_map.default)]
            kwargs["tool_choice"] = {"type": "tool", "name": "speak"}

        t0 = time.time()
        resp = self.client.messages.create(**kwargs)
        if timer is not None:
            timer.add("llm", time.time() - t0)

        # 빈 응답의 원인을 나중에 판정할 수 있게 남긴다.
        # 검증(laika_empty_check.json, 45회): max_tokens=200 에서 15회 중 6회가
        # stop_reason="max_tokens", output_tokens=200, tool_use.input 이 빈 채로 왔다.
        # 400/800 에서는 0회. 출력은 126~250 토큰이었다.
        stop_reason = getattr(resp, "stop_reason", "") or ""
        _usage = getattr(resp, "usage", None)
        out_tokens = getattr(_usage, "output_tokens", None) if _usage else None

        sentences, fallback = [], ""
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and block.name == "speak":
                sentences = list(block.input.get("sentences", []))
            elif getattr(block, "type", "") == "text":
                fallback += block.text

        tool_used = bool(sentences)
        if not tool_used:
            # 도구를 안 쓴 경우. 검증에서는 10/10 사용했지만 보장은 없다.
            text = fallback.strip()
            sentences = [{"emotion": self.emotion_map.default, "text": s}
                         for s in self.split_sentences(text)] or \
                        ([{"emotion": self.emotion_map.default, "text": text}]
                         if text else [])
            if use_tool:
                self._log("system",
                          "[표정] 도구를 쓰지 않아 감정 없이 진행합니다. "
                          f"(stop_reason={stop_reason}, output_tokens={out_tokens})")

        # 말할 것이 하나도 없으면 조용히 넘어가지 않고 원인을 남긴다.
        # 예전에는 split_sentences 가 [""] 를 돌려줘 문장 1개처럼 보였고,
        # metrics.csv 의 오류 칸도 비어 있어 원인을 알 수 없었다.
        if not sentences:
            _msg = f"빈 응답: stop_reason={stop_reason}, output_tokens={out_tokens}"
            if stop_reason == "max_tokens":
                _msg += f" - max_tokens({kwargs['max_tokens']}) 상한에 잘렸습니다"
            self._log("error", _msg)
            if timer is not None:
                timer.empty_reason = _msg

        # 출력 필터. block_output 이 False 면 원문 그대로 돌려주고 기록만 남는다.
        # 여기서 한 번에 걸어야 로그·히스토리·TTS 가 같은 문장을 쓴다.
        try:
            for _s in sentences:
                _t = _s.get("text", "")
                if _t:
                    _s["text"], _ = self.word_filter.check_output(_t)
        except Exception as e:
            self._log("error", f"[필터] 출력 검사 실패, 원문 사용: {type(e).__name__}: {e}")

        # 매핑을 벗어난 감정 개수를 센다. 검증에서 21문장 중 3개가 벗어났다.
        off = sum(1 for s in sentences
                  if s.get("emotion") not in self.emotion_map.emotions)

        spoken = " ".join(s.get("text", "").strip() for s in sentences).strip()

        # 이미지를 히스토리에 남기면 매 요청마다 재전송되어 비용이 급증한다.
        # 다만 그냥 지우면 다음 턴에 "나는 화면을 못 본다"고 착각하므로,
        # 화면을 봤다는 사실은 텍스트로 남긴다.
        stored = (f"[{username}] (게임 화면을 직접 봤음) {message}"
                  if image_b64 else content)
        if spoken:
            self.history.add(stored, spoken, kind=kind)

        if timer is not None:
            timer.tool_used = tool_used
            timer.off_emotions = off

        return sentences

    # ---------------------------------------------------------------- 음성

    @staticmethod
    def split_sentences(text):
        """첫 문장만 먼저 합성해 재생하면 첫 소리까지의 대기가 크게 줄어든다."""
        parts = re.split(r"(?<=[.!?。！？])\s+", text)
        out = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if out and len(p) < 8:      # 너무 짧은 조각은 합친다 (TTS 품질 저하)
                out[-1] += " " + p
            else:
                out.append(p)
        # text 가 빈 문자열이면 out 도 비어 [""] 가 나온다.
        # 그러면 호출한 쪽이 "문장 1개" 로 착각해 빈 발화를 만든다.
        return out or ([text] if text.strip() else [])

    def _stt_kwargs(self):
        """transcribe 에 넘길 인자를 만든다.

        어휘는 stt_vocab.txt 에서 읽어 initial_prompt 로 넘긴다.
        나머지 디코딩 설정은 config 의 stt 항목에서 읽되,
        지정하지 않은 것은 faster-whisper 기본값을 그대로 쓴다."""
        kw = {}

        prompt = build_initial_prompt(load_stt_vocab())
        if prompt:
            kw["initial_prompt"] = prompt

        stt = self.cfg.get("stt", {})

        # None 을 "그 조건을 보지 마라" 로 받는 키.
        # 예전에는 값이 None 이면 전부 건너뛰어서, config 로 조건을 끌 방법이
        # 없었다. log_prob_threshold 를 끄려면 이 예외가 필요하다.
        #
        # 근거 (laika_nospeech_check.json, 2026-08-29 방송 162건):
        #   faster-whisper 는 no_speech_prob > no_speech_threshold 이고
        #   동시에 avg_logprob < log_prob_threshold 일 때만 세그먼트를 버린다.
        #   무음 6건은 no_speech_prob 0.6353~0.8110 으로 전부 높았는데,
        #   그중 4건은 avg_logprob 이 -0.837~-0.981 로 기본값 -1.0 보다 커서
        #   버려지지 않고 "한국어 대화입니다." 같은 문장이 되어 나왔다.
        #   log_prob_threshold 를 None 으로 두면 no_speech_prob 만으로 판정한다.
        #   정상 156건은 no_speech_prob 이 최대 0.5605 라 한 건도 안 걸린다.
        #
        # 주의: log_prob_threshold 는 온도 재시도(temperature fallback) 판단에도
        #       쓰인다. None 이면 logprob 이 낮다는 이유로는 재시도하지 않는다.
        NULLABLE = ("log_prob_threshold", "no_speech_threshold",
                    "compression_ratio_threshold")

        for key in ("beam_size", "vad_filter", "condition_on_previous_text",
                    "no_speech_threshold", "log_prob_threshold",
                    "temperature", "compression_ratio_threshold"):
            if key not in stt:
                continue
            if stt[key] is None and key not in NULLABLE:
                continue
            kw[key] = stt[key]
        return kw

    def _tts(self, text):
        import requests
        import soundfile as sf

        # GPT-SoVITS 는 넘긴 글자를 그대로 읽는다.
        # "LP 8000" 을 그대로 넘기면 자릿수를 하나씩 읽어버리므로,
        # 사람이 읽는 방식으로 바꿔서 넘긴다.
        # 정규화가 실패해도 합성은 되어야 하므로 예외를 흡수한다.
        try:
            spoken = self.tts_norm.normalize(text)
        except Exception as e:
            self._log("error", f"[발음] 정규화 실패, 원문 사용: {type(e).__name__}: {e}")
            spoken = text

        # cfg["tts"] 에 streaming_mode 가 들어 있으면 뒤에 펼쳐지며
        # 위의 False 를 덮어써 버린다. 이 함수는 논스트리밍 전용이므로 빼고 펼친다.
        extra = {k: v for k, v in (self.cfg.get("tts") or {}).items()
                 if k != "streaming_mode"}
        payload = {
            "text": spoken,
            "text_lang": "ko",
            "ref_audio_path": self.cfg["ref_audio"],
            "prompt_text": self.cfg["ref_text"],
            "prompt_lang": "ko",
            "aux_ref_audio_paths": [a for a in self.cfg.get("aux_ref_audio", []) if a],
            **extra,
            "streaming_mode": False,
        }
        # 잠금을 기다리는 데도 한도를 둔다.
        # with self._tts_lock 는 한도가 없어서, 앞 요청이 영영 안 끝나면
        # 이 함수가 영원히 멈춘다. 그러면 워커 전체가 선다.
        if not self._tts_lock.acquire(timeout=TTS_LOCK_WAIT):
            raise TimeoutError(
                f"앞 문장 합성이 {TTS_LOCK_WAIT:.0f}초 안에 끝나지 않았습니다")
        try:
            r = requests.post(self.cfg["sovits_url"], json=payload,
                              timeout=TTS_PLAIN_TIMEOUT)
            r.raise_for_status()
            content = r.content
        finally:
            self._tts_lock.release()
        return sf.read(io.BytesIO(content))

    @staticmethod
    def _wav_sr(header: bytes):
        """스트리밍 wav 의 앞부분에서 샘플레이트를 읽는다.

        raw 로 받으면 샘플레이트를 알 길이가 없다. wav 로 받으면
        api_v2.py 425~427행이 첫 오디오가 준비된 뒤 44바이트 헤더를 먼저 내보내므로
        거기서 정확히 읽을 수 있다. 추측하지 않는다."""
        i = header.find(b"fmt ")
        if i < 0 or len(header) < i + 16:
            return None
        return int.from_bytes(header[i + 12:i + 16], "little")

    def _tts_play_stream(self, text, emotion=None):
        """조각을 받아가며 곧바로 재생한다.

        emotion 을 주면 첫 조각을 내보내기 직전에 표정을 바꾼다.
        스트리밍은 호출부터 첫 소리까지 1초 남짓 걸리므로,
        호출 전에 바꾸면 표정이 소리보다 그만큼 먼저 움직인다.

        반환: (첫 소리까지 걸린 초, 표정에 걸린 초) / None(실패)
              None 이면 호출한 쪽이 기존 논스트리밍 경로로 되돌아간다.

        근거 (laika_tts_mode_sample.json, 2026-09-18, 문장 4개):
          첫 소리까지 mode 0 평균 4.030초 -> mode 1 평균 1.495초.
          긴 문장에서 차이가 가장 컸다 (5.639초 -> 0.872초).

        주의: 이 함수는 PYTHONIOENCODING=utf-8 로 띄운 서버에서만 동작한다.
              그렇지 않으면 TTS.py 1081/1093행의 중국어 print 가
              cp949 로 못 써져 스트리밍 응답이 그대로 끊긴다.
              _start_server 가 그 환경을 붙여준다."""
        import requests
        import sounddevice as sd

        mode = int((self.cfg.get("tts") or {}).get("streaming_mode", 0) or 0)
        if mode <= 0:
            return None

        try:
            spoken = self.tts_norm.normalize(text)
        except Exception as e:
            self._log("error", f"[발음] 정규화 실패, 원문 사용: {type(e).__name__}: {e}")
            spoken = text

        extra = {k: v for k, v in (self.cfg.get("tts") or {}).items()
                 if k != "streaming_mode"}
        payload = {
            "text": spoken,
            "text_lang": "ko",
            "ref_audio_path": self.cfg["ref_audio"],
            "prompt_text": self.cfg["ref_text"],
            "prompt_lang": "ko",
            "aux_ref_audio_paths": [a for a in self.cfg.get("aux_ref_audio", []) if a],
            **extra,
            "streaming_mode": mode,
            "media_type": "wav",
        }

        t0 = time.time()
        out = mon = None
        first_at = None
        expr_took = 0.0
        self.speaking.set()
        self._status("busy", True)
        locked = self._tts_lock.acquire(timeout=TTS_LOCK_WAIT)
        if not locked:
            self.speaking.clear()
            self._status("busy", False)
            self._log("error",
                      f"[TTS] 앞선 문장 합성이 {TTS_LOCK_WAIT:.0f}초 안에 안 끝났습니다. "
                      "서버가 멈춘 것일 수 있습니다. "
                      "말이 계속 끊기면 TTS 서버를 껐다 켜 주세요.")
            self._status("tts", "응답 없음")
            return None
        try:
            # (연결 5초, 응답 15초). 전에는 120초 하나였고,
            # 2026-09-22 09:38 방송에서 서버가 멈춘 채 2분을 기다렸다.
            r = requests.post(self.cfg["sovits_url"], json=payload,
                              stream=True, timeout=TTS_STREAM_TIMEOUT)
            r.raise_for_status()

            head, sr, rem = b"", None, b""
            for chunk in r.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                if sr is None:
                    # 헤더가 다 찰 때까지 모은다
                    head += chunk
                    sr = self._wav_sr(head)
                    if sr is None:
                        continue
                    chunk = head[44:]          # 헤더 뒤부터가 소리다
                    out = sd.RawOutputStream(samplerate=sr, channels=1,
                                             dtype="int16",
                                             device=self.cable_device)
                    out.start()
                    if self.cfg["audio"].get("enable_monitor", True):
                        try:
                            mon = sd.RawOutputStream(samplerate=sr, channels=1,
                                                     dtype="int16", device=None)
                            mon.start()
                        except Exception:
                            mon = None         # 모니터 실패는 방송에 지장 없음
                if not chunk:
                    continue
                # int16 은 2바이트다. 홀수로 끊긴 1바이트는 다음 조각에 붙인다.
                buf = rem + chunk
                if len(buf) % 2:
                    buf, rem = buf[:-1], buf[-1:]
                else:
                    rem = b""
                if not buf:
                    continue
                if first_at is None:
                    first_at = time.time() - t0
                    # 소리가 나가는 바로 그 순간에 표정을 바꾼다
                    if emotion is not None and self.expression.connected:
                        try:
                            _applied, expr_took = self.expression.set(emotion)
                        except Exception as e:
                            self._log("error",
                                      f"[표정] 전환 실패({emotion}): "
                                      f"{type(e).__name__}: {e}")
                out.write(buf)
                if mon is not None:
                    try:
                        mon.write(buf)
                    except Exception:
                        mon = None
            r.close()

            if first_at is None:
                self._log("error",
                          "[TTS] 서버가 소리를 보내지 않았습니다. "
                          "서버가 멈춘 것으로 보입니다. TTS 서버를 껐다 켜 주세요.")
                self._status("tts", "응답 없음")
                return None
            self._status("tts", "연결됨")
            self._tts_restarts = 0      # 정상으로 돌아왔으니 횟수를 되돌린다
            return first_at, expr_took
        except Exception as e:
            self._log("error", "[TTS] 스트리밍 실패, 기존 방식으로 넘어갑니다. "
                               + _tts_fail_reason(e))
            self._status("tts", "응답 없음")
            return None
        finally:
            if locked:
                self._tts_lock.release()
            for st in (out, mon):
                if st is not None:
                    try:
                        st.stop()
                        st.close()
                    except Exception:
                        pass
            time.sleep(0.3)       # 잔향이 마이크에 잡히지 않도록
            self.speaking.clear()
            self._status("busy", False)

    def _play(self, data, sr):
        import sounddevice as sd

        self.speaking.set()
        self._status("busy", True)
        try:
            if self.cfg["audio"].get("enable_monitor", True):
                try:
                    threading.Thread(
                        target=lambda: sd.play(data, sr, device=None), daemon=True
                    ).start()
                except Exception:
                    pass          # 모니터링 실패는 방송에 지장 없음
            sd.play(data, sr, device=self.cable_device)
            sd.wait()
        finally:
            time.sleep(0.3)       # 잔향이 마이크에 잡히지 않도록
            self.speaking.clear()
            self._status("busy", False)

    async def _speak(self, sentences, loop, timer=None):
        """한 문장을 재생하는 동안 다음 문장을 미리 합성해 대기 시간을 숨긴다.

        sentences 는 [{"emotion": str, "text": str}, ...] 이다.
        표정은 재생 직전에 바꾼다. 합성이 아니라 재생에 맞춰야
        소리와 표정이 같이 나온다."""
        if not sentences:
            return

        texts = [s.get("text", "").strip() for s in sentences]
        texts = [t for t in texts if t]
        if not texts:
            return
        emos = [s.get("emotion") for s in sentences if s.get("text", "").strip()]

        # 서버를 껐다 켠 직후라면 사과 한마디를 맨 앞에 붙인다.
        # 멈춰 있는 동안에는 소리를 낼 수 없으므로 복구 뒤에 나간다.
        if self._tts_apology_due:
            self._tts_apology_due = False
            line = ((self.cfg.get("tts") or {}).get("apology")
                    or TTS_APOLOGY).strip()
            if line:
                texts.insert(0, line)
                emos.insert(0, (self.cfg.get("tts") or {}).get("apology_emotion")
                            or "슬픔")

        t0 = time.time()
        use_stream = int((self.cfg.get("tts") or {}).get("streaming_mode", 0) or 0) > 0

        async def _face(i):
            """재생 직전에 표정을 바꾼다. 실패해도 발화는 그대로 진행된다."""
            if self.expression.connected and i < len(emos):
                applied, took = await loop.run_in_executor(
                    None, self.expression.set, emos[i])
                if timer is not None:
                    timer.add("expr", took)

        if use_stream:
            # 문장마다 순서대로 스트리밍한다. 미리 합성은 하지 않는다.
            # 서버는 요청이 겹치면 멈추므로(2026-09-22 확인) 겹치게 보내지 않는다.
            # 문장 사이 간격은 다음 문장의 첫 조각까지(실측 평균 1.5초 남짓)다.
            for i, text in enumerate(texts):
                emo = emos[i] if i < len(emos) else None
                if timer is not None:
                    timer.stream_try = getattr(timer, "stream_try", 0) + 1
                res = await loop.run_in_executor(
                    None, self._tts_play_stream, text, emo)
                if res is not None:
                    if timer is not None:
                        timer.stream_ok = getattr(timer, "stream_ok", 0) + 1
                    el, expr_took = res
                    if timer is not None:
                        if i == 0:
                            timer.add("first_tts", el)
                        timer.add("expr", expr_took)
                    continue
                # 이 문장만 기존 방식으로 되돌아간다
                try:
                    data, sr = await loop.run_in_executor(None, self._tts, text)
                except Exception as e:
                    self._log("error", "[TTS] 기존 방식도 실패했습니다. "
                                       + _tts_fail_reason(e))
                    self._status("tts", "응답 없음")
                    # 서버를 껐다 켜고 이 문장부터 다시 해 본다
                    if not await loop.run_in_executor(None, self._tts_recover):
                        break
                    try:
                        data, sr = await loop.run_in_executor(
                            None, self._tts, text)
                    except Exception as e2:
                        self._log("error", "[TTS] 되살린 뒤에도 실패했습니다. "
                                           + _tts_fail_reason(e2))
                        break
                if timer is not None and i == 0:
                    timer.add("first_tts", time.time() - t0)
                await _face(i)
                await loop.run_in_executor(None, self._play, data, sr)
        else:
            nxt = loop.run_in_executor(None, self._tts, texts[0])
            for i in range(len(texts)):
                try:
                    data, sr = await nxt
                except Exception as e:
                    self._log("error", "[TTS] 합성에 실패했습니다. "
                                       + _tts_fail_reason(e))
                    self._status("tts", "응답 없음")
                    if not await loop.run_in_executor(None, self._tts_recover):
                        break
                    try:
                        data, sr = await loop.run_in_executor(
                            None, self._tts, texts[i])
                    except Exception as e2:
                        self._log("error", "[TTS] 되살린 뒤에도 실패했습니다. "
                                           + _tts_fail_reason(e2))
                        break
                if timer is not None and i == 0:
                    timer.add("first_tts", time.time() - t0)
                if i + 1 < len(texts):
                    nxt = loop.run_in_executor(None, self._tts, texts[i + 1])
                await _face(i)
                await loop.run_in_executor(None, self._play, data, sr)

        # 말이 끝났으니 잠시 뒤 맨 얼굴로 돌아간다.
        # 여기서 기다리지 않는다. 다음 항목 처리가 밀리면 안 된다.
        if self.expression.connected:
            self.expression.schedule_reset()

        if timer is not None:
            timer.add("tts_total", time.time() - t0)

    # ---------------------------------------------------------------- 워커

    def _next_item(self):
        """우선순위: 호스트 > 시청자 채팅 > 게임 화면"""
        if self.host_queue:
            return self.host_queue.popleft() + (None,)

        if self.chat_queue:
            batch = list(self.chat_queue)[-self.cfg["chat"]["batch"]:]
            self.chat_queue.clear()
            if len(batch) == 1:
                u, m = batch[0]
                return u, m, None
            return "여러명", " / ".join(f"[{u}] {m}" for u, m in batch), None

        if self.vision_queue:
            return self.vision_queue.popleft()

        return None

    async def _worker(self):
        loop = asyncio.get_event_loop()

        while self.running:
            item = self._next_item()
            if item is None:
                await asyncio.sleep(0.2)
                continue

            username, message, image_b64 = item

            from laika_metrics import Timer
            timer = Timer()
            timer.tool_used = False
            timer.off_emotions = 0
            timer.empty_reason = ""
            err = ""
            sentences = []

            try:
                sentences = await loop.run_in_executor(
                    None, self._llm_reply, username, message, image_b64, timer)

                spoken = " ".join(s.get("text", "").strip() for s in sentences).strip()
                if spoken:
                    tags = "".join(f"[{s.get('emotion')}]" for s in sentences)
                    self._log("laika", f"{spoken}   {tags}" if timer.tool_used
                              else spoken)

                await self._speak(sentences, loop, timer)
            except Exception as e:
                # 한 번의 실패로 방송이 멈추지 않도록 모든 예외를 흡수한다
                err = f"{type(e).__name__}: {e}"
                self._log("error", err)
                # 말하다 끊겼어도 표정이 굳은 채 남으면 안 된다
                try:
                    if self.expression.connected:
                        self.expression.schedule_reset()
                except Exception:
                    pass

            # 예외는 없었지만 아무 말도 못 한 경우도 오류 칸에 남긴다
            if not err:
                err = getattr(timer, "empty_reason", "") or ""

            # 어디가 느린지 나중에 볼 수 있도록 구간별로 남긴다
            first = ""
            if timer.get("llm") != "" and timer.get("first_tts") != "":
                first = round(timer.sections.get("llm", 0)
                              + timer.sections.get("first_tts", 0), 3)
            self.metrics.write({
                "시각": time.strftime("%Y-%m-%d %H:%M:%S"),
                "종류": "vision" if image_b64 else "chat",
                "입력길이": len(message or ""),
                "문장수": len(sentences),
                "llm초": timer.get("llm"),
                "tool사용": int(bool(timer.tool_used)),
                "감정이탈": timer.off_emotions,
                "첫문장tts초": timer.get("first_tts"),
                "첫소리까지초": first,
                "tts합계초": timer.get("tts_total"),
                "표정합계초": timer.get("expr"),
                "전체초": timer.total(),
                "스트리밍": (f"{getattr(timer, 'stream_ok', 0)}/"
                         f"{getattr(timer, 'stream_try', 0)}"
                         if getattr(timer, "stream_try", 0) else ""),
                "오류": err,
            })

    # ---------------------------------------------------------------- 치지직

    async def _chzzk(self):
        """이 코루틴은 절대 반환하지 않는다.
        반환되면 gather 가 워커까지 정리해 방송 전체가 멈추기 때문이다."""
        channel = self.cfg.get("chzzk_channel_id", "").strip()
        if not channel:
            self._status("chzzk", "미설정")
            while self.running:
                await asyncio.sleep(1)
            return

        from chzzkpy.unofficial.chat import ChatClient

        while self.running:
            try:
                self._status("chzzk", "연결 중")
                client = ChatClient(channel)

                @client.event
                async def on_chat(message):
                    nick = message.profile.nickname if message.profile else "익명"
                    # 걸린 채팅은 라이카에게 넘기지 않는다. 로그에는 남긴다.
                    ok, _hits = self.word_filter.check_input(nick, message.content)
                    self._log("chat", f"{nick}: {message.content}"
                              + ("" if ok else "   [필터됨]"))
                    if ok:
                        self.chat_queue.append((nick, message.content))

                self._status("chzzk", "연결됨")
                await client.start()
                self._status("chzzk", "끊김")
            except Exception as e:
                self._status("chzzk", "오류")
                self._log("error", f"치지직: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

    # ---------------------------------------------------------------- 마이크

    def _mic_thread(self):
        import numpy as np
        import sounddevice as sd

        stt = self.cfg["stt"]
        rate, blocksize = 16000, 1600
        buf, silence = [], 0.0

        try:
            stream = sd.InputStream(samplerate=rate, channels=1,
                                    blocksize=blocksize, dtype="float32")
        except Exception as e:
            self._log("error", f"마이크를 열 수 없습니다: {e}")
            return

        with stream:            # with 가 start/stop 을 담당한다
            while self.running:
                try:
                    block, _ = stream.read(blocksize)
                except Exception:
                    continue

                # 꺼져 있거나 라이카가 말하는 중이면 버린다
                # (스피커 소리를 되받아 자기 말에 대답하는 것을 막는다)
                if not self.mic_on.is_set() or self.speaking.is_set():
                    buf, silence = [], 0.0
                    continue

                if float(np.abs(block).mean()) > stt["threshold"]:
                    buf.append(block.copy())
                    silence = 0.0
                elif buf:
                    buf.append(block.copy())
                    silence += 0.1
                    if silence >= stt["silence_sec"]:
                        audio = np.concatenate(buf).flatten()
                        buf, silence = [], 0.0
                        if len(audio) / rate < stt["min_speech_sec"]:
                            # ==================== DEBUG SAVE 시작 ====================
                            # 짧아서 버려지는 발화도 남긴다.
                            # 말이 잘려 들어오는 것이 원인이라면 여기에 증거가 쌓인다.
                            debug_save_audio(audio, rate, stt,
                                             text=None,
                                             error="min_speech_sec 미만으로 버려짐",
                                             log=self._log)
                            # ==================== DEBUG SAVE 끝 ======================
                            continue
                        # 순간 잡음 한 번이 [0.1초 소리 + silence_sec 무음] 짜리
                        # 녹음을 만들고, 그것이 그대로 Whisper 로 넘어가
                        # initial_prompt 를 되받은 "한국어 대화입니다." 같은
                        # 문장이 되어 나왔다.
                        #
                        # 근거 (2026-08-29 방송 162건):
                        #   무음 6건  rms 0.0051 ~ 0.0169
                        #   정상 156건 rms 0.0213 ~ 0.1380
                        #   0.010 이면 무음 4건이 여기서 걸리고 정상은 0건이 걸린다.
                        #   남은 2건(0.0153 / 0.0169)은 no_speech_threshold 가 잡는다.
                        #
                        # 여기서 걸러내면 그만큼 Whisper 를 안 부른다.
                        # (2026-08-28 에 transcribe 가 MemoryError: bad allocation
                        #  으로 실패한 적이 있어 호출을 줄이는 편이 낫다)
                        rms = float(np.sqrt(np.mean(audio.astype("float64") ** 2)))
                        min_rms = stt.get("min_rms", 0.010)
                        if min_rms and rms < float(min_rms):
                            # ==================== DEBUG SAVE 시작 ====================
                            debug_save_audio(
                                audio, rate, stt, text=None,
                                error=f"min_rms 미만으로 버려짐 (rms {rms:.4f} < {min_rms})",
                                log=self._log)
                            # ==================== DEBUG SAVE 끝 ======================
                            continue

                        try:
                            segs, _ = self.stt_model.transcribe(
                                audio, language="ko",
                                **self._stt_kwargs())
                            text = " ".join(s.text.strip() for s in segs).strip()
                            # ==================== DEBUG SAVE 시작 ====================
                            debug_save_audio(audio, rate, stt,
                                             text=text, error=None, log=self._log)
                            # ==================== DEBUG SAVE 끝 ======================
                            if text:
                                ok, _hits = self.word_filter.check_input("호스트", text)
                                self._log("host", text
                                          + ("" if ok else "   [필터됨]"))
                                if ok:
                                    self.host_queue.append(("호스트", text))
                        except Exception as e:
                            # 전사 실패로 스레드가 죽으면 이후 음성 입력이 영영 안 된다
                            self._log("error", f"STT: {type(e).__name__}: {e}")
                            # ==================== DEBUG SAVE 시작 ====================
                            debug_save_audio(audio, rate, stt,
                                             text=None,
                                             error=f"{type(e).__name__}: {e}",
                                             log=self._log)
                            # ==================== DEBUG SAVE 끝 ======================

    # ---------------------------------------------------------------- 화면

    def _capture(self):
        """설정된 대상(모니터 / 창 / 영역)을 캡처해 base64 JPEG 로 반환한다."""
        import mss
        import laika_capture
        from PIL import Image

        v = self.cfg["vision"]
        with mss.mss() as sct:
            area, desc = laika_capture.resolve_area(v, sct)
            if area is None:
                raise RuntimeError(desc)
            shot = sct.grab(area)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

        if img.width > v["max_width"]:
            h = int(img.height * v["max_width"] / img.width)
            img = img.resize((v["max_width"], h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=v["jpeg_quality"])
        return base64.b64encode(buf.getvalue()).decode()

    async def _vision(self):
        loop = asyncio.get_event_loop()
        while self.running:
            await asyncio.sleep(self.cfg["vision"]["interval_sec"])
            if not self.vision_on.is_set() or self.speaking.is_set():
                continue
            if self.host_queue or self.chat_queue:
                continue           # 사람 입력이 밀려 있으면 건너뛴다
            try:
                img = await loop.run_in_executor(None, self._capture)
                self.vision_queue.append(
                    ("게임화면", "지금 이 장면 보고 한마디 해줘", img))
            except Exception as e:
                self._log("error", f"화면 캡처: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 수명주기

    async def _main(self):
        await asyncio.gather(self._worker(), self._chzzk(), self._vision())

    def _run(self):
        try:
            import anthropic
            from faster_whisper import WhisperModel

            self.client = anthropic.Anthropic(api_key=self.cfg["api_key"])

            if not self._start_server():
                self.running = False
                self._status("running", False)
                return

            stt = self.cfg["stt"]
            self._log("system", f"음성 인식 모델 로딩 중 ({stt['model_size']})...")
            try:
                self.stt_model = WhisperModel(
                    stt["model_size"], device=stt["device"],
                    compute_type="float16" if stt["device"] == "cuda" else "int8")
            except Exception as e:
                self._log("error", f"{stt['device']} 사용 불가, CPU 로 전환합니다. ({e})")
                self.stt_model = WhisperModel(
                    stt["model_size"], device="cpu", compute_type="int8")

            self.cable_device = self._resolve_output_device()

            # 지난 방송의 대화를 이어받는다.
            # 예전에는 시작할 때마다 기억이 통째로 사라졌다.
            self.history.resume()
            s = self.history.stats()
            self._log("system",
                      f"[히스토리] 채팅 {s['채팅_보관']}턴 / 화면 {s['화면_보관']}턴 "
                      f"(전송 {s['전송_메시지수']}개 메시지)")

            # 표정. 연결 실패해도 방송은 그대로 진행된다.
            self.expression.start()
            # 화면 오른쪽 상태 칸에 연동 여부를 띄운다.
            self._status("expression",
                         "연결됨" if self.expression.connected else "안 됨")

            threading.Thread(target=self._mic_thread, daemon=True).start()

            self._log("system", "방송을 시작했습니다.")
            self._status("running", True)

            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._main())
            except RuntimeError:
                # stop() 이 call_soon_threadsafe(self.loop.stop) 으로 루프를 멈추면
                # run_until_complete 가
                # "Event loop stopped before Future completed" RuntimeError 를 낸다.
                # 종료 요청이 있었으면 정상 종료다. 시작 실패가 아니다.
                if not self._stop_flag.is_set():
                    raise

        except Exception as e:
            self._log("error", f"시작 실패: {type(e).__name__}: {e}")
        finally:
            self.running = False
            self._status("running", False)

    def _register_hotkey(self):
        """게임 화면에 있을 때도 마이크를 켜고 끌 수 있어야 하므로 전역 단축키를 쓴다.
        관리자 권한으로 실행된 게임 위에서는 동작하지 않을 수 있다."""
        key = self.cfg["stt"].get("toggle_key", "").strip()
        if not key:
            return
        try:
            import keyboard
            keyboard.add_hotkey(key, self.toggle_mic)
            self._log("system", f"{key.upper()} 키로 마이크를 켜고 끌 수 있습니다.")
        except Exception as e:
            self._log("system", f"단축키 등록 실패({e}). 창의 마이크 버튼을 사용하세요.")

    def start(self):
        if self.running:
            return
        self.running = True
        self._stop_flag.clear()
        self._register_hotkey()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _kill_server(self):
        """자기가 띄운 서버만 종료한다.

        api_v2.py 는 uvicorn 서버라 terminate() 를 무시하고 버티는 경우가 있고,
        자식 프로세스를 띄우면 부모만 죽고 자식이 남는다.
        그래서 안 죽으면 taskkill 로 프로세스 트리째 정리한다.
        (재사용한 서버는 server_proc 이 None 이므로 건드리지 않는다)"""
        proc = self.server_proc
        self.server_proc = None
        if not proc or proc.poll() is not None:
            return

        self._log("system", "TTS 서버를 종료합니다.")
        try:
            if getattr(self, "_server_log", None):
                self._server_log.write(
                    f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} 서버 종료 =====\n")
                self._server_log.close()
                self._server_log = None
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass

        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                proc.wait(timeout=5)
                return
            except Exception as e:
                self._log("error", f"서버 강제 종료 실패: {e}")

        try:
            proc.kill()
        except Exception:
            pass

    def stop(self):
        self.running = False
        self._stop_flag.set()
        self.mic_on.clear()

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        # 표정을 켜둔 채로 끝나면 다음 실행 때 토글이 어긋난다
        try:
            self.expression.stop()
        except Exception as e:
            self._log("error", f"[표정] 정리 실패: {type(e).__name__}: {e}")

        # 마지막 대화까지 남긴다
        try:
            self.history.save()
        except Exception as e:
            self._log("error", f"[히스토리] 저장 실패: {type(e).__name__}: {e}")

        self._kill_server()

        self._status("tts", "중지됨")
        self._status("chzzk", "중지됨")
        self._status("expression", "중지됨")
        self._status("running", False)
        self._log("system", "방송을 종료했습니다.")

        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    # ---------------------------------------------------------------- 입력

    def send_host(self, text):
        ok, _ = self.word_filter.check_input("호스트", text)
        self._log("host", text + ("" if ok else "   [필터됨]"))
        if ok:
            self.host_queue.append(("호스트", text))

    def send_chat(self, nick, text):
        ok, _ = self.word_filter.check_input(nick, text)
        self._log("chat", f"{nick}: {text}" + ("" if ok else "   [필터됨]"))
        if ok:
            self.chat_queue.append((nick, text))

    def toggle_mic(self):
        if self.mic_on.is_set():
            self.mic_on.clear()
        else:
            self.mic_on.set()
        on = self.mic_on.is_set()
        self._status("mic", on)
        return on

    def toggle_vision(self):
        if self.vision_on.is_set():
            self.vision_on.clear()
        else:
            self.vision_on.set()
        on = self.vision_on.is_set()
        self._status("vision", on)
        return on
