# main.py - AI 잡담 방송 콘솔 프로토타입
# 구조: 콘솔 입력(채팅 시뮬) -> 최신우선 큐 -> Claude 응답 생성 -> GPT-SoVITS -> 재생
# 재생 중에도 다음 응답을 미리 생성하는 비동기 파이프라인

import asyncio
import collections
import io
import threading

import anthropic
import requests
import sounddevice as sd
import soundfile as sf

# ===== 설정 =====
ANTHROPIC_API_KEY = "" # 본인 키로 교체
SOVITS_URL = "http://127.0.0.1:9880/tts"
REF_AUDIO = r"D:\ai\GPT-SoVITS-v2pro-20250604\GPT-SoVITS-v2pro-20250604\output\slicer_opt\ko_main -1.wav_0007823680_0007964800.wav"         # 참조 음성 경로
REF_TEXT = "그래도 오랜만에 플레이 한 것 치고는 잘했어요"   # 참조 음성 대사
QUEUE_SIZE = 5  # 이 개수 넘는 오래된 채팅은 자동 폐기

PERSONA = """너는 AI 버튜버 '라이카'이다. 시청자 채팅에 반응하는 잡담 방송 중이다.
- 말투:밝고, 친근하고 장난기 있게
- 답변은 1~3문장, 짧고 리듬감 있게 (TTS로 읽히므로 이모티콘/특수문자 금지)
- 가능하면 존댓말로 이야기 하기 
- 게임과 애니를 좋아함
- 정치/종교/혐오 주제는 자연스럽게 회피
"""

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
chat_queue = collections.deque(maxlen=QUEUE_SIZE)  # maxlen 초과 시 오래된 것부터 자동 삭제
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
        if not chat_queue and pending_audio is None:
            await asyncio.sleep(0.2)
            continue

        # 1) 최신 채팅 꺼내서 응답+TTS 생성 태스크 시작
        gen_task = None
        if chat_queue:
            username, message = chat_queue.pop()  # 오른쪽 = 최신 우선
            chat_queue.clear()  # 가볍게: 답하는 동안 쌓인 나머지는 버림

            def generate(u=username, m=message):
                reply = llm_reply(u, m)
                data, sr = tts(reply)
                return data, sr, reply

            gen_task = loop.run_in_executor(None, generate)

        # 2) 이전에 만들어둔 오디오가 있으면 지금 재생 (생성과 병렬)
        if pending_audio is not None:
            data, sr, reply = pending_audio
            print(f"\n🔊 AI: {reply}\n> ", end="", flush=True)
            await loop.run_in_executor(None, play, data, sr)
            pending_audio = None

        # 3) 생성 완료 대기 -> 다음 루프에서 재생
        if gen_task is not None:
            pending_audio = await gen_task


def console_input():
    """콘솔로 채팅 시뮬레이션. '유저명: 내용' 또는 그냥 내용 입력"""
    while True:
        line = input("> ").strip()
        if not line:
            continue
        if ":" in line:
            username, message = line.split(":", 1)
        else:
            username, message = "시청자", line
        chat_queue.append((username.strip(), message.strip()))


async def main():
    print("=== AI 잡담 방송 프로토타입 ===")
    print("채팅 입력 (형식: '닉네임: 내용' 또는 그냥 내용)\n")
    threading.Thread(target=console_input, daemon=True).start()
    await worker()


if __name__ == "__main__":
    asyncio.run(main())