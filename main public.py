# main.py - AI 잡담 방송 콘솔 프로토타입
# 구조: 콘솔 입력(채팅 시뮬) -> 최신우선 큐 -> Claude 응답 생성 -> GPT-SoVITS -> 재생
# 재생 중에도 다음 응답을 미리 생성하는 비동기 파이프라인


# ===== 최상단: DLL 등록 (faster_whisper import 전에 실행돼야 함) =====
import os
import sys

_dll_dirs = [
    r"C:\Users\Administrator\miniconda3\envs\myenv\Lib\site-packages\nvidia\cublas\bin",
    r"C:\Users\Administrator\miniconda3\envs\myenv\Lib\site-packages\nvidia\cudnn\bin",
]
for _d in _dll_dirs:
    if os.path.isdir(_d):
        os.add_dll_directory(_d)
        os.environ["PATH"] = _d + os.pathsep + os.environ["PATH"]
        print(f"[STT] DLL 등록: {_d}")
    else:
        print(f"[STT] 경로 없음: {_d}")

import asyncio
import collections
import io
import threading
from pathlib import Path

import numpy as np
import anthropic
import requests
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from chzzkpy.unofficial.chat import ChatClient
from chzzkpy import Client

# ===== STT 설정 =====
STT_SAMPLE_RATE = 16000
STT_SILENCE_SEC = 1.0      # 이 시간 이상 조용하면 발화 종료
STT_MIN_SPEECH_SEC = 0.5   # 이보다 짧은 소리는 무시
STT_THRESHOLD = 0.01       # 음성 감지 볼륨 (환경 따라 0.005~0.03 조정)

stt_model = WhisperModel("small", device="cuda", compute_type="float16")
# CUDA 실패 시 아래로 교체:
# stt_model = WhisperModel("small", device="cpu", compute_type="int8")
def mic_listener():
    """마이크 상시 청취 -> 발화 종료 감지 -> 전사 -> host_queue 투입"""
    buf = []
    silence = 0.0
    blocksize = int(STT_SAMPLE_RATE * 0.1)

    with sd.InputStream(samplerate=STT_SAMPLE_RATE, channels=1,
                        blocksize=blocksize, dtype="float32") as stream:
        print("[STT] 마이크 대기 중")
        while True:
            block, _ = stream.read(blocksize)
            vol = float(np.abs(block).mean())

            if vol > STT_THRESHOLD:
                buf.append(block.copy())
                silence = 0.0
            elif buf:
                buf.append(block.copy())
                silence += 0.1
                if silence >= STT_SILENCE_SEC:
                    audio = np.concatenate(buf).flatten()
                    buf, silence = [], 0.0
                    if len(audio) / STT_SAMPLE_RATE >= STT_MIN_SPEECH_SEC:
                        try:
                            segs, _ = stt_model.transcribe(audio, language="ko")
                            text = " ".join(s.text.strip() for s in segs).strip()
                            if text:
                                print(f"\n[호스트 음성] {text}\n> ", end="", flush=True)
                                host_queue.append(("호스트", text))
                        except Exception as e:
                            print(f"\n[STT 에러] {type(e).__name__}: {e}\n> ", end="", flush=True)


# ===== 설정 =====
ANTHROPIC_API_KEY = ""  # 본인 키로 교체
SOVITS_URL = "http://127.0.0.1:9880/tts"
REF_AUDIO = r"D:\ai\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\output\slicer_opt\ko_main -1.wav_0007823680_0007964800.wav"         # 참조 음성 경로
REF_TEXT = "그래도 오랜만에 플레이 한 것 치고는 잘했어요"   # 참조 음성 대사
QUEUE_SIZE = 5  # 이 개수 넘는 오래된 채팅은 자동 폐기
CHZZK_CHANNEL_ID = "ce4812fb6924f2cba428143380ab6096"  # 치지직 채널 URL의 해시


PERSONA = """너는 AI 버튜버 '라이카'이다. 시청자 채팅에 반응하는 잡담 방송 중이다.
- 말투:밝고, 친근하고 장난기 있게
- 답변은 1~3문장, 짧고 리듬감 있게 (TTS로 읽히므로 이모티콘/특수문자 금지)
- 가능하면 존댓말로 이야기 하기 
- 게임과 애니를 좋아함
- 정치/종교/혐오 주제는 자연스럽게 회피
- [호스트]가 말하면 방송 진행자의 말이다. 시청자보다 우선해서 자연스럽게 대화해라
- 우앙4시는 라이카가를 개발하는 개발자다.
- 여러 채팅이 한번에 오면 전부에 개별 대답하지 말고 자연스럽게 묶어서 반응해라
"""
import os
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
chat_queue = collections.deque(maxlen=QUEUE_SIZE)  # maxlen 초과 시 오래된 것부터 자동 삭제
host_queue = collections.deque()  # 호스트 입력은 폐기 없이 전부 처리
history = []  # 방송 흐름 유지용 대화 기록




def llm_reply(username: str, message: str) -> str:
    """Claude로 응답 생성 (동기 함수, 스레드에서 실행됨)"""
    history.append({"role": "user", "content": f"[{username}] {message}"})
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=PERSONA,
        messages=history[-20:],  # 최근 20턴만 유지
    )
    text = resp.content[0].text.strip()
    history.append({"role": "assistant", "content": text})
    return text


def tts(text: str) -> tuple:
    payload = {
        "text": text,
        "text_lang": "ko",
        "ref_audio_path": REF_AUDIO,
        "prompt_text": REF_TEXT,
        "prompt_lang": "ko",
        "aux_ref_audio_paths": [
            r"D:\ai\refs\ko_main_ref32.wav",
            r"D:\ai\refs\upfiles5_c787f355f28010322fd8db0bdc4b02400.mp3",
            r"D:\ai\refs\upfiles8_2a889c8f2784ff1ef86073e3ed9e00e20.mp3",
        ],
        "top_k": 34, "top_p": 0.9, "temperature": 0.8,
        "streaming_mode": False,
    }
    r = requests.post(SOVITS_URL, json=payload, timeout=120)
    r.raise_for_status()
    data, sr = sf.read(io.BytesIO(r.content))
    return data, sr


def play(data, sr):
    sd.play(data, sr)
    sd.wait()


async def worker():
    """큐에서 최신 채팅을 꺼내 응답->TTS->재생. 재생 중 다음 생성이 겹치도록 파이프라인화"""
    loop = asyncio.get_event_loop()
    pending_audio = None  # (data, sr, 자막텍스트)

    while True:
        # 호스트 우선, 다음 최신 시청자 채팅
        if host_queue:
            username, message = host_queue.popleft()
        elif chat_queue:
            username, message = chat_queue.pop()
            chat_queue.clear()
        else:
            await asyncio.sleep(0.2)
            continue
        print(f"\n[처리중] {username}: {message}")
        try:
            reply = await loop.run_in_executor(None, llm_reply, username, message)
            data, sr = await loop.run_in_executor(None, tts, reply)
            print(f"🔊 AI: {reply}\n> ", end="", flush=True)
            await loop.run_in_executor(None, play, data, sr)
        except Exception as e:
            print(f"\n[에러] {type(e).__name__}: {e}\n> ", end="", flush=True)


        # 1) 최신 채팅 꺼내서 응답+TTS 생성 태스크 시작
        gen_task = None
        if chat_queue:
            username, message = chat_queue.pop()  # 오른쪽 = 최신 우선
            chat_queue.clear()  # 가볍게: 답하는 동안 쌓인 나머지는 버림
        elif chat_queue:
            batch = list(chat_queue)[-3:]   # 최근 3개
            chat_queue.clear()
            username = "여러명"
            message = " / ".join(f"[{u}] {m}" for u, m in batch)

            def generate(u=username, m=message):
                try:
                    reply = llm_reply(u, m)
                    data, sr = tts(reply)
                    return data, sr, reply
                except Exception as e:
                    print(f"\n[에러] {type(e).__name__}: {e}\n> ", end="", flush=True)
                    return None

            gen_task = None
            if host_queue or chat_queue:
                if host_queue:
                     username, message = host_queue.popleft()
                else:
                     username, message = chat_queue.pop()
                     chat_queue.clear

        # 2) 이전에 만들어둔 오디오가 있으면 지금 재생 (생성과 병렬)
        if pending_audio is None and gen_task is None:
            continue
        if pending_audio is not None:
            data, sr, reply = pending_audio
            print(f"\n🔊 라이카: {reply}\n> ", end="", flush=True)
            await loop.run_in_executor(None, play, data, sr)
            pending_audio = None

        # 3) 생성 완료 대기 -> 다음 루프에서 재생
        if gen_task is not None:
            pending_audio = await gen_task


def console_input():
    """'/내용' = 호스트(방장) 발언, 그 외 = 시청자 채팅 시뮬"""
    while True:
        line = input("> ").strip()
        if not line:
            continue
        if line.startswith("/"):
            host_queue.append(("호스트", line[1:].strip()))
        elif ":" in line:
            username, message = line.split(":", 1)
            chat_queue.append((username.strip(), message.strip()))
        else:
            chat_queue.append(("시청자", line))

# === 치지직 채팅 ===
chat_client = ChatClient(CHZZK_CHANNEL_ID)

@chat_client.event
async def on_chat(message):
    username = message.profile.nickname if message.profile else "익명"
    chat_queue.append((username, message.content))

async def chzzk_listener():
    global chat_client
    while True:
        try:
            chat_client = ChatClient(CHZZK_CHANNEL_ID)

            @chat_client.event
            async def on_chat(message):
                username = message.profile.nickname if message.profile else "익명"
                chat_queue.append((username, message.content))

            print("\n[치지직] 연결 시도...\n> ", end="", flush=True)
            await chat_client.start()
            print("\n[치지직] 연결 종료됨, 5초 후 재접속\n> ", end="", flush=True)
        except Exception as e:
            print(f"\n[치지직] 오류: {type(e).__name__}: {e}, 5초 후 재접속\n> ", end="", flush=True)
        await asyncio.sleep(5)
async def chzzk_listener():
    await chat_client.start()


# === 호스트 음성 입력 (STT) ===
import numpy as np
from faster_whisper import WhisperModel

# CUDA DLL 경로 등록 (어제 노트북에서 쓴 그 방식)
import sys
from pathlib import Path
_nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
for _f in list(_nvidia.rglob("cublas64_12.dll")) + list(_nvidia.rglob("cudnn_ops*.dll")):
    os.add_dll_directory(str(_f.parent))

STT_SAMPLE_RATE = 16000
STT_SILENCE_SEC = 1.0      # 이 시간 이상 조용하면 발화 종료로 판단
STT_MIN_SPEECH_SEC = 0.5   # 너무 짧은 소리는 무시 (키보드 소리 등)
STT_THRESHOLD = 0.01       # 음성 감지 볼륨 기준 (환경 따라 조정)

stt_model = WhisperModel("small", device="cuda", compute_type="float16")

def mic_listener():
    """마이크 상시 청취 -> 발화 끝나면 전사 -> host_queue 투입"""
    buf = []
    silence = 0.0
    blocksize = int(STT_SAMPLE_RATE * 0.1)  # 0.1초 단위

    with sd.InputStream(samplerate=STT_SAMPLE_RATE, channels=1,
                        blocksize=blocksize, dtype="float32") as stream:
        print("[STT] 마이크 대기 중")
        while True:
            block, _ = stream.read(blocksize)
            vol = float(np.abs(block).mean())

            if vol > STT_THRESHOLD:
                buf.append(block.copy())
                silence = 0.0
            elif buf:
                buf.append(block.copy())
                silence += 0.1
                if silence >= STT_SILENCE_SEC:
                    audio = np.concatenate(buf).flatten()
                    buf, silence = [], 0.0
                    if len(audio) / STT_SAMPLE_RATE >= STT_MIN_SPEECH_SEC:
                        try:
                            segs, _ = stt_model.transcribe(audio, language="ko")
                            text = " ".join(s.text.strip() for s in segs).strip()
                            if text:
                                print(f"\n[호스트 음성] {text}\n> ", end="", flush=True)
                                host_queue.append(("호스트", text))
                        except Exception as e:
                            print(f"\n[STT 에러] {type(e).__name__}: {e}\n> ", end="", flush=True)

async def main():
    print("=== AI 방송 시작 ===")
    threading.Thread(target=console_input, daemon=True).start()
    threading.Thread(target=mic_listener, daemon=True).start()
    await asyncio.gather(
        worker(),
        chzzk_listener(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n방송 종료")