# =============================================================================
# 화면 캡처 대상
#
# 세 가지 방식을 지원한다.
#   monitor : 모니터 전체
#   window  : 특정 창만 (창을 옮기거나 크기를 바꿔도 따라간다)
#   region  : 화면의 사각형 영역 (좌표 고정)
#
# 게임이 전체화면 전용 모드면 어떤 방식이든 검은 화면이 잡힐 수 있다.
# 테두리 없는 창 모드를 써야 한다.
# =============================================================================

import sys


def list_windows():
    """보이는 창 목록을 반환한다. [{title, hwnd, rect}]

    제목이 없거나 최소화된 창, 크기가 지나치게 작은 창은 제외한다.
    캡처 대상으로 의미가 없기 때문이다."""
    if sys.platform != "win32":
        return []

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    results = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 200 or h < 150:          # 아이콘 수준의 창은 제외
            return True
        if rect.left <= -30000:          # 최소화된 창
            return True

        results.append({
            "title": title,
            "hwnd": int(hwnd),
            "rect": {"left": rect.left, "top": rect.top, "width": w, "height": h},
        })
        return True

    user32.EnumWindows(EnumWindowsProc(callback), None)

    results.sort(key=lambda x: x["title"].lower())
    return results


def get_window_rect(hwnd):
    """창의 현재 위치와 크기를 다시 읽는다.
    창을 옮기거나 크기를 바꿔도 따라가려면 캡처할 때마다 갱신해야 한다."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return None

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0 or rect.left <= -30000:
            return None

        return {"left": rect.left, "top": rect.top, "width": w, "height": h}
    except Exception:
        return None


def find_window_by_title(title):
    """제목으로 창을 다시 찾는다.
    창을 껐다 켜면 hwnd 가 바뀌므로 제목으로 되찾을 수 있어야 한다."""
    for w in list_windows():
        if w["title"] == title:
            return w
    for w in list_windows():          # 제목이 조금 바뀌는 창(문서명 등) 대비
        if title and title in w["title"]:
            return w
    return None


def resolve_area(vision_cfg, sct):
    """설정을 실제 캡처 영역으로 바꾼다.

    반환: (영역 dict, 설명 문자열) 또는 (None, 실패 사유)
    """
    mode = vision_cfg.get("mode", "monitor")

    if mode == "window":
        hwnd = vision_cfg.get("window_hwnd")
        title = vision_cfg.get("window_title", "")

        rect = get_window_rect(hwnd) if hwnd else None
        if rect is None and title:
            found = find_window_by_title(title)       # 창을 껐다 켠 경우
            if found:
                rect = found["rect"]
                vision_cfg["window_hwnd"] = found["hwnd"]

        if rect is None:
            return None, f"창을 찾을 수 없습니다: {title or '(지정 안 됨)'}"
        return rect, f"창: {title}"

    if mode == "region":
        r = vision_cfg.get("region") or {}
        if not all(k in r for k in ("left", "top", "width", "height")):
            return None, "캡처 영역이 지정되지 않았습니다."
        if r["width"] < 10 or r["height"] < 10:
            return None, "캡처 영역이 너무 작습니다."
        return dict(r), f"영역: {r['width']}x{r['height']}"

    idx = int(vision_cfg.get("monitor", 1))
    if idx >= len(sct.monitors):
        idx = 1
    return sct.monitors[idx], f"모니터 {idx}"
