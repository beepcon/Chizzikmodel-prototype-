# =============================================================================
# 라이카 - GUI
#
# 단일 창 구성
#   좌측 : 대화 로그 + 입력창
#   우측 : 상태 / 조작 버튼 / 설정
#
# 파이프라인은 laika_core.LaikaCore 가 담당한다.
# 코어의 콜백은 워커 스레드에서 오므로 큐에 넣고 Tk 타이머에서만 위젯을 만진다.
# (Tk 위젯을 다른 스레드에서 직접 건드리면 프로그램이 죽는다)
# =============================================================================

import json
import queue
import sys
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

# ---------------------------------------------------------------- 외형

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

BG          = "#16161e"
PANEL       = "#1e1e2a"
PANEL_SOFT  = "#252534"
ACCENT      = "#7c6cf5"
ACCENT_HOV  = "#6a59e8"
DANGER      = "#e5484d"
DANGER_HOV  = "#cf3d42"
OK          = "#4ec9a5"
WARN        = "#e0b070"
MUTED       = "#8b8ba3"
TEXT        = "#e8e8f0"
LINE        = "#2c2c40"

COLORS = {
    "system": MUTED,
    "host":   "#7fb8ff",
    "chat":   "#a9d977",
    "laika":  "#f0a8d0",
    "error":  "#ff8a8a",
}
LABELS = {
    "system": "시스템", "host": "나", "chat": "채팅",
    "laika": "라이카", "error": "오류",
}

FONT = "Malgun Gothic" if sys.platform == "win32" else "Noto Sans CJK KR"

MODE_LABEL = {"monitor": "모니터 전체", "window": "특정 창", "region": "지정 영역"}
LABEL_MODE = {v: k for k, v in MODE_LABEL.items()}


# ---------------------------------------------------------------- 설정

def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_PATH = base_dir() / "config.json"

DEFAULT_CFG = {
    "api_key": "",
    "model": "claude-sonnet-4-6",
    "max_tokens": 200,
    "chzzk_channel_id": "",
    "sovits_dir": "",
    "sovits_url": "http://127.0.0.1:9880/tts",
    "ref_audio": "",
    "ref_text": "",
    "aux_ref_audio": [],
    # streaming_mode 는 GPT-SoVITS 의 조각 반환 방식이다 (api_v2.py 44행).
    #   0 끔 / 1 Best Quality, Slowest / 2 Medium / 3 Lower Quality, Faster
    # 2026-09-18 듣기 비교에서 1 을 골랐다.
    # 첫 소리까지 실측 평균: 0 은 4.030초, 1 은 1.495초.
    # apology 는 TTS 서버를 껐다 켠 뒤 라이카가 먼저 하는 한마디다.
    # apology_emotion 은 그때 지을 표정. 모델에 없으면 준비물 점검에서 알려 준다.
    "tts": {"top_k": 34, "top_p": 0.9, "temperature": 0.8, "streaming_mode": 1,
            "apology": "죄송해요, 잠깐 목이 막혔어요.", "apology_emotion": "사과"},
    # 화면 로그를 파일로도 남긴다. 방송 뒤에 원인을 되짚을 때 쓴다.
    "laika_log": {"enabled": True, "file": "laika.log", "max_mb": 20},
    "audio": {"cable_device_name": "CABLE Input",
              "cable_device_index": None, "enable_monitor": True},
    # min_rms / no_speech_threshold / log_prob_threshold 는 무음 오인식 대책이다.
    # 2026-08-29 방송 162건 측정: 무음 6건 rms 0.0051~0.0169 no_speech 0.6353~0.8110,
    # 정상 156건 rms 0.0213~0.1380 no_speech 최대 0.5605. 둘 다 오분류 0건.
    # log_prob_threshold 는 None 이어야 no_speech_prob 만으로 판정한다.
    "stt": {"model_size": "medium", "device": "cuda", "silence_sec": 1.5,
            "min_speech_sec": 0.5, "threshold": 0.01, "toggle_key": "f8",
            "min_rms": 0.02,
            "no_speech_threshold": 0.6, "log_prob_threshold": None},
    "vision": {"enabled": False, "interval_sec": 30,
               "mode": "monitor",
               "monitor": 1,
               "window_title": "", "window_hwnd": None,
               "region": None,
               "max_width": 1024, "jpeg_quality": 70},
    "chat": {"queue_size": 5, "batch": 3},

    # 대화 기억. 예전에는 코드에 20 이 박혀 있었고 그것도 턴이 아니라
    # 메시지 개수여서 실제로는 10턴만 기억했다. 이제 턴 단위다.
    # 화면(비전) 은 별도 통이라 채팅 기억을 밀어내지 않는다.
    "history": {
        "chat_turns": 20,
        "vision_turns": 3,
        "persist": True,            # 방송을 껐다 켜도 이어간다
        "dir": "sessions",
        "resume_within_hours": 12,
    },

    # 표정. vts_emotion_map.json 의 핫키 이름이 비어 있으면 자동으로 건너뛴다.
    "emotion": {
        "enabled": True,
        "map_file": "vts_emotion_map.json",
        "skip_if_same": True,       # 직전과 같은 감정이면 호출하지 않는다
        "timeout_sec": 3.0,
        # 발화가 끝나고 이 시간 뒤에 맨 얼굴로 돌아간다.
        # laika_emotion.DEFAULTS 에는 있었는데 여기 빠져 있어 UI 에 못 올렸다.
        "reset_after_sec": 2.0,
    },

    # VTube Studio 연결. vts_config.json 보다 이쪽이 우선한다.
    "vts": {"host": "127.0.0.1", "port": 8001},

    # 응답 시간 기록. 어디가 느린지 보려면 필요하다.
    "metrics": {"enabled": True, "file": "metrics.csv"},

    # GPT-SoVITS 서버가 찍는 것을 파일로 남긴다.
    # 버리면 서버 쪽 오류를 영영 못 본다(2026-09-17 TTS 스트리밍 실패가 그랬다).
    # t2s_model.py 의 tqdm(range(1500)) 진행바가 파일에 쌓이므로 상한이 필요하다.
    # 상한을 넘으면 .log.1 로 옮기고 새로 쓴다. 보관은 직전 것 하나.
    "server_log": {"enabled": True, "file": "tts_server.log", "max_mb": 50},

    # 언어 필터. 목록은 word_filter.txt / 예외는 word_allow.txt.
    # 2026-09-16 세션 194건 실측: 출력 쪽 오탐 8건('패' x6, '미쳤' x2),
    # 실제 욕설 0건. 그래서 출력은 기본적으로 기록만 하고 통과시킨다.
    # filter_log.csv 에서 오탐을 정리한 뒤 block_output 을 켠다.
    "filter": {
        "enabled": True,
        "block_input": True,
        "block_output": False,
        "replace_with": "...",
        "log_file": "filter_log.csv",
        "words_file": "word_filter.txt",
        "allow_file": "word_allow.txt",
    },
    "persona": (
        "너는 AI 버튜버 '라이카'이다. 시청자 채팅에 반응하는 잡담 방송 중이다.\n"
        "- 말투: 밝고 친근하고 장난기 있게, 존댓말\n"
        "- 답변은 1~3문장, 짧고 리듬감 있게 (TTS 로 읽히므로 이모티콘/특수문자 금지)\n"
        "- 숫자와 영문 약어는 소리나는 대로 한글로 써라.\n"
        "  8000 이 아니라 팔천, LP 가 아니라 라이프포인트, 2500 이 아니라 이천오백.\n"
        "  읽을 수 없는 표기가 그대로 나가면 자릿수를 하나씩 읽어버린다.\n"
        "- 영어 단어나 영어 표현을 쓰지 마라. 외래어가 꼭 필요하면 한글로 소리나는 대로 써라.\n"
        "  hibernation 이 아니라 동면, game 이 아니라 게임, Master Duel 이 아니라 마스터 듀얼.\n"
        "- 게임과 애니를 좋아함\n"
        "- 정치/종교/혐오 주제는 자연스럽게 회피\n"
        "- [호스트]가 말하면 방송 진행자의 말이다. 시청자보다 우선해서 대화해라\n"
        "- 여러 채팅이 한번에 오면 개별로 답하지 말고 자연스럽게 묶어서 반응해라\n"
        "- [게임화면]이 오면 호스트가 플레이 중인 화면을 네가 직접 본 것이다.\n"
        "  너는 화면을 볼 수 있다. 화면을 못 본다거나 텍스트로만 받았다고 말하지 마라.\n"
        "  옆에서 같이 보는 친구처럼 짧게 리액션해라\n"
        "- 화면에서 또렷하게 보이는 것만 말해라. 흐릿하거나 확실하지 않은 것은\n"
        "  아는 척하지 말고, 잘 안 보인다고 하거나 그냥 넘어가라.\n"
        "  글자가 안 읽히면 무슨 내용인지 지어내지 마라.\n"
        "- 화면에 대해 이미 말한 내용은 다시 설명하지 마라.\n"
        "  달라진 점이나 새로 눈에 띄는 것에만 짧게 반응하고,\n"
        "  달라진 게 없으면 화면 이야기 대신 다른 말을 하거나 한마디만 해라."
    ),
}


