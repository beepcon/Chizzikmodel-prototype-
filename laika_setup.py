# =============================================================================
# 준비물 점검
#
# 프로그램 폴더 아래를 훑어서 필요한 것들이 있는지 확인한다.
# 사용자가 안내대로 프로그램 폴더 안에 압축을 풀면 경로를 자동으로 잡아준다.
#
# 자동 다운로드는 하지 않는다.
# 수 GB 파일을 받다 끊기는 처리와 릴리스 주소 변경까지 감당하는 것보다,
# 어디에 풀어야 하는지 알려주고 찾아주는 쪽이 덜 깨진다.
# =============================================================================

import os
import sys
import shutil
from pathlib import Path

LINKS = {
    "sovits": "https://github.com/RVC-Boss/GPT-SoVITS/releases",
    "vbcable": "https://vb-audio.com/Cable/",
    "vtube": "https://store.steampowered.com/app/1325860/VTube_Studio/",
    "apikey": "https://console.anthropic.com/settings/keys",
}

# 프로그램 폴더 아래를 훑을 때 들어가지 않을 폴더
SKIP_DIRS = {
    "__pycache__", "build", "dist", ".git", "node_modules",
    "runtime", "logs", "TEMP", "output",
}


def program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def find_sovits(root: Path = None, max_depth: int = 4):
    """api_v2.py 가 있는 폴더를 찾는다.

    통합패키지는 압축을 풀면 같은 이름 폴더가 한 겹 더 생기는 경우가 있어서,
    폴더 이름이 아니라 api_v2.py 의 위치를 기준으로 찾는다."""
    root = root or program_dir()
    if not root.is_dir():
        return None

    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):
            continue

        for e in entries:
            if e.is_file() and e.name == "api_v2.py":
                # runtime 까지 있어야 실제로 쓸 수 있는 통합패키지다
                if (cur / "runtime" / "python.exe").exists():
                    return cur

        if depth < max_depth:
            for e in entries:
                if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith("."):
                    stack.append((e, depth + 1))
    return None


def find_audio_files(root: Path = None, max_depth: int = 3):
    """참조 음성으로 쓸 만한 오디오 파일을 찾는다."""
    root = root or program_dir()
    exts = {".wav", ".mp3", ".flac"}
    found = []

    stack = [(root, 0)]
    while stack and len(found) < 50:
        cur, depth = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):
            continue

        for e in entries:
            if e.is_file() and e.suffix.lower() in exts:
                found.append(e)
            elif e.is_dir() and depth < max_depth and e.name not in SKIP_DIRS:
                stack.append((e, depth + 1))
    return found


def check_vbcable():
    """VB-CABLE 설치 여부. 오디오 장치 목록에 CABLE 이 있는지로 판단한다."""
    try:
        import sounddevice as sd
        for dev in sd.query_devices():
            if "cable" in dev["name"].lower():
                return True
    except Exception:
        pass
    return False


def check_vtube_running():
    """VTube Studio 실행 여부. 설치 위치가 제각각이라 프로세스로 확인한다."""
    if os.name != "nt":
        return False
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq VTube Studio.exe"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        return "VTube Studio" in out
    except Exception:
        return False


def check_all(cfg: dict):
    """준비물 상태를 점검한다.

    반환: [{key, name, ok, detail, hint, action, link}]
      action: none / link / auto / refmaker
    """
    items = []
    prog = program_dir()

    # 1) API 키
    has_key = bool(cfg.get("api_key", "").strip())
    items.append({
        "key": "apikey", "name": "Anthropic API 키",
        "ok": has_key,
        "detail": "입력됨" if has_key else "입력되지 않음",
        "hint": "대사를 만드는 데 씁니다. 발급 후 설정 - 연결 탭에 붙여넣으세요.",
        "action": "none" if has_key else "link", "link": LINKS["apikey"],
    })

    # 2) GPT-SoVITS
    cur = cfg.get("sovits_dir", "")
    ok = bool(cur) and (Path(cur) / "api_v2.py").exists()
    found = None if ok else find_sovits(prog)
    items.append({
        "key": "sovits", "name": "GPT-SoVITS",
        "ok": ok or found is not None,
        "detail": (cur if ok else (f"찾음: {found}" if found else "없음")),
        "hint": (f"통합패키지(windows 7z package)를 받아 아래 폴더에 압축을 푸세요.\n{prog}"
                 if not (ok or found) else
                 ("프로그램 폴더에서 찾았습니다. 적용을 누르면 경로를 넣습니다."
                  if found else "")),
        "action": "none" if ok else ("auto" if found else "link"),
        "link": LINKS["sovits"], "found": str(found) if found else "",
    })

    # 3) VB-CABLE
    has_cable = check_vbcable()
    items.append({
        "key": "vbcable", "name": "VB-CABLE",
        "ok": has_cable,
        "detail": "설치됨" if has_cable else "설치되지 않음",
        "hint": ("목소리를 아바타로 넘기는 가상 케이블입니다.\n"
                 "받아서 설치한 뒤 재부팅해야 인식됩니다."),
        "action": "none" if has_cable else "link", "link": LINKS["vbcable"],
    })

    # 4) VTube Studio
    running = check_vtube_running()
    items.append({
        "key": "vtube", "name": "VTube Studio",
        "ok": running,
        "detail": "실행 중" if running else "실행 중이 아님",
        "hint": ("아바타를 띄우고 입을 움직입니다. Steam 에서 무료입니다.\n"
                 "실행 후 마이크를 CABLE Output 으로, "
                 "입 파라미터의 Input 을 Voice Volume 계열로 지정하세요."),
        "action": "none" if running else "link", "link": LINKS["vtube"],
    })

    # 5) 참조 음성
    ref = cfg.get("ref_audio", "")
    has_ref = bool(ref) and Path(ref).exists() and bool(cfg.get("ref_text", "").strip())
    audio_files = [] if has_ref else find_audio_files(prog)
    items.append({
        "key": "ref", "name": "참조 음성",
        "ok": has_ref,
        "detail": (Path(ref).name if has_ref else
                   (f"폴더에서 음성 파일 {len(audio_files)}개 발견" if audio_files else "없음")),
        "hint": ("흉내 낼 목소리입니다. 3~10초짜리가 필요한데, "
                 "긴 녹음을 넣으면 알아서 잘라줍니다."),
        "action": "none" if has_ref else "refmaker", "link": "",
    })

    return items
