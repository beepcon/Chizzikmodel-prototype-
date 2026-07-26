# AI Streamer — 치지직 AI 버튜버 파이프라인

치지직(Chzzk) 실시간 채팅에 반응하여 음성으로 답하는 AI 스트리머 프로토타입이다.
시청자 채팅과 호스트의 음성 발화를 입력받아 LLM이 응답을 생성하고,
파인튜닝된 GPT-SoVITS 모델이 커스텀 보이스로 발화한다.

## 아키텍처

````
치지직 채팅 ─────┐
호스트 음성(STT) ─┼─→ 큐(우선순위) ─→ Claude API ─→ GPT-SoVITS(로컬) ─→ 오디오 재생
콘솔 입력 ───────┘                                                        └→ (예정) VTube Studio 립싱크 → OBS
````

- **입력 3계통**: 치지직 채팅(chzzkpy, 웹소켓 자동 재접속), 호스트 마이크(faster-whisper STT), 콘솔
- **큐 정책**: 호스트 발화는 전량 처리, 시청자 채팅은 최신 우선 + 초과분 폐기 (`deque(maxlen=5)`)
- **응답 생성**: Claude API (claude-sonnet-4-6), 최근 20턴 히스토리 유지, 페르소나 시스템 프롬프트
- **음성 합성**: GPT-SoVITS v2Pro 파인튜닝 모델, api_v2 로컬 서버(HTTP) 호출
- **예외 복구**: 워커 예외 격리, 치지직 연결 단절 시 5초 간격 무한 재접속

## 음성 모델

다중 화자 데이터를 혼합 학습하여 특정 인물로 식별되지 않는 합성 음색을 목표로 한다.

1. 원본 음성 → WebUI 슬라이싱(5~10초 조각) → Faster Whisper 배치 전사 → `.list` 생성
2. SoVITS(epoch 8) / GPT(epoch 15) 파인튜닝 — RTX 3090 기준 약 30분~1시간
3. 추론 시 메인 참조 + 보조 참조(`aux_ref_audio_paths`)로 음색 블렌딩

> 참조 음성·모델 가중치는 저장소에 포함하지 않는다. 별도로 준비할 것.

## 요구 사항

- Windows + NVIDIA GPU (개발 환경: RTX 3090 24GB)
- Python 3.10+ / GPT-SoVITS v2Pro 통합 패키지 (별도 설치)
- Anthropic API 키

````
pip install -r requirements.txt
````

## 실행

````powershell
# 1) TTS 서버 (GPT-SoVITS 폴더에서, 별도 창 유지)
.\runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml

# 2) API 키 등록 (최초 1회)
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")

# 3) 메인 실행
python main.py
````

`main.py` 상단 설정에서 채널 ID, 참조 음성 경로, 페르소나를 수정한다.
콘솔 입력: `/내용` = 호스트 발언(우선 처리), `닉네임: 내용` = 시청자 채팅 시뮬레이션.

### 설정 참고

- STT용 CUDA DLL 경로(`_dll_dirs`)는 환경에 맞게 수정 (nvidia-cublas-cu12 / cudnn 설치 위치)
- CUDA 불가 시 `WhisperModel("small", device="cpu", compute_type="int8")`로 폴백
- 마이크 감도는 `STT_THRESHOLD`(0.005~0.03)로 조정
- 에코 루프 방지를 위해 호스트는 헤드폰 사용 권장

## 로드맵 (Issues)

| # | 항목 | 상태 |
|---|---|---|
| 1 | 음성 모델 화자 희석 재학습 (AI Hub 다화자 데이터 추가, 화자당 40% 미만) | 진행 중 |
| 2 | 응답 레이턴시 최적화 (LLM 스트리밍 + TTS 문장 분할, 목표 3초 이내) | 예정 |
| 3 | 무채팅 구간 혼잣말 기능 | 예정 |
| 4 | 아바타 연동 (VTube Studio + VB-Cable 립싱크 + OBS) | 다음 작업 |
| 5 | 호스트 음성 입력 (STT) | **완료** — CUDA 동작, 환경 통합 정리 잔여 |
| 6 | 치지직 공식 API 전환 (현재 비공식 chzzkpy 2.x) | 예정 |
| 7 | 채팅 선별 로직 고도화 (묶음 응답 적용됨 → 랜덤 추출/도배 필터) | 일부 완료 |
| 8 | 예외 처리 및 복구 | 일부 완료 — 워커/재접속 적용, TTS 서버 다운 폴백 잔여 |
| 9 | LLM 로컬 전환 (EXAONE/Qwen + Ollama, API 비용 실측 후 착수 판단) | 보류 |
| 10 | 게임 화면 실황 (vision 기반 관전 모드) | 예정 |
| 11 | 게임 자동 조작 (장기) | 장기 |

## 개발 노트 (삽질 기록)

- Whisper 타임스탬프는 침묵을 포함할 수 있어 참조 음성 추출 시 에너지 기반 트리밍 필수
- 참조 음성은 실발화 3~10초, 대사(`prompt_text`) 정확 일치가 품질을 좌우함
- WebUI 훈련의 "완료됨" 표시는 프로세스 종료 시 무조건 출력됨 — 가중치 파일 존재로만 성공 판정할 것
- OOM 에러가 "free 충분" 상태로 나오면 VRAM이 아닌 시스템 RAM(커밋 한계)/좀비 프로세스를 의심
- `OMP Error #15`는 `KMP_DUPLICATE_LIB_OK=TRUE`로 우회
- chzzkpy 2.x부터 비공식 API는 `chzzkpy.unofficial.chat`으로 이동
- `os.add_dll_directory`가 무시되는 경우 PATH 선두 추가로 해결, 단 DLL 등록은 faster_whisper import 이전에 실행
