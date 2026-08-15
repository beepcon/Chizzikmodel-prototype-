# =============================================================================
# 라이카 exe 빌드
#
#   python build_exe.py
#
# 결과: dist/라이카/ 폴더. 안의 라이카.exe 를 실행하면 GUI 가 뜬다.
# 이 폴더를 통째로 옮겨서 쓰면 되고, 설정은 프로그램 안에서 하므로
# config.json 을 직접 만질 필요가 없다. (없으면 첫 저장 때 자동 생성된다)
#
# --onedir 를 쓰는 이유:
#   torch / ctranslate2 같은 네이티브 DLL 이 --onefile 로 묶이면
#   실행할 때마다 임시폴더에 수 GB 를 풀어야 해서 시작이 매우 느리고
#   DLL 을 못 찾아 실패하는 일이 잦다. onedir 은 폴더째 배포하지만 안정적이다.
# =============================================================================

import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
APP_NAME = "라이카"


def ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        print(f"[빌드] {pkg} 설치 중...")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg], check=True)


def build():
    ensure("pyinstaller", "PyInstaller")

    for folder in ("build", "dist"):
        p = BASE / folder
        if p.exists():
            shutil.rmtree(p)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--windowed",              # 검은 콘솔 창을 띄우지 않는다
        "--name", APP_NAME,
        "--clean",
        "--noconfirm",

        # 코어를 함께 묶는다
        "--add-data", f"{BASE / 'laika_core.py'};.",
        "--add-data", f"{BASE / 'laika_refmaker.py'};.",
        "--add-data", f"{BASE / 'laika_setup.py'};.",

        # PyInstaller 가 동적 import 를 찾지 못하는 것들
        "--hidden-import", "laika_core",
        "--hidden-import", "laika_refmaker",
        "--hidden-import", "laika_setup",
        "--hidden-import", "anthropic",
        "--hidden-import", "chzzkpy",
        "--hidden-import", "chzzkpy.unofficial",
        "--hidden-import", "chzzkpy.unofficial.chat",
        "--hidden-import", "ahttp_client",
        "--hidden-import", "ahttp_client.extension",
        "--hidden-import", "faster_whisper",
        "--hidden-import", "ctranslate2",
        "--hidden-import", "sounddevice",
        "--hidden-import", "soundfile",
        "--hidden-import", "mss",
        "--hidden-import", "keyboard",
        "--hidden-import", "scipy",
        "--hidden-import", "scipy.signal",
        "--hidden-import", "PIL",

        # 데이터/바이너리 수집
        "--collect-all", "customtkinter",
        "--collect-data", "faster_whisper",
        "--collect-binaries", "ctranslate2",
        "--collect-binaries", "sounddevice",
        "--collect-binaries", "soundfile",
        "--collect-all", "nvidia",

        str(BASE / "laika_gui.py"),
    ]

    print("[빌드] 시작합니다. 수 분 걸립니다...")
    if subprocess.run(cmd).returncode != 0:
        print("[빌드] 실패했습니다. 위 오류를 확인하세요.")
        return

    out_dir = BASE / "dist" / APP_NAME
    for name in ("사용법.txt",):
        src = BASE / name
        if src.exists():
            shutil.copy(src, out_dir / name)

    print(f"\n[빌드] 완료: {out_dir}")
    print("      폴더를 통째로 옮겨서 라이카.exe 를 실행하세요.")


if __name__ == "__main__":
    build()
