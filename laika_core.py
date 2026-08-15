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


class LaikaCore:
    """방송 파이프라인 전체를 담당한다.

    on_log(kind, text)   : 로그 한 줄. kind 는 system/host/chat/laika/error
    on_status(key, value): 상태 변화. key 는 tts/chzzk/mic/vision/busy
    """

    def __init__(self, cfg: dict, on_log=None, on_status=None):
        self.cfg = cfg
        self._log = on_log or (lambda k, t: None)
        self._status = on_status or (lambda k, v: None)

        self.running = False
        self.loop = None
        self.thread = None
        self.server_proc = None

        self.chat_queue = collections.deque(maxlen=cfg["chat"]["queue_size"])
        self.host_queue = collections.deque()
        self.vision_queue = collections.deque(maxlen=1)
        self.history = []

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
        try:
            self.server_proc = subprocess.Popen(
                cmd, cwd=str(sovits), creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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

    def _llm_reply(self, username, message, image_b64=None):
        if image_b64:
            content = [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": f"[{username}] {message}"},
            ]
        else:
            content = f"[{username}] {message}"

        self.history.append({"role": "user", "content": content})

        resp = self.client.messages.create(
            model=self.cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=self.cfg.get("max_tokens", 200),
            system=self.cfg["persona"],
            messages=self.history[-20:],       # 최근 20턴만 유지 (비용/속도)
        )
        text = resp.content[0].text.strip()
        self.history.append({"role": "assistant", "content": text})

        # 이미지를 히스토리에 남기면 매 요청마다 재전송되어 비용이 급증한다
        if image_b64:
            self.history[-2] = {"role": "user", "content": f"[{username}] {message}"}
        return text

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
        return out or [text]

    def _tts(self, text):
        import requests
        import soundfile as sf

        payload = {
            "text": text,
            "text_lang": "ko",
            "ref_audio_path": self.cfg["ref_audio"],
            "prompt_text": self.cfg["ref_text"],
            "prompt_lang": "ko",
            "aux_ref_audio_paths": [a for a in self.cfg.get("aux_ref_audio", []) if a],
            "streaming_mode": False,
            **self.cfg["tts"],
        }
        r = requests.post(self.cfg["sovits_url"], json=payload, timeout=120)
        r.raise_for_status()
        return sf.read(io.BytesIO(r.content))

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

    async def _speak(self, text, loop):
        """한 문장을 재생하는 동안 다음 문장을 미리 합성해 대기 시간을 숨긴다."""
        sentences = self.split_sentences(text)
        nxt = loop.run_in_executor(None, self._tts, sentences[0])

        for i in range(len(sentences)):
            data, sr = await nxt
            if i + 1 < len(sentences):
                nxt = loop.run_in_executor(None, self._tts, sentences[i + 1])
            await loop.run_in_executor(None, self._play, data, sr)

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
            try:
                reply = await loop.run_in_executor(
                    None, self._llm_reply, username, message, image_b64)
                self._log("laika", reply)
                await self._speak(reply, loop)
            except Exception as e:
                # 한 번의 실패로 방송이 멈추지 않도록 모든 예외를 흡수한다
                self._log("error", f"{type(e).__name__}: {e}")

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
                    self._log("chat", f"{nick}: {message.content}")
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
                            continue
                        try:
                            segs, _ = self.stt_model.transcribe(audio, language="ko")
                            text = " ".join(s.text.strip() for s in segs).strip()
                            if text:
                                self._log("host", text)
                                self.host_queue.append(("호스트", text))
                        except Exception as e:
                            # 전사 실패로 스레드가 죽으면 이후 음성 입력이 영영 안 된다
                            self._log("error", f"STT: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 화면

    def _capture(self):
        import mss
        from PIL import Image

        v = self.cfg["vision"]
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[v["monitor"]])
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

            threading.Thread(target=self._mic_thread, daemon=True).start()

            self._log("system", "방송을 시작했습니다.")
            self._status("running", True)

            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._main())

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

        self._kill_server()

        self._status("tts", "중지됨")
        self._status("chzzk", "중지됨")
        self._status("running", False)
        self._log("system", "방송을 종료했습니다.")

    # ---------------------------------------------------------------- 입력

    def send_host(self, text):
        self.host_queue.append(("호스트", text))
        self._log("host", text)

    def send_chat(self, nick, text):
        self.chat_queue.append((nick, text))
        self._log("chat", f"{nick}: {text}")

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