def block_wheel(widget):
    """슬라이더 위에서 휠을 굴려도 값이 바뀌지 않게 한다.

    CustomTkinter 슬라이더가 휠 이벤트를 가로채서,
    목록을 스크롤하려다 설정값이 바뀌는 일이 생긴다.
    이벤트를 위쪽 스크롤 영역으로 넘기고 슬라이더는 무시하게 한다."""
    def redirect(event):
        node = widget
        while node is not None:
            if isinstance(node, ctk.CTkScrollableFrame):
                try:
                    delta = event.delta
                    if delta:
                        node._parent_canvas.yview_scroll(int(-delta / 120), "units")
                    elif getattr(event, "num", None) == 4:
                        node._parent_canvas.yview_scroll(-1, "units")
                    elif getattr(event, "num", None) == 5:
                        node._parent_canvas.yview_scroll(1, "units")
                except Exception:
                    pass
                break
            node = getattr(node, "master", None)
        return "break"

    targets = [widget] + list(widget.winfo_children())
    for t in targets:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                t.bind(seq, redirect)
            except Exception:
                pass


def deep_merge(base: dict, override: dict) -> dict:
    """저장된 설정에 없는 항목은 기본값으로 채운다.
    버전이 올라가며 항목이 추가돼도 기존 설정 파일이 깨지지 않는다."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return deep_merge(DEFAULT_CFG, json.load(f))
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CFG))


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def target_summary(vision: dict) -> str:
    mode = vision.get("mode", "monitor")
    if mode == "window":
        return f"특정 창 : {vision.get('window_title') or '(지정 안 됨)'}"
    if mode == "region":
        r = vision.get("region")
        if r:
            return f"지정 영역 : {r['left']}, {r['top']}  ({r['width']} x {r['height']})"
        return "지정 영역 : (지정 안 됨)"
    return f"모니터 전체 : {vision.get('monitor', 1)}번"


# =============================================================================
# 영역 지정 창
# =============================================================================

class RegionPicker(ctk.CTkToplevel):
    """화면 전체를 덮는 반투명 창을 띄우고 드래그로 영역을 받는다."""

    def __init__(self, master, on_done):
        super().__init__(master)
        self.on_done = on_done
        self.start = None
        self.rect_id = None

        self.attributes("-fullscreen", True)
        self.attributes("-alpha", 0.35)
        self.attributes("-topmost", True)
        self.configure(fg_color="#000000")

        import tkinter as tk
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            self.winfo_screenwidth() // 2, 60,
            text="캡처할 부분을 드래그하세요    ·    Esc 로 취소",
            fill="white", font=(FONT, 18))

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda _: self._cancel())
        self.focus_force()

    def _press(self, e):
        self.start = (e.x_root, e.y_root)
        self._x0, self._y0 = e.x, e.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline=ACCENT, width=3)

    def _drag(self, e):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self._x0, self._y0, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return self._cancel()

        x0, y0 = self.start
        x1, y1 = e.x_root, e.y_root
        region = {"left": min(x0, x1), "top": min(y0, y1),
                  "width": abs(x1 - x0), "height": abs(y1 - y0)}
        parent = self.master
        self.destroy()

        if region["width"] < 10 or region["height"] < 10:
            messagebox.showwarning("영역이 너무 작습니다",
                                   "다시 지정해 주세요.", parent=parent)
            self.on_done(None)
        else:
            self.on_done(region)

    def _cancel(self):
        self.destroy()
        self.on_done(None)


# =============================================================================
# 무엇을 볼지 + 실시간 미리보기
# =============================================================================

class VisionTargetWindow(ctk.CTkToplevel):
    """캡처 대상을 고르고, 라이카가 실제로 보게 될 그림을 실시간으로 보여준다.

    미리보기는 전송 해상도(max_width)로 한 번 줄인 뒤 다시 키워서 그린다.
    화면에서는 또렷한데 라이카는 못 읽는 상황을 눈으로 확인할 수 있어야 한다."""

    FPS = 10

    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.cfg = cfg
        self.on_save = on_save
        self.alive = True
        self._photo = None

        v = cfg["vision"]
        self.mode = v.get("mode", "monitor")
        self.monitor = int(v.get("monitor", 1) or 1)
        self.win_title = v.get("window_title", "") or ""
        self.region = v.get("region")

        self.title("무엇을 볼지")
        self.geometry("860x660")
        self.configure(fg_color=BG)
        self.transient(master)
        self.after(200, self.lift)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._tick()

    # ------------------------------------------------------------ 화면

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(16, 0))
        ctk.CTkLabel(head, text="무엇을 볼지", font=(FONT, 18, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="아래 미리보기는 라이카에게 실제로 전달되는 그림입니다. "
                 "여기서 글자가 안 읽히면 라이카도 못 읽습니다.",
            font=(FONT, 11), text_color=MUTED, anchor="w",
            justify="left", wraplength=810).pack(fill="x", pady=(2, 0))

        seg = ctk.CTkSegmentedButton(
            self, values=list(MODE_LABEL.values()),
            fg_color=PANEL_SOFT, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOV, unselected_color=PANEL_SOFT,
            unselected_hover_color="#31314a", font=(FONT, 12),
            command=self._on_mode)
        seg.pack(fill="x", padx=16, pady=12)
        seg.set(MODE_LABEL[self.mode])

        self.opt_box = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        self.opt_box.pack(fill="x", padx=16)

        prev = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        prev.pack(fill="both", expand=True, padx=16, pady=12)

        import tkinter as tk
        self.canvas = tk.Canvas(prev, bg="#0d0d14", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.info = ctk.CTkLabel(prev, text="", font=(FONT, 11),
                                 text_color=MUTED, anchor="w")
        self.info.pack(fill="x", padx=12, pady=(0, 10))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="취소", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self._close).pack(side="right")
        ctk.CTkButton(bar, text="저장", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOV, font=(FONT, 13, "bold"),
                      command=self._save).pack(side="right", padx=8)

        self._render_options()

    def _on_mode(self, label):
        self.mode = LABEL_MODE[label]
        self._render_options()

    def _render_options(self):
        for w in self.opt_box.winfo_children():
            w.destroy()

        inner = ctk.CTkFrame(self.opt_box, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=12)

        if self.mode == "monitor":
            try:
                import mss
                with mss.mss() as sct:
                    count = max(1, len(sct.monitors) - 1)
            except Exception:
                count = 1
            values = [str(i) for i in range(1, count + 1)]
            cur = str(self.monitor) if str(self.monitor) in values else "1"
            self.mon_var = ctk.StringVar(value=cur)

            ctk.CTkLabel(inner, text="모니터", font=(FONT, 12),
                         text_color=TEXT).pack(side="left")
            ctk.CTkOptionMenu(inner, variable=self.mon_var, values=values,
                              width=90, height=32, fg_color=PANEL_SOFT,
                              button_color=PANEL_SOFT, button_hover_color="#31314a",
                              font=(FONT, 12), text_color=TEXT).pack(side="left", padx=(10, 0))
            ctk.CTkLabel(inner,
                         text="모니터 전체는 축소되면서 글자가 뭉개집니다. "
                              "창이나 영역으로 좁히는 쪽이 정확합니다.",
                         font=(FONT, 10), text_color=WARN,
                         anchor="w").pack(side="left", padx=(16, 0))

        elif self.mode == "window":
            import laika_capture
            wins = laika_capture.list_windows()
            titles = [w["title"] for w in wins] or ["(창을 찾을 수 없습니다)"]
            cur = self.win_title if self.win_title in titles else titles[0]
            self.win_var = ctk.StringVar(value=cur)

            ctk.CTkLabel(inner, text="캡처할 창", font=(FONT, 12),
                         text_color=TEXT, anchor="w").pack(fill="x")
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(4, 0))
            ctk.CTkOptionMenu(row, variable=self.win_var, values=titles,
                              height=32, fg_color=PANEL_SOFT, button_color=PANEL_SOFT,
                              button_hover_color="#31314a", font=(FONT, 11),
                              text_color=TEXT, dynamic_resizing=False
                              ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="새로고침", width=80, height=32,
                          fg_color=PANEL_SOFT, hover_color="#31314a",
                          font=(FONT, 11),
                          command=self._render_options).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(inner,
                         text="창을 옮기거나 크기를 바꿔도 따라갑니다. "
                              "창을 껐다 켜면 제목으로 다시 찾습니다.",
                         font=(FONT, 10), text_color=MUTED,
                         anchor="w").pack(fill="x", pady=(4, 0))

        else:
            r = self.region
            txt = (f"{r['left']}, {r['top']}   ({r['width']} x {r['height']})"
                   if r else "영역이 지정되지 않았습니다.")
            self.region_label = ctk.CTkLabel(inner, text=txt, font=(FONT, 12),
                                             text_color=TEXT if r else MUTED)
            self.region_label.pack(side="left")
            ctk.CTkButton(inner, text="영역 지정", width=100, height=32,
                          fg_color=ACCENT, hover_color=ACCENT_HOV,
                          font=(FONT, 12, "bold"),
                          command=self._pick_region).pack(side="right")

    def _pick_region(self):
        def done(region):
            if region:
                self.region = region
                self.region_label.configure(
                    text=f"{region['left']}, {region['top']}   "
                         f"({region['width']} x {region['height']})",
                    text_color=TEXT)
        RegionPicker(self, done)

    # ------------------------------------------------------------ 미리보기

    def _preview_cfg(self):
        v = dict(self.cfg["vision"])
        v["mode"] = self.mode
        if self.mode == "monitor" and hasattr(self, "mon_var"):
            v["monitor"] = int(self.mon_var.get())
        elif self.mode == "window" and hasattr(self, "win_var"):
            v["window_title"] = self.win_var.get()
            v["window_hwnd"] = None          # 제목으로 매번 다시 찾는다
        elif self.mode == "region":
            v["region"] = self.region
        return v

    def _tick(self):
        if not self.alive:
            return
        try:
            self._draw()
        except Exception as e:
            self._message(f"미리보기 오류: {type(e).__name__}: {e}")
        self.after(int(1000 / self.FPS), self._tick)

    def _draw(self):
        import mss
        import laika_capture
        from PIL import Image, ImageTk

        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        if cw < 50 or ch < 50:
            return

        v = self._preview_cfg()
        with mss.mss() as sct:
            area, desc = laika_capture.resolve_area(v, sct)
            if area is None:
                self._message(desc)
                return
            shot = sct.grab(area)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

        src_w, src_h = img.size

        # 라이카가 실제로 받는 해상도로 한 번 줄인다.
        # 이 단계에서 사라진 정보는 라이카도 볼 수 없다.
        max_w = int(self.cfg["vision"].get("max_width", 1024))
        if src_w > max_w:
            sent = img.resize((max_w, max(1, int(src_h * max_w / src_w))),
                              Image.LANCZOS)
        else:
            sent = img
        sw, sh = sent.size

        # 줄인 그림을 캔버스에 맞춰 다시 키운다 (뭉개짐이 그대로 보이도록)
        ratio = min(cw / sw, ch / sh)
        view = sent.resize((max(1, int(sw * ratio)), max(1, int(sh * ratio))),
                           Image.NEAREST)

        self._photo = ImageTk.PhotoImage(view)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)
        self.info.configure(
            text=f"{desc}    ·    원본 {src_w}x{src_h}    ·    전송 {sw}x{sh}",
            text_color=MUTED)

    def _message(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(
            max(self.canvas.winfo_width(), 1) // 2,
            max(self.canvas.winfo_height(), 1) // 2,
            text=text, fill=MUTED, font=(FONT, 13), width=600)
        self.info.configure(text="")

    # ------------------------------------------------------------ 저장

    def _save(self):
        v = self.cfg["vision"]
        v["mode"] = self.mode

        if self.mode == "monitor" and hasattr(self, "mon_var"):
            v["monitor"] = int(self.mon_var.get())
        elif self.mode == "window" and hasattr(self, "win_var"):
            import laika_capture
            title = self.win_var.get()
            v["window_title"] = title
            found = laika_capture.find_window_by_title(title)
            v["window_hwnd"] = found["hwnd"] if found else None
        elif self.mode == "region":
            v["region"] = self.region

        self.alive = False
        self.on_save(self.cfg)
        self.destroy()

    def _close(self):
        self.alive = False
        self.destroy()


# =============================================================================
# 준비물 점검 창
# =============================================================================

class SetupWindow(ctk.CTkToplevel):
    """필요한 것들이 갖춰졌는지 보여주고, 없는 것은 받을 곳을 열어준다.

    GPT-SoVITS 는 프로그램 폴더 아래에서 찾아내면 경로를 자동으로 넣는다.
    다운로드와 압축 해제는 사용자가 한다."""

    def __init__(self, master, cfg, on_apply_path, on_open_refmaker):
        super().__init__(master)
        self.cfg = cfg
        self.on_apply_path = on_apply_path
        self.on_open_refmaker = on_open_refmaker

        self.title("준비물 점검")
        self.geometry("760x860")
        self.configure(fg_color=BG)
        self.transient(master)
        self.after(200, self.lift)

        import laika_setup
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(16, 0))
        ctk.CTkLabel(head, text="준비물 점검", font=(FONT, 18, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text=f"내려받은 것들은 아래 폴더 안에 두면 자동으로 찾습니다.\n"
                 f"{laika_setup.program_dir()}",
            font=(FONT, 11), text_color=MUTED, anchor="w",
            justify="left").pack(fill="x", pady=(2, 0))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        # 표정 점검. laika_vts 의 조회는 asyncio 다.
        # Tk 메인 스레드에서 그대로 돌리면 창이 멈추므로 별도 스레드에서 돌리고,
        # 결과는 큐에 넣어 Tk 타이머에서만 위젯에 반영한다.
        self.expr_queue = queue.Queue()
        self._build_expression()
        self.after(150, self._expr_pump)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="닫기", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(bar, text="다시 검사", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOV, font=(FONT, 13, "bold"),
                      command=self.refresh).pack(side="right", padx=8)

        self.refresh()
        self.refresh_expressions()

    def refresh(self):
        import laika_setup
        for w in self.list_frame.winfo_children():
            w.destroy()
        for item in laika_setup.check_all(self.cfg):
            self._card(item)

    # -------------------------------------------------------- 표정 점검

    EXPR_COLS = (("이름", 150), ("종류", 140), ("화면버튼", 70),
                 ("현재", 56), ("매핑된 감정", 130))

    def _build_expression(self):
        box = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        box.pack(fill="x", padx=12, pady=(0, 6))

        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(top, text="표정 점검", font=(FONT, 14, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(top, text="새로고침", width=84, height=28,
                      fg_color=ACCENT, hover_color=ACCENT_HOV, font=(FONT, 12),
                      command=self.refresh_expressions).pack(side="right")
        ctk.CTkButton(top, text="전부 끄기", width=84, height=28,
                      fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                      command=self._expr_all_off).pack(side="right", padx=(0, 6))

        self.expr_state = ctk.CTkLabel(
            box, text="VTS 에 조회하는 중...", font=(FONT, 11),
            text_color=MUTED, anchor="w", justify="left", wraplength=690)
        self.expr_state.pack(fill="x", padx=14, pady=(4, 0))

        # 사과 표정 안내. 모델에 사과 표정이 없으면 여기서 알려 준다.
        self.expr_note = ctk.CTkLabel(
            box, text="", font=(FONT, 11), text_color=MUTED,
            anchor="w", justify="left", wraplength=690)
        self.expr_note.pack(fill="x", padx=14, pady=(2, 0))

        self.expr_rows = ctk.CTkScrollableFrame(box, height=210,
                                                fg_color="transparent")
        self.expr_rows.pack(fill="x", padx=8, pady=(6, 12))

    def _expr_pump(self):
        """워커 스레드가 큐에 넣은 것을 Tk 메인 스레드에서만 반영한다."""
        try:
            while True:
                kind, payload = self.expr_queue.get_nowait()
                if kind == "rows":
                    self._expr_render(*payload)
                elif kind == "state":
                    text, color = payload
                    self.expr_state.configure(text=text, text_color=color)
                elif kind == "refresh":
                    self.after(300, self.refresh_expressions)
        except queue.Empty:
            pass
        except Exception:
            return            # 창이 닫힌 뒤. 더 돌 필요 없다.
        try:
            self.after(150, self._expr_pump)
        except Exception:
            pass

    def _expr_say(self, text, color=MUTED):
        self.expr_queue.put(("state", (text, color)))

    def _vts_cfg(self):
        """vts_config.json 을 읽고 config.json 의 vts 항목으로 덮어쓴다."""
        from laika_vts import load_config
        vcfg = load_config()
        vcfg.update({k: v for k, v in (self.cfg.get("vts") or {}).items()
                     if k in vcfg})
        return vcfg

    def _expr_thread(self, fn):
        def work():
            try:
                fn()
            except Exception as e:
                self._expr_say(f"실패: {type(e).__name__}: {e}", WARN)
        threading.Thread(target=work, daemon=True).start()

    def _core_expression(self):
        """방송 중이면 코어의 표정 제어기를 돌려준다.

        방송 중에 점검 창이 따로 연결해 핫키를 누르면 코어가 추적하는
        '켜진 표정' 과 어긋나, 다음 전환의 [끄기]->[켜기] 가 반대로 동작한다."""
        core = getattr(self.master, "core", None)
        if core is not None and getattr(core, "running", False):
            exp = getattr(core, "expression", None)
            if exp is not None and getattr(exp, "connected", False):
                return exp
        return None

    def refresh_expressions(self):
        self._expr_say("VTS 에 조회하는 중...")

        def work():
            import asyncio
            from laika_vts import VTSClient

            async def job():
                c = VTSClient(self._vts_cfg())
                await c.connect()
                try:
                    return await c.hotkeys(), await c.expressions()
                finally:
                    await c.close()

            hotkeys, exprs = asyncio.run(job())
            self.expr_queue.put(("rows", (hotkeys, exprs)))

        self._expr_thread(work)

    def _expr_render(self, hotkeys, exprs):
        for w in self.expr_rows.winfo_children():
            w.destroy()

        # 핫키 이름 -> 감정 이름들. 여러 감정이 한 핫키를 쓸 수 있다 (화남 / 못마땅)
        try:
            from laika_emotion import EmotionMap
            emap = EmotionMap(self.cfg).data.get("map") or {}
        except Exception:
            emap = {}
        by_hotkey = {}
        for emo, hk in emap.items():
            if hk:
                by_hotkey.setdefault(hk, []).append(emo)

        # 표정 파일 이름과 핫키 이름은 다르므로 file 로 맞춘다
        on_files = {e.get("file") for e in exprs if e.get("active")}

        head = ctk.CTkFrame(self.expr_rows, fg_color="transparent")
        head.pack(fill="x", pady=(0, 2))
        for title, w in self.EXPR_COLS:
            ctk.CTkLabel(head, text=title, width=w, anchor="w",
                         font=(FONT, 11), text_color=MUTED).pack(side="left")
        ctk.CTkLabel(head, text="", width=70).pack(side="left")

        on_count = 0
        for h in hotkeys:
            name = h.get("name", "")
            btn = h.get("onScreenButtonID", -1)
            on = h.get("file") in on_files
            on_count += 1 if on else 0

            row = ctk.CTkFrame(self.expr_rows, fg_color="transparent")
            row.pack(fill="x", pady=1)

            # 이름이 빈 핫키가 실제로 6개 있다(vts_hotkeys.json 실측).
            # 이름으로는 부를 수 없으므로 그때는 hotkeyID 로 부른다.
            # 매핑 파일은 이름 기준이라, 이름이 있으면 이름을 그대로 쓴다.
            key = name or h.get("hotkeyID", "")

            # 이름 끝의 공백은 VTS 에 등록된 실제 이름이다. 지우면 못 찾는다.
            disp = (name + (" \u2423" if name != name.rstrip() else "")
                    if name else f"(이름 없음) {key[:8]}")
            vals = (disp,
                    h.get("type", ""),
                    "없음" if btn in (-1, None) else str(btn),
                    "켜짐" if on else "꺼짐",
                    ", ".join(by_hotkey.get(name, [])) or "-")
            for (title, w), v in zip(self.EXPR_COLS, vals):
                ctk.CTkLabel(row, text=v, width=w, anchor="w", font=(FONT, 11),
                             text_color=OK if (title == "현재" and on) else TEXT
                             ).pack(side="left")
            ctk.CTkButton(row, text="켜보기", width=70, height=24,
                          fg_color=PANEL_SOFT, hover_color="#31314a",
                          font=(FONT, 11),
                          command=lambda k=key: self._expr_toggle(k)
                          ).pack(side="left")

        # 사과 표정이 준비됐는지 본다.
        # 서버가 멈춰 껐다 켠 뒤 라이카가 사과 한마디를 할 때 쓰는 표정이다.
        apo = ((self.cfg.get("tts") or {}).get("apology_emotion") or "슬픔")
        hk_names = {h.get("name", "") for h in hotkeys}
        mapped = emap.get(apo)
        if mapped and mapped in hk_names:
            self.expr_note.configure(
                text=f"사과 표정: '{apo}' -> 핫키 '{mapped}' 로 연결돼 있습니다.",
                text_color=MUTED)
        else:
            self.expr_note.configure(
                text=("사과 표정이 없습니다. TTS 서버를 껐다 켠 뒤 라이카가 사과할 때 쓸 표정입니다.\n"
                      "VTube Studio 에서 사과 표정을 만들고 핫키를 추가한 다음, "
                      "vts_emotion_map.json 의 "
                      f"'{apo}' 항목에 그 핫키 이름을 적어 주세요."),
                text_color=DANGER)

        note = ("방송 중입니다. 여기서 누른 표정은 라이카가 추적하는 상태에 함께 반영됩니다."
                if self._core_expression() is not None else
                "방송이 꺼져 있어 점검용으로 따로 연결해서 누릅니다.")
        self._expr_say(
            f"핫키 {len(hotkeys)}개 / 켜져 있는 표정 {on_count}개. {note}", MUTED)

    def _expr_toggle(self, name):
        exp = self._core_expression()
        if exp is not None:
            ok, msg = exp.press_hotkey(name)
            self._expr_say(msg, MUTED if ok else WARN)
            self.after(500, self.refresh_expressions)
            return

        self._expr_say(f"'{name}' 누르는 중...")

        def work():
            import asyncio
            from laika_vts import VTSClient

            async def job():
                c = VTSClient(self._vts_cfg())
                await c.connect()
                try:
                    await c._send("HotkeyTriggerRequest", {"hotkeyID": name})
                finally:
                    await c.close()

            asyncio.run(job())
            self.expr_queue.put(("state", (f"'{name}' 를 눌렀습니다.", MUTED)))
            self.expr_queue.put(("refresh", None))

        self._expr_thread(work)

    def _expr_all_off(self):
        exp = self._core_expression()
        if exp is not None:
            ok, msg = exp.all_off()
            self._expr_say(msg, MUTED if ok else WARN)
            self.after(500, self.refresh_expressions)
            return

        self._expr_say("켜져 있는 표정을 끄는 중...")

        def work():
            import asyncio
            from laika_vts import VTSClient

            async def job():
                c = VTSClient(self._vts_cfg())
                await c.connect()
                try:
                    exprs = await c.expressions()
                    hotkeys = await c.hotkeys()
                    by_file = {h.get("file"): (h.get("name")
                                               or h.get("hotkeyID", ""))
                               for h in hotkeys}
                    done, orphan = [], []
                    for e in exprs:
                        if not e.get("active"):
                            continue
                        nm = by_file.get(e.get("file"))
                        if not nm:
                            # 핫키에 연결되지 않은 표정은 여기서 끌 방법이 없다
                            orphan.append(e.get("file"))
                            continue
                        await c._send("HotkeyTriggerRequest", {"hotkeyID": nm})
                        done.append(nm)
                    return done, orphan
                finally:
                    await c.close()

            done, orphan = asyncio.run(job())
            msg = f"{len(done)}개를 껐습니다: {', '.join(done) or '없음'}"
            if orphan:
                msg += f" / 핫키에 없는 표정은 VTS 에서 직접 꺼주세요: {orphan}"
            self.expr_queue.put(("state", (msg, WARN if orphan else MUTED)))
            self.expr_queue.put(("refresh", None))

        self._expr_thread(work)

    def _card(self, item):
        card = ctk.CTkFrame(self.list_frame, fg_color=PANEL, corner_radius=10)
        card.pack(fill="x", pady=5, padx=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(top, text="●", font=(FONT, 14),
                     text_color=OK if item["ok"] else "#6a6a85").pack(side="left")
        ctk.CTkLabel(top, text=item["name"], font=(FONT, 14, "bold"),
                     text_color=TEXT).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(top, text=item["detail"], font=(FONT, 11),
                     text_color=OK if item["ok"] else MUTED).pack(side="right")

        if item["hint"]:
            ctk.CTkLabel(card, text=item["hint"], font=(FONT, 11),
                         text_color=MUTED, anchor="w", justify="left",
                         wraplength=640).pack(fill="x", padx=14, pady=(4, 0))

        action = item["action"]
        if action == "none":
            ctk.CTkFrame(card, height=8, fg_color="transparent").pack()
            return

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 12))

        if action == "auto":
            ctk.CTkButton(row, text="이 경로 적용", width=120, height=32,
                          fg_color=ACCENT, hover_color=ACCENT_HOV,
                          font=(FONT, 12, "bold"),
                          command=lambda p=item["found"]: self._apply(p)).pack(side="left")
            ctk.CTkButton(row, text="받는 곳 열기", width=110, height=32,
                          fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                          command=lambda u=item["link"]: self._open(u)).pack(side="left", padx=(6, 0))
        elif action == "link":
            ctk.CTkButton(row, text="받는 곳 열기", width=120, height=32,
                          fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                          command=lambda u=item["link"]: self._open(u)).pack(side="left")
        elif action == "refmaker":
            ctk.CTkButton(row, text="참조 음성 만들기", width=140, height=32,
                          fg_color=ACCENT, hover_color=ACCENT_HOV,
                          font=(FONT, 12, "bold"),
                          command=self._refmaker).pack(side="left")

    def _apply(self, path):
        self.on_apply_path("sovits_dir", path)
        self.refresh()

    def _refmaker(self):
        self.on_open_refmaker()
        self.destroy()

    @staticmethod
    def _open(url):
        import webbrowser
        webbrowser.open(url)


# =============================================================================
# 참조 음성 만들기 창
# =============================================================================

class RefMakerWindow(ctk.CTkToplevel):
    """긴 녹음에서 참조로 쓸 3~10초 구간을 찾아 잘라낸다.

    무거운 작업(모델 로딩, 전사)은 별도 스레드에서 돌리고
    결과는 큐를 통해 받는다. Tk 위젯은 이 창에서만 만진다."""

    def __init__(self, master, model_size, device, on_apply):
        super().__init__(master)
        self.model_size = model_size
        self.device = device
        self.on_apply = on_apply

        self.model = None
        self.src_path = None
        self.candidates = []
        self.events = queue.Queue()

        self.title("참조 음성 만들기")
        self.geometry("760x600")
        self.configure(fg_color=BG)
        self.transient(master)
        self.after(200, self.lift)

        self._build()
        self.after(80, self._drain)

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(16, 0))
        ctk.CTkLabel(head, text="참조 음성 만들기", font=(FONT, 18, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="말이 또렷하고 잡음·웃음·한숨이 없는 구간을 고르세요. "
                 "감정 톤이 그대로 따라오므로 방송에서 쓸 톤과 비슷한 구간이 좋습니다.",
            font=(FONT, 11), text_color=MUTED, anchor="w", justify="left",
            wraplength=700).pack(fill="x", pady=(2, 0))

        pick = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        pick.pack(fill="x", padx=16, pady=12)

        row = ctk.CTkFrame(pick, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=12)
        self.path_var = ctk.StringVar(value="")
        ctk.CTkEntry(row, textvariable=self.path_var, height=34,
                     fg_color=PANEL_SOFT, border_color="#3a3a52",
                     font=(FONT, 12), text_color=TEXT,
                     placeholder_text="분석할 음성 파일 (몇 분짜리 긴 녹음도 괜찮습니다)"
                     ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="파일 선택", width=90, height=34,
                      fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 12), command=self._choose).pack(side="left", padx=(6, 0))
        self.btn_scan = ctk.CTkButton(row, text="분석", width=80, height=34,
                                      fg_color=ACCENT, hover_color=ACCENT_HOV,
                                      font=(FONT, 12, "bold"), command=self._scan)
        self.btn_scan.pack(side="left", padx=(6, 0))

        self.progress = ctk.CTkProgressBar(pick, height=6, progress_color=ACCENT,
                                           fg_color=PANEL_SOFT)
        self.progress.pack(fill="x", padx=12, pady=(0, 8))
        self.progress.set(0)
        self.status = ctk.CTkLabel(pick, text="파일을 고르고 분석을 누르세요.",
                                   font=(FONT, 11), text_color=MUTED, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 10))

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=PANEL, corner_radius=10, label_text="찾은 구간",
            label_font=(FONT, 12, "bold"), label_text_color=MUTED)
        self.list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="닫기", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self.destroy).pack(side="right")

    def _choose(self):
        p = filedialog.askopenfilename(
            title="분석할 음성 파일",
            filetypes=[("오디오", "*.wav *.mp3 *.flac *.m4a"), ("모든 파일", "*.*")])
        if p:
            self.path_var.set(p.replace("/", "\\") if sys.platform == "win32" else p)

    def _scan(self):
        path = self.path_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning("파일 없음", "분석할 음성 파일을 먼저 고르세요.",
                                   parent=self)
            return

        self.src_path = path
        self.btn_scan.configure(state="disabled", text="분석 중")
        self.progress.set(0)
        for w in self.list_frame.winfo_children():
            w.destroy()
        threading.Thread(target=self._scan_worker, args=(path,), daemon=True).start()

    def _scan_worker(self, path):
        try:
            import laika_refmaker as rm

            if self.model is None:
                self.events.put(("status",
                                 f"음성 인식 모델을 불러오는 중입니다 ({self.model_size})..."))
                from faster_whisper import WhisperModel
                try:
                    self.model = WhisperModel(
                        self.model_size, device=self.device,
                        compute_type="float16" if self.device == "cuda" else "int8")
                except Exception:
                    self.events.put(("status",
                                     "그래픽카드를 쓸 수 없어 CPU 로 분석합니다. 시간이 더 걸립니다."))
                    self.model = WhisperModel(self.model_size, device="cpu",
                                              compute_type="int8")

            self.events.put(("status", "음성을 분석하는 중입니다..."))
            found = rm.find_candidates(
                self.model, path,
                on_progress=lambda p: self.events.put(("progress", p)))
            self.events.put(("done", found))
        except Exception as e:
            self.events.put(("error", f"{type(e).__name__}: {e}"))

    def _drain(self):
        try:
            while True:
                kind, val = self.events.get_nowait()
                if kind == "status":
                    self.status.configure(text=val, text_color=MUTED)
                elif kind == "progress":
                    self.progress.set(val)
                elif kind == "done":
                    self._show_results(val)
                elif kind == "saved":
                    path, text, dur = val
                    self.on_apply(path, text)
                    messagebox.showinfo(
                        "완료",
                        f"참조 음성을 저장했습니다.\n\n길이: {dur:.1f}초\n대사: {text}",
                        parent=self)
                    self.destroy()
                    return
                elif kind == "error":
                    self.status.configure(text=f"실패: {val}", text_color=COLORS["error"])
                    self.btn_scan.configure(state="normal", text="분석")
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _show_results(self, found):
        self.candidates = found
        self.btn_scan.configure(state="normal", text="분석")
        self.progress.set(1)

        if not found:
            self.status.configure(
                text="쓸 만한 구간을 찾지 못했습니다. "
                     "더 길거나 또렷하게 말하는 녹음을 넣어보세요.",
                text_color=COLORS["error"])
            return

        self.status.configure(text=f"{len(found)}개 구간을 찾았습니다. 하나를 고르세요.",
                              text_color=OK)

        for c in found:
            card = ctk.CTkFrame(self.list_frame, fg_color=PANEL_SOFT, corner_radius=8)
            card.pack(fill="x", pady=4, padx=4)

            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkLabel(left,
                         text=f"{int(c['start'] // 60)}분 {int(c['start'] % 60)}초 "
                              f"· {c['dur']:.1f}초",
                         font=(FONT, 11), text_color=ACCENT, anchor="w").pack(fill="x")
            ctk.CTkLabel(left, text=c["text"], font=(FONT, 12), text_color=TEXT,
                         anchor="w", justify="left", wraplength=520).pack(fill="x")

            ctk.CTkButton(card, text="듣기", width=56, height=30,
                          fg_color="transparent", hover_color="#31314a",
                          text_color=MUTED, font=(FONT, 11),
                          command=lambda x=c: self._preview(x)).pack(side="left", padx=(0, 4))
            ctk.CTkButton(card, text="사용", width=64, height=30,
                          fg_color=ACCENT, hover_color=ACCENT_HOV,
                          font=(FONT, 12, "bold"),
                          command=lambda x=c: self._use(x)).pack(side="left", padx=(0, 12))

    def _preview(self, c):
        def run():
            try:
                import soundfile as sf
                import sounddevice as sd
                data, sr = sf.read(self.src_path)
                if data.ndim > 1:
                    data = data.mean(axis=1)
                sd.play(data[int(c["start"] * sr): int(c["end"] * sr)], sr)
            except Exception as e:
                self.events.put(("error", f"재생 실패: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def _use(self, c):
        out = Path(self.src_path).with_name(
            f"{Path(self.src_path).stem}_ref_{int(c['start'])}.wav")
        self.status.configure(text="구간을 잘라 저장하는 중입니다...", text_color=MUTED)

        def run():
            try:
                import laika_refmaker as rm
                dur, text = rm.extract(self.model, self.src_path,
                                       c["start"], c["end"], out)
                self.events.put(("saved", (str(out), text, dur)))
            except Exception as e:
                self.events.put(("error", f"{type(e).__name__}: {e}"))
        threading.Thread(target=run, daemon=True).start()


# =============================================================================
# 설정 창
# =============================================================================

class SettingsWindow(ctk.CTkToplevel):

    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.cfg = json.loads(json.dumps(cfg))
        self.on_save = on_save
        self.vars = {}

        self.title("설정")
        self.geometry("720x680")
        self.configure(fg_color=BG)
        self.transient(master)
        self.after(200, self.lift)

        tabs = ctk.CTkTabview(
            self, fg_color=PANEL, segmented_button_fg_color=PANEL_SOFT,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOV,
            text_color=TEXT, corner_radius=10)
        tabs.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        for name in ("간단", "연결", "음성", "인식 · 화면", "성격", "고급"):
            tabs.add(name)

        self._tab_simple(tabs.tab("간단"))
        tabs.set("간단")
        self._tab_connection(tabs.tab("연결"))
        self._tab_voice(tabs.tab("음성"))
        self._tab_stt(tabs.tab("인식 · 화면"))
        self._tab_persona(tabs.tab("성격"))
        self._tab_advanced(tabs.tab("고급"))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="취소", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(bar, text="저장", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOV, font=(FONT, 13, "bold"),
                      command=self._save).pack(side="right", padx=8)

    # ------------------------------------------------------------ 위젯 헬퍼

    def _get(self, dotted):
        cur = self.cfg
        for part in dotted.split("."):
            cur = cur[part]
        return cur

    def _set(self, dotted, value):
        parts = dotted.split(".")
        cur = self.cfg
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = value

    def _row(self, parent, label, hint=None):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", pady=(10, 0), padx=4)
        ctk.CTkLabel(wrap, text=label, font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x")
        if hint:
            ctk.CTkLabel(wrap, text=hint, font=(FONT, 11), text_color=MUTED,
                         anchor="w", justify="left", wraplength=620).pack(fill="x")
        return wrap

    def _entry(self, parent, key, label, hint=None, show=None):
        wrap = self._row(parent, label, hint)
        # 같은 key 를 두 탭에서 쓰면 변수를 공유한다.
        # 따로 만들면 나중에 만든 쪽만 저장되고 앞 탭에서 고친 값이 사라진다.
        var = self.vars.get(key)
        if var is None:
            var = ctk.StringVar(value=str(self._get(key) or ""))
            self.vars[key] = var
        ctk.CTkEntry(wrap, textvariable=var, height=34, show=show,
                     fg_color=PANEL_SOFT, border_color="#3a3a52",
                     font=(FONT, 12), text_color=TEXT).pack(fill="x", pady=(4, 0))
        return var

    def _picker(self, parent, key, label, hint, mode, filetypes=None):
        wrap = self._row(parent, label, hint)
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))

        # 같은 key 를 두 탭에서 쓰면 변수를 공유한다.
        # 따로 만들면 나중에 만든 쪽만 저장되고 앞 탭에서 고친 값이 사라진다.
        var = self.vars.get(key)
        if var is None:
            var = ctk.StringVar(value=str(self._get(key) or ""))
            self.vars[key] = var
        ctk.CTkEntry(row, textvariable=var, height=34, fg_color=PANEL_SOFT,
                     border_color="#3a3a52", font=(FONT, 12),
                     text_color=TEXT).pack(side="left", fill="x", expand=True)

        def browse():
            if mode == "dir":
                p = filedialog.askdirectory(title=label)
            else:
                p = filedialog.askopenfilename(title=label, filetypes=filetypes or [])
            if p:
                var.set(p.replace("/", "\\") if sys.platform == "win32" else p)

        ctk.CTkButton(row, text="찾기", width=64, height=34, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 12),
                      command=browse).pack(side="left", padx=(6, 0))
        return var

    def _slider(self, parent, key, label, hint, lo, hi, steps=None, fmt="{:.2f}"):
        wrap = self._row(parent, label, hint)
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))

        val = float(self._get(key))
        # 같은 key 를 두 탭에서 쓰면 변수를 공유한다.
        # 따로 만들면 나중에 만든 쪽만 저장되고 앞 탭에서 고친 값이 사라진다.
        var = self.vars.get(key)
        if var is None:
            var = ctk.DoubleVar(value=val)
            self.vars[key] = var
        show = ctk.CTkLabel(row, text=fmt.format(val), width=56,
                            font=(FONT, 12), text_color=ACCENT)
        show.pack(side="right")

        slider = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=steps,
                               variable=var, button_color=ACCENT,
                               button_hover_color=ACCENT_HOV, progress_color=ACCENT,
                               command=lambda v: show.configure(text=fmt.format(v)))
        slider.pack(side="left", fill="x", expand=True)
        block_wheel(slider)          # 스크롤하다 값이 바뀌는 것을 막는다
        return var

    def _option(self, parent, key, label, hint, values):
        wrap = self._row(parent, label, hint)
        # 같은 key 를 두 탭에서 쓰면 변수를 공유한다.
        # 따로 만들면 나중에 만든 쪽만 저장되고 앞 탭에서 고친 값이 사라진다.
        var = self.vars.get(key)
        if var is None:
            var = ctk.StringVar(value=str(self._get(key)))
            self.vars[key] = var
        ctk.CTkOptionMenu(wrap, variable=var, values=values, height=34,
                          fg_color=PANEL_SOFT, button_color=PANEL_SOFT,
                          button_hover_color="#31314a", font=(FONT, 12),
                          text_color=TEXT).pack(fill="x", pady=(4, 0))
        return var

    def _switch(self, parent, key, label, hint=None):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", pady=(12, 0), padx=4)
        # 같은 key 를 두 탭에서 쓰면 변수를 공유한다.
        # 따로 만들면 나중에 만든 쪽만 저장되고 앞 탭에서 고친 값이 사라진다.
        var = self.vars.get(key)
        if var is None:
            var = ctk.BooleanVar(value=bool(self._get(key)))
            self.vars[key] = var
        ctk.CTkSwitch(wrap, text=label, variable=var, font=(FONT, 13),
                      progress_color=ACCENT, text_color=TEXT).pack(anchor="w")
        if hint:
            ctk.CTkLabel(wrap, text=hint, font=(FONT, 11), text_color=MUTED,
                         anchor="w").pack(fill="x")
        return var

    # ------------------------------------------------------------ 탭

    def _tab_simple(self, tab):
        """자주 건드리는 것만 모은 탭.

        나머지 탭을 지우지 않는다. 같은 값을 가리키므로
        여기서 바꾸면 해당 탭에서도 같은 값이 보인다."""
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text="자주 쓰는 설정만 모았습니다. 나머지는 옆 탭에 그대로 있습니다.",
            font=(FONT, 11), text_color=MUTED, anchor="w",
            justify="left", wraplength=620).pack(fill="x", padx=4, pady=(6, 0))

        self._section(f, "방송 시작 전에 보는 것")
        self._entry(f, "api_key", "Anthropic API 키",
                    "비어 있으면 라이카가 말을 못 합니다.", show="●")
        self._entry(f, "chzzk_channel_id", "치지직 채널 ID",
                    "비우면 채팅을 읽지 않습니다.")

        self._section(f, "말")
        self._entry(f, "max_tokens", "한 번에 쓰는 글자 수 한도",
                    "권장 400~600. 작으면 말이 중간에 끊깁니다.")
        self._switch(f, "tts.streaming_mode", "말을 빨리 시작하기",
                     "켜면 합성이 끝나기 전에 앞부분부터 내보냅니다. "
                     "첫 소리까지 4.0초에서 1.5초로 줄었습니다(09-18 측정).")

        self._section(f, "듣기")
        self._slider(f, "stt.min_rms", "마이크 문턱값",
                     "이보다 작은 소리는 무시합니다. 0.02 를 권장합니다. "
                     "녹음 162건에서 잡음 6건을 전부 걸렀고 정상 목소리는 하나도 "
                     "안 잘렸습니다.",
                     0.0, 0.06, fmt="{:.3f}")
        self._switch(f, "audio.enable_monitor", "내 스피커로도 듣기",
                     "끄면 방송에만 나가고 내 귀에는 안 들립니다.")

        self._section(f, "표정")
        self._switch(f, "emotion.enabled", "표정 사용",
                     "VTube Studio 가 켜져 있어야 합니다.")

    def _tab_connection(self, tab):
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        self._entry(f, "api_key", "Anthropic API 키",
                    "console.anthropic.com 에서 발급합니다. 이 값은 config.json 에 저장되니 "
                    "파일을 공유하지 마세요.", show="●")
        self._entry(f, "model", "모델", "기본값을 그대로 두면 됩니다.")
        self._entry(f, "chzzk_channel_id", "치지직 채널 ID",
                    "채널 주소 chzzk.naver.com/ 뒤에 오는 문자열. "
                    "비우면 채팅 연동을 하지 않습니다.")
        self._picker(f, "sovits_dir", "GPT-SoVITS 폴더",
                     "api_v2.py 와 runtime 폴더가 들어 있는 폴더를 고르세요.", "dir")
        self._entry(f, "sovits_url", "TTS 서버 주소", "기본값을 그대로 두면 됩니다.")

    def _tab_voice(self, tab):
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        self._picker(f, "ref_audio", "참조 음성",
                     "3~10초 길이의 깨끗한 음성 파일. 이 목소리를 흉내 냅니다.",
                     "file", [("오디오", "*.wav *.mp3 *.flac"), ("모든 파일", "*.*")])
        self._entry(f, "ref_text", "참조 음성의 대사",
                    "위 파일이 실제로 말하는 문장을 정확히 적으세요. 틀리면 발음이 뭉개집니다.")

        ctk.CTkButton(f, text="긴 녹음에서 참조 음성 만들기", height=36,
                      corner_radius=10, fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 12), command=self._open_refmaker
                      ).pack(fill="x", padx=4, pady=(10, 0))
        ctk.CTkLabel(f, text="긴 음성 파일을 넣으면 쓸 만한 구간을 찾아 잘라주고 대사도 채워줍니다.",
                     font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x", padx=4)

        wrap = self._row(f, "보조 참조 음성",
                         "음색만 섞습니다. 대사는 필요 없습니다. 한 줄에 하나씩 적으세요.")
        self.aux_box = ctk.CTkTextbox(wrap, height=80, fg_color=PANEL_SOFT,
                                      border_color="#3a3a52", border_width=1,
                                      font=(FONT, 12), text_color=TEXT)
        self.aux_box.pack(fill="x", pady=(4, 0))
        self.aux_box.insert("1.0", "\n".join(self.cfg.get("aux_ref_audio", [])))

        def add_aux():
            paths = filedialog.askopenfilenames(
                title="보조 참조 음성",
                filetypes=[("오디오", "*.wav *.mp3 *.flac"), ("모든 파일", "*.*")])
            for p in paths:
                p = p.replace("/", "\\") if sys.platform == "win32" else p
                cur = self.aux_box.get("1.0", "end").strip()
                self.aux_box.insert("end", ("\n" if cur else "") + p)

        ctk.CTkButton(wrap, text="파일 추가", width=100, height=30,
                      fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 12), command=add_aux).pack(anchor="w", pady=(6, 0))

        self._slider(f, "tts.temperature", "표현 다양성 (temperature)",
                     "낮을수록 안정적이고 단조롭습니다. 방송에는 0.7~0.9 가 무난합니다.",
                     0.1, 1.5, 28)
        self._slider(f, "tts.top_p", "top_p", "기본값을 그대로 두면 됩니다.", 0.1, 1.0, 18)
        self._slider(f, "tts.top_k", "top_k", "기본값을 그대로 두면 됩니다.",
                     1, 100, 99, "{:.0f}")

        self._entry(f, "audio.cable_device_name", "출력 장치 이름",
                    "이 이름이 들어간 장치로 소리를 보냅니다. "
                    "VTube Studio 는 CABLE Output 을 듣게 설정하세요.")
        self._switch(f, "audio.enable_monitor", "내 스피커로도 함께 듣기")

    def _tab_stt(self, tab):
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        self._option(f, "stt.model_size", "음성 인식 모델",
                     "클수록 정확하지만 느리고 그래픽 메모리를 더 씁니다.",
                     ["tiny", "base", "small", "medium", "large-v3"])
        self._option(f, "stt.device", "연산 장치",
                     "cuda 실패 시 자동으로 cpu 로 넘어갑니다.", ["cuda", "cpu"])
        self._slider(f, "stt.silence_sec", "말이 끝났다고 판단할 침묵 시간 (초)",
                     "짧으면 뜸 들일 때 문장이 잘립니다.", 0.5, 3.0, 25, "{:.1f}")
        self._slider(f, "stt.threshold", "마이크 감도",
                     "말하지 않아도 인식되면 올리고, 말해도 안 잡히면 내리세요.",
                     0.002, 0.05, 48, "{:.3f}")
        self._entry(f, "stt.toggle_key", "마이크 토글 키",
                    "이 키를 누르면 마이크가 켜지고 꺼집니다.")

        self._slider(f, "vision.interval_sec", "화면을 보는 간격 (초)",
                     "짧을수록 반응이 잦아지지만 비용이 늘어납니다.",
                     10, 120, 22, "{:.0f}")

        wrap = self._row(f, "무엇을 볼지",
                         "대상 선택과 미리보기는 전용 창에서 합니다. "
                         "메인 화면의 톱니 버튼으로도 열 수 있습니다.")
        self.target_label = ctk.CTkLabel(wrap, text=target_summary(self.cfg["vision"]),
                                         font=(FONT, 12), text_color=TEXT, anchor="w")
        self.target_label.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(wrap, text="대상 고르기 · 미리보기", height=34,
                      fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                      command=self._open_target).pack(fill="x", pady=(6, 0))

        self._slider(f, "vision.max_width", "화면을 보내는 해상도 (가로 픽셀)",
                     "클수록 글자를 잘 읽지만 비용이 늘어납니다. "
                     "좁은 영역이면 낮아도 되고, 넓은 화면일수록 높여야 합니다.",
                     640, 1568, 29, "{:.0f}")

        self._slider(f, "chat.queue_size", "채팅 대기 개수",
                     "이보다 많이 쌓이면 오래된 채팅부터 버립니다.", 1, 20, 19, "{:.0f}")
        self._slider(f, "chat.batch", "한 번에 묶어 답할 채팅 수",
                     "채팅이 몰릴 때 이 개수를 묶어 한 번에 반응합니다.", 1, 8, 7, "{:.0f}")

    def _tab_persona(self, tab):
        f = ctk.CTkFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(f, text="성격 · 말투", font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(f, text="라이카가 어떻게 말할지 정합니다. "
                             "예시 대화를 몇 개 적어두면 말투가 잘 잡힙니다.",
                     font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x")

        self.persona_box = ctk.CTkTextbox(f, fg_color=PANEL_SOFT, border_width=1,
                                          border_color="#3a3a52", font=(FONT, 12),
                                          text_color=TEXT, wrap="word")
        self.persona_box.pack(fill="both", expand=True, pady=(8, 8))
        self.persona_box.insert("1.0", self.cfg["persona"])

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="기본값으로 되돌리기", width=150, height=30,
                      fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                      command=self._reset_persona).pack(side="left")

        self._entry(f, "max_tokens", "최대 응답 길이 (max_tokens)",
                    "숫자를 직접 적습니다. 작을수록 짧게 말하고 빨리 반응합니다.\n"
                    "권장 400 이상. 200 에서는 검증 15회 중 6회(40%)가 문장이 잘려 "
                    "아무 말도 못 했습니다 (stop_reason=max_tokens). "
                    "실측 출력은 최대 250 토큰이었습니다. "
                    "근거: laika_empty_check.json 45건.")

    def _section(self, parent, title, hint=None):
        """고급 탭 안의 소제목."""
        ctk.CTkFrame(parent, height=1, fg_color=LINE).pack(
            fill="x", padx=4, pady=(16, 0))
        ctk.CTkLabel(parent, text=title, font=(FONT, 13, "bold"),
                     text_color=ACCENT, anchor="w").pack(fill="x", padx=4, pady=(8, 0))
        if hint:
            ctk.CTkLabel(parent, text=hint, font=(FONT, 11), text_color=MUTED,
                         anchor="w", justify="left", wraplength=620).pack(
                fill="x", padx=4)

    def _tab_advanced(self, tab):
        """config.json 에는 있는데 창에서 못 고치던 값들.

        전에는 이 16개를 고치려면 config.json 을 직접 열어야 했다."""
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        # ---------------------------------------------------------- 표정
        self._section(f, "표정",
                      "핫키 이름과 감정 연결은 vts_emotion_map.json 에서 고칩니다. "
                      "실제 표정이 뭔지 보려면 '준비물 점검' 창의 표정 점검을 쓰세요.")
        self._switch(f, "emotion.enabled", "표정 사용",
                     "끄면 감정 도구를 붙이지 않고 문장만 받습니다.")
        self._switch(f, "emotion.skip_if_same", "같은 감정이면 다시 안 누르기",
                     "직전과 같은 표정이면 VTS 호출을 건너뜁니다.")
        self._slider(f, "emotion.reset_after_sec", "발화 후 맨 얼굴로 돌아가는 시간 (초)",
                     "0 이면 즉시. 크게 하면 다음 발화까지 표정이 유지됩니다.",
                     0.0, 10.0, 20, "{:.1f}")
        self._slider(f, "emotion.timeout_sec", "표정 호출 제한 시간 (초)",
                     "이 시간을 넘겨도 발화는 그대로 진행됩니다.",
                     0.5, 10.0, 19, "{:.1f}")
        self._entry(f, "emotion.map_file", "감정 매핑 파일",
                    "감정 이름과 VTS 핫키 이름을 연결한 파일.")
        self._entry(f, "vts.host", "VTube Studio 주소",
                    "같은 PC 면 127.0.0.1 입니다.")
        self._entry(f, "vts.port", "VTube Studio 포트",
                    "VTS 의 API 설정에 적힌 번호. 기본 8001.")

        # ---------------------------------------------------------- 기억
        self._section(f, "대화 기억",
                      "턴 단위로 셉니다. 한 턴은 '말 걸기 + 대답' 한 쌍입니다. "
                      "채팅과 화면은 서로 다른 통이라 밀어내지 않습니다.")
        self._slider(f, "history.chat_turns", "채팅을 기억할 턴 수",
                     "많을수록 앞 얘기를 잘 잇지만 요청이 길어져 느려집니다.",
                     1, 60, 59, "{:.0f}")
        self._slider(f, "history.vision_turns", "화면을 기억할 턴 수",
                     "게임 화면에 대한 반응을 몇 턴까지 남길지.",
                     0, 20, 20, "{:.0f}")
        self._switch(f, "history.persist", "방송을 껐다 켜도 대화 이어가기",
                     "끄면 시작할 때마다 기억이 비워집니다.")
        self._slider(f, "history.resume_within_hours", "이어받을 수 있는 시간 (시간)",
                     "마지막 대화가 이보다 오래됐으면 새로 시작합니다.",
                     1, 72, 71, "{:.0f}")
        self._entry(f, "history.dir", "대화 기록 폴더",
                    "세션 파일이 저장되는 폴더 이름.")

        # ---------------------------------------------------------- 기록
        self._section(f, "응답 시간 기록",
                      "응답마다 한 줄씩 남깁니다. laika_metrics_view.py 로 표와 그래프를 봅니다.")
        self._switch(f, "metrics.enabled", "응답 시간 기록하기")
        self._entry(f, "metrics.file", "기록 파일",
                    "엑셀로 바로 열립니다.")

        # ---------------------------------------------------------- 그 밖
        self._section(f, "그 밖")
        self._slider(f, "stt.min_speech_sec", "말로 인정할 최소 길이 (초)",
                     "이보다 짧은 소리는 무시합니다. 기침이나 잡음을 거릅니다.",
                     0.1, 3.0, 29, "{:.2f}")
        self._switch(f, "audio.enable_monitor", "내 스피커로도 같이 듣기",
                     "끄면 VB-Cable 로만 나갑니다. 방송에 나가는 소리는 그대로입니다.")

    def _reset_persona(self):
        if messagebox.askyesno("기본값으로 되돌리기",
                               "지금 작성한 내용을 지우고 기본 성격으로 되돌릴까요?",
                               parent=self):
            self.persona_box.delete("1.0", "end")
            self.persona_box.insert("1.0", DEFAULT_CFG["persona"])

    def _open_refmaker(self):
        def apply(path, text):
            self.vars["ref_audio"].set(path)
            self.vars["ref_text"].set(text)
        RefMakerWindow(self, self._get("stt.model_size"),
                       self._get("stt.device"), apply)

    def _open_target(self):
        def saved(cfg):
            self.cfg["vision"] = cfg["vision"]
            self.target_label.configure(text=target_summary(self.cfg["vision"]))
        VisionTargetWindow(self, self.cfg, saved)

    # ------------------------------------------------------------ 저장

    # 켜짐/꺼짐으로 보여주지만 설정에는 숫자로 들어가야 하는 것
    BOOL_AS_INT = ("tts.streaming_mode",)

    INT_KEYS = ("max_tokens", "tts.top_k", "vision.interval_sec",
                "vision.max_width", "chat.queue_size", "chat.batch",
                "vts.port", "history.chat_turns", "history.vision_turns",
                "history.resume_within_hours")

    def _save(self):
        for key, var in self.vars.items():
            val = var.get()
            if key in self.BOOL_AS_INT:
                val = 1 if val else 0
            if key in self.INT_KEYS:
                # max_tokens 등이 슬라이더에서 입력칸으로 바뀌었다.
                # 숫자가 아닌 값이 들어오면 저장을 통째로 날리지 말고
                # 그 항목만 이전 값을 지킨다.
                try:
                    val = int(round(float(str(val).strip())))
                except (TypeError, ValueError):
                    messagebox.showwarning(
                        "설정",
                        f"'{key}' 에 숫자가 아닌 값이 있어 이전 값을 유지합니다.\n"
                        f"입력한 값: {val!r}",
                        parent=self)
                    continue
            self._set(key, val)

        self.cfg["aux_ref_audio"] = [
            l.strip() for l in self.aux_box.get("1.0", "end").splitlines() if l.strip()
        ]
        self.cfg["persona"] = self.persona_box.get("1.0", "end").strip()

        save_config(self.cfg)
        self.on_save(self.cfg)
        self.destroy()


# =============================================================================
# 메인 창
# =============================================================================

class LaikaApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.core = None
        self.events = queue.Queue()      # 코어 콜백을 담아 두는 곳

        self.title("라이카")
        self.geometry("1080x680")
        self.minsize(880, 560)
        self.configure(fg_color=BG)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=270)
        self.grid_rowconfigure(0, weight=1)

        self._build_main()
        self._build_side()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain_events)
        self._append("system", "설정을 확인한 뒤 방송 시작을 누르세요.")

    # ------------------------------------------------------------ 좌측

    def _build_main(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.log = ctk.CTkTextbox(wrap, fg_color=PANEL, corner_radius=12,
                                  font=(FONT, 13), text_color=TEXT, wrap="word",
                                  border_width=0)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.configure(state="disabled")

        for kind, color in COLORS.items():
            self.log.tag_config(kind, foreground=color)
        self.log.tag_config("time", foreground="#5a5a72")

        bar = ctk.CTkFrame(wrap, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        bar.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            bar, height=44, corner_radius=12, fg_color=PANEL,
            border_color="#33334a", font=(FONT, 13), text_color=TEXT,
            placeholder_text="라이카에게 말하기   (닉네임: 내용  으로 치면 시청자 채팅으로 들어갑니다)")
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda _: self._send())

        ctk.CTkButton(bar, text="보내기", width=88, height=44, corner_radius=12,
                      fg_color=ACCENT, hover_color=ACCENT_HOV,
                      font=(FONT, 13, "bold"), command=self._send
                      ).grid(row=0, column=1, padx=(8, 0))

    # ------------------------------------------------------------ 우측

    def _build_side(self):
        side = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)

        head = ctk.CTkFrame(side, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(18, 4))
        ctk.CTkLabel(head, text="라이카", font=(FONT, 22, "bold"),
                     text_color=TEXT).pack(anchor="w")
        self.state_label = ctk.CTkLabel(head, text="대기 중", font=(FONT, 12),
                                        text_color=MUTED)
        self.state_label.pack(anchor="w")

        self.btn_run = ctk.CTkButton(side, text="방송 시작", height=46, corner_radius=12,
                                     fg_color=ACCENT, hover_color=ACCENT_HOV,
                                     font=(FONT, 15, "bold"), command=self._toggle_run)
        self.btn_run.pack(fill="x", padx=18, pady=(14, 10))

        self.btn_mic = ctk.CTkButton(
            side, text="마이크  꺼짐", height=38, corner_radius=10,
            fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 13),
            command=self._toggle_mic, state="disabled")
        self.btn_mic.pack(fill="x", padx=18, pady=(0, 8))

        vis_row = ctk.CTkFrame(side, fg_color="transparent")
        vis_row.pack(fill="x", padx=18, pady=(0, 4))
        self.btn_vision = ctk.CTkButton(
            vis_row, text="화면 보기  꺼짐", height=38, corner_radius=10,
            fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 13),
            command=self._toggle_vision, state="disabled")
        self.btn_vision.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(vis_row, text="⚙", width=40, height=38, corner_radius=10,
                      fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 15),
                      command=self._open_vision_target).pack(side="left", padx=(6, 0))

        self.target_hint = ctk.CTkLabel(
            side, text=target_summary(self.cfg["vision"]), font=(FONT, 10),
            text_color=MUTED, anchor="w", justify="left", wraplength=230)
        self.target_hint.pack(fill="x", padx=18, pady=(0, 8))

        ctk.CTkFrame(side, height=1, fg_color=LINE).pack(fill="x", padx=18, pady=10)

        ctk.CTkLabel(side, text="상태", font=(FONT, 12, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=18)

        self.status_labels = {}
        for key, name in (("tts", "음성 서버"), ("chzzk", "치지직"),
                          ("mic", "마이크"), ("vision", "화면"),
                          ("expression", "표정")):
            row = ctk.CTkFrame(side, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=3)
            ctk.CTkLabel(row, text=name, font=(FONT, 12),
                         text_color=MUTED).pack(side="left")
            lab = ctk.CTkLabel(row, text="-", font=(FONT, 12), text_color=MUTED)
            lab.pack(side="right")
            self.status_labels[key] = lab

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=18, pady=18)
        ctk.CTkButton(bottom, text="설정", height=38, corner_radius=10,
                      fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 13), command=self._open_settings).pack(fill="x")
        ctk.CTkButton(bottom, text="준비물 점검", height=38, corner_radius=10,
                      fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 13), command=self._open_setup).pack(fill="x", pady=(6, 0))
        ctk.CTkButton(bottom, text="로그 지우기", height=32, corner_radius=10,
                      fg_color="transparent", hover_color=PANEL_SOFT,
                      text_color=MUTED, font=(FONT, 12),
                      command=self._clear_log).pack(fill="x", pady=(6, 0))

    # ------------------------------------------------------------ 동작

    def _toggle_run(self):
        if self.core and self.core.running:
            self.core.stop()
            self.btn_run.configure(text="방송 시작", fg_color=ACCENT,
                                   hover_color=ACCENT_HOV)
            self.btn_mic.configure(state="disabled", fg_color=PANEL_SOFT,
                                   text="마이크  꺼짐")
            self.btn_vision.configure(state="disabled", fg_color=PANEL_SOFT,
                                      text="화면 보기  꺼짐")
            self.state_label.configure(text="대기 중", text_color=MUTED)
            return

        from laika_core import LaikaCore
        core = LaikaCore(self.cfg,
                         on_log=lambda k, t: self.events.put(("log", k, t)),
                         on_status=lambda k, v: self.events.put(("status", k, v)))

        problems = core.preflight()
        if problems:
            messagebox.showwarning(
                "설정을 확인하세요",
                "다음 항목을 먼저 채워야 방송을 시작할 수 있습니다.\n\n"
                + "\n".join(f"· {p}" for p in problems))
            self._open_settings()
            return

        self.core = core
        self.btn_run.configure(text="방송 종료", fg_color=DANGER, hover_color=DANGER_HOV)
        self.state_label.configure(text="준비 중", text_color=MUTED)
        self.btn_mic.configure(state="normal")
        self.btn_vision.configure(state="normal")
        core.start()

    def _toggle_mic(self):
        if not (self.core and self.core.running):
            return
        on = self.core.toggle_mic()
        self.btn_mic.configure(text=f"마이크  {'켜짐' if on else '꺼짐'}",
                               fg_color=OK if on else PANEL_SOFT)

    def _toggle_vision(self):
        if not (self.core and self.core.running):
            return
        on = self.core.toggle_vision()
        self.btn_vision.configure(text=f"화면 보기  {'켜짐' if on else '꺼짐'}",
                                  fg_color=OK if on else PANEL_SOFT)

    def _send(self):
        text = self.entry.get().strip()
        if not text:
            return
        if not (self.core and self.core.running):
            self._append("system", "방송을 먼저 시작하세요.")
            return
        self.entry.delete(0, "end")

        if ":" in text and not text.startswith("/"):
            nick, msg = text.split(":", 1)
            self.core.send_chat(nick.strip(), msg.strip())
        else:
            self.core.send_host(text.lstrip("/").strip())

    def _open_settings(self):
        SettingsWindow(self, self.cfg, self._on_settings_saved)

    def _on_settings_saved(self, cfg):
        self.cfg = cfg
        self.target_hint.configure(text=target_summary(cfg["vision"]))
        if self.core and self.core.running:
            self.core.cfg = cfg
        self._append("system", "설정을 저장했습니다.")

    def _open_vision_target(self):
        def saved(cfg):
            self.cfg = cfg
            save_config(cfg)
            self.target_hint.configure(text=target_summary(cfg["vision"]))
            if self.core and self.core.running:
                self.core.cfg = cfg          # 방송 중에도 바로 반영
            self._append("system", f"화면 대상을 바꿨습니다. {target_summary(cfg['vision'])}")

        VisionTargetWindow(self, self.cfg, saved)

    def _open_setup(self):
        SetupWindow(self, self.cfg, self._apply_path, self._open_refmaker)

    def _apply_path(self, key, value):
        self.cfg[key] = value
        save_config(self.cfg)
        self._append("system", f"경로를 설정했습니다: {value}")

    def _open_refmaker(self):
        def apply(path, text):
            self.cfg["ref_audio"] = path
            self.cfg["ref_text"] = text
            save_config(self.cfg)
            self._append("system", f"참조 음성을 설정했습니다: {Path(path).name}")

        RefMakerWindow(self, self.cfg["stt"]["model_size"],
                       self.cfg["stt"]["device"], apply)

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ 이벤트

    def _drain_events(self):
        """코어 콜백은 다른 스레드에서 오므로 큐를 통해 여기서만 위젯을 만진다."""
        try:
            while True:
                kind, a, b = self.events.get_nowait()
                if kind == "log":
                    self._append(a, b)
                else:
                    self._update_status(a, b)
        except queue.Empty:
            pass
        self.after(60, self._drain_events)

    def _append(self, kind, text):
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M:%S")

        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  ", "time")
        self.log.insert("end", f"{LABELS.get(kind, kind)}  ", kind)
        self.log.insert("end", f"{text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_status(self, key, value):
        if key == "running":
            self.state_label.configure(text="방송 중" if value else "대기 중",
                                       text_color=OK if value else MUTED)
            return
        if key == "busy":
            self.state_label.configure(text="말하는 중" if value else "방송 중",
                                       text_color=ACCENT if value else OK)
            return

        lab = self.status_labels.get(key)
        if lab is None:
            return
        if isinstance(value, bool):
            lab.configure(text="켜짐" if value else "꺼짐",
                          text_color=OK if value else MUTED)
        else:
            lab.configure(text=str(value),
                          text_color=OK if value == "연결됨" else MUTED)

    def _on_close(self):
        if self.core and self.core.running:
            self.core.stop()
        self.destroy()


if __name__ == "__main__":
    LaikaApp().mainloop()
