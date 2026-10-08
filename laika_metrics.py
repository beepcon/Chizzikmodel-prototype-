# =============================================================================
# 응답 시간 측정
#
# 검증에서 tool use 응답이 평균 3.55초로 나왔는데(10회 시행),
# 기존 텍스트 전용 호출의 시간을 잰 기록이 없어 비교가 불가능했다.
# 그래서 방송 중에 상시 기록해 둔다.
#
# 구간을 나눠 기록하는 이유:
#   첫 소리까지의 지연은 LLM + 첫 문장 TTS 이고, 표정은 그와 별개다.
#   합계만 재면 어디가 느린지 알 수 없다.
#
# metrics.csv 는 엑셀로 바로 열린다. 그래프는 laika_metrics_view.py 로 본다.
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   기록 on/off 와 파일명 : config.json 의 metrics 항목
# =============================================================================

import csv
import sys
import threading
import time
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "file": "metrics.csv",
}

COLUMNS = [
    "시각", "종류", "입력길이", "문장수",
    "llm초", "tool사용", "감정이탈",
    "첫문장tts초", "첫소리까지초", "tts합계초", "표정합계초", "전체초",
    # 스트리밍이 실제로 쓰였는지. "성공/전체" 문장 수.
    # 09-22 방송에서 첫문장tts초가 3.7~8.7초였는데(측정 1.495초),
    # 스트리밍이 몇 번 되돌아갔는지 알 길이 없어 원인을 못 좁혔다.
    "스트리밍",
    "오류",
]


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class Metrics:
    def __init__(self, cfg=None, log=None):
        raw = (cfg or {}).get("metrics") or {}
        self.cfg = dict(DEFAULTS)
        self.cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
        self.log = log
        self.path = base_dir() / self.cfg["file"]
        self._lock = threading.Lock()

        if self.cfg["enabled"] and not self.path.exists():
            try:
                with open(self.path, "w", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow(COLUMNS)
            except Exception as e:
                if self.log:
                    self.log("error", f"[측정] 파일 생성 실패: {e}")
        elif self.cfg["enabled"]:
            self._upgrade_header()

    def _upgrade_header(self):
        """예전 머리글로 된 파일이면 지금 열 순서로 다시 쓴다.

        write 는 COLUMNS 순서로 줄을 쓰는데, 열이 늘기 전에 만든 파일은
        머리글이 짧아서 값이 한 칸씩 밀린다.
        (2026-10-08 확인: 13열 머리글 파일에 14열을 쓰니 '스트리밍' 값이 '오류' 열에 들어갔다)
        기존 값은 열 이름으로 옮기고, 원본은 .bak_날짜 로 남긴다."""
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            if not rows or rows[0] == COLUMNS:
                return
            head = rows[0]
            unknown = [c for c in head if c not in COLUMNS]
            if unknown:
                if self.log:
                    self.log("error", f"[측정] {self.path.name} 머리글에 모르는 열이 있어 "
                                      f"그대로 둡니다: {unknown}")
                return
            backup = self.path.with_name(
                f"{self.path.name}.bak_{time.strftime('%Y%m%d_%H%M%S')}")
            backup.write_bytes(self.path.read_bytes())
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(COLUMNS)
                for r in rows[1:]:
                    d = dict(zip(head, r))
                    w.writerow([d.get(c, "") for c in COLUMNS])
            tmp.replace(self.path)
            if self.log:
                self.log("system", f"[측정] {self.path.name} 머리글을 새 열 순서로 바꿨습니다 "
                                   f"({len(head)}열 -> {len(COLUMNS)}열, {len(rows) - 1}줄). "
                                   f"원본: {backup.name}")
        except Exception as e:
            if self.log:
                self.log("error", f"[측정] 머리글 갱신 실패: {type(e).__name__}: {e}")

    def write(self, row: dict):
        """한 응답의 측정값을 한 줄 기록한다. 실패해도 방송을 막지 않는다."""
        if not self.cfg["enabled"]:
            return
        try:
            with self._lock:
                with open(self.path, "a", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow(
                        [row.get(c, "") for c in COLUMNS])
        except Exception as e:
            if self.log:
                self.log("error", f"[측정] 기록 실패: {type(e).__name__}: {e}")


class Timer:
    """구간 시간을 재는 도우미.

        t = Timer()
        with t.section("llm"):
            ...
        t.get("llm")   -> 초
    """

    def __init__(self):
        self.t0 = time.time()
        self.sections = {}

    def section(self, name):
        return _Section(self, name)

    def add(self, name, seconds):
        self.sections[name] = self.sections.get(name, 0.0) + seconds

    def get(self, name, digits=3):
        v = self.sections.get(name)
        return round(v, digits) if v is not None else ""

    def total(self, digits=3):
        return round(time.time() - self.t0, digits)

    def since_start(self, digits=3):
        return round(time.time() - self.t0, digits)


class _Section:
    def __init__(self, timer, name):
        self.timer, self.name = timer, name

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *a):
        self.timer.add(self.name, time.time() - self.t0)
        return False
