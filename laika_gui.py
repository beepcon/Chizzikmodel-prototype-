# =============================================================================
# 라이카 - GUI
#
# 단일 창 구성
#   좌측 : 대화 로그 + 입력창
#   우측 : 상태 표시 / 조작 버튼 / 설정
#
# 파이프라인은 laika_core.LaikaCore 가 담당한다.
# 코어의 콜백은 별도 스레드에서 오므로 큐에 넣고 Tk 타이머로 꺼내 처리한다.
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
MUTED       = "#8b8ba3"
TEXT        = "#e8e8f0"

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
    "tts": {"top_k": 34, "top_p": 0.9, "temperature": 0.8},
    "audio": {"cable_device_name": "CABLE Input",
              "cable_device_index": None, "enable_monitor": True},
    "stt": {"model_size": "medium", "device": "cuda", "silence_sec": 1.5,
            "min_speech_sec": 0.5, "threshold": 0.01, "toggle_key": "f8"},
    "vision": {"enabled": False, "interval_sec": 30, "monitor": 1,
               "max_width": 1024, "jpeg_quality": 70},
    "chat": {"queue_size": 5, "batch": 3},
    "persona": (
        "너는 AI 버튜버 '라이카'이다. 시청자 채팅에 반응하는 잡담 방송 중이다.\n"
        "- 말투: 밝고 친근하고 장난기 있게, 존댓말\n"
        "- 답변은 1~3문장, 짧고 리듬감 있게 (TTS 로 읽히므로 이모티콘/특수문자 금지)\n"
        "- 게임과 애니를 좋아함\n"
        "- 정치/종교/혐오 주제는 자연스럽게 회피\n"
        "- [호스트]가 말하면 방송 진행자의 말이다. 시청자보다 우선해서 대화해라\n"
        "- 여러 채팅이 한번에 오면 개별로 답하지 말고 자연스럽게 묶어서 반응해라\n"
        "- [게임화면]이 오면 호스트가 플레이 중인 화면이다. 옆에서 보는 친구처럼 짧게 리액션해라"
    ),
}


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
        self.geometry("720x640")
        self.configure(fg_color=BG)
        self.transient(master)
        self.after(200, self.lift)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(16, 0))
        ctk.CTkLabel(head, text="준비물 점검", font=(FONT, 18, "bold"),
                     text_color=TEXT).pack(anchor="w")

        import laika_setup
        prog = laika_setup.program_dir()
        ctk.CTkLabel(
            head,
            text=f"내려받은 것들은 아래 폴더 안에 두면 자동으로 찾습니다.\n{prog}",
            font=(FONT, 11), text_color=MUTED, anchor="w",
            justify="left").pack(fill="x", pady=(2, 0))

        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, padx=12, pady=12)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="닫기", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(bar, text="다시 검사", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOV, font=(FONT, 13, "bold"),
                      command=self.refresh).pack(side="right", padx=8)

        self.refresh()

    def refresh(self):
        import laika_setup

        for w in self.list_frame.winfo_children():
            w.destroy()

        for item in laika_setup.check_all(self.cfg):
            self._card(item)

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
            ctk.CTkButton(
                row, text="이 경로 적용", width=120, height=32,
                fg_color=ACCENT, hover_color=ACCENT_HOV, font=(FONT, 12, "bold"),
                command=lambda p=item["found"]: self._apply(p)).pack(side="left")
            ctk.CTkButton(
                row, text="받는 곳 열기", width=110, height=32,
                fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                command=lambda u=item["link"]: self._open(u)).pack(side="left", padx=(6, 0))

        elif action == "link":
            ctk.CTkButton(
                row, text="받는 곳 열기", width=120, height=32,
                fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
                command=lambda u=item["link"]: self._open(u)).pack(side="left")

        elif action == "refmaker":
            ctk.CTkButton(
                row, text="참조 음성 만들기", width=140, height=32,
                fg_color=ACCENT, hover_color=ACCENT_HOV, font=(FONT, 12, "bold"),
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
    결과는 큐를 통해 받는다. Tk 위젯은 이 창에서만 만진다.
    """

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

    # ------------------------------------------------------------ 동작

    def _choose(self):
        p = filedialog.askopenfilename(
            title="분석할 음성 파일",
            filetypes=[("오디오", "*.wav *.mp3 *.flac *.m4a"), ("모든 파일", "*.*")])
        if p:
            self.path_var.set(p.replace("/", "\\") if sys.platform == "win32" else p)

    def _scan(self):
        path = self.path_var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning("파일 없음", "분석할 음성 파일을 먼저 고르세요.", parent=self)
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
                self.events.put(("status", f"음성 인식 모델을 불러오는 중입니다 ({self.model_size})..."))
                from faster_whisper import WhisperModel
                try:
                    self.model = WhisperModel(
                        self.model_size, device=self.device,
                        compute_type="float16" if self.device == "cuda" else "int8")
                except Exception:
                    self.events.put(("status", "그래픽카드를 쓸 수 없어 CPU 로 분석합니다. 시간이 더 걸립니다."))
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
                    self.status.configure(text=val)
                elif kind == "progress":
                    self.progress.set(val)
                elif kind == "done":
                    self._show_results(val)
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
                text="쓸 만한 구간을 찾지 못했습니다. 더 길거나 또렷하게 말하는 녹음을 넣어보세요.",
                text_color=COLORS["error"])
            return

        self.status.configure(text=f"{len(found)}개 구간을 찾았습니다. 하나를 고르세요.",
                              text_color=OK)

        for i, c in enumerate(found):
            card = ctk.CTkFrame(self.list_frame, fg_color=PANEL_SOFT, corner_radius=8)
            card.pack(fill="x", pady=4, padx=4)

            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=12, pady=10)
            ctk.CTkLabel(left, text=f"{int(c['start'] // 60)}분 {int(c['start'] % 60)}초 "
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

        def wait_saved():
            try:
                kind, val = self.events.get_nowait()
                if kind == "saved":
                    path, text, dur = val
                    self.on_apply(path, text)
                    messagebox.showinfo(
                        "완료",
                        f"참조 음성을 저장했습니다.\n\n"
                        f"길이: {dur:.1f}초\n대사: {text}\n\n"
                        f"설정에 자동으로 입력했습니다. 저장을 눌러 적용하세요.",
                        parent=self)
                    self.destroy()
                    return
                self.events.put((kind, val))
            except queue.Empty:
                pass
            self.after(100, wait_saved)

        self.after(100, wait_saved)


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
        self.after(200, self.lift)          # 부모 창 뒤로 숨는 것 방지

        tabs = ctk.CTkTabview(
            self, fg_color=PANEL, segmented_button_fg_color=PANEL_SOFT,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOV,
            text_color=TEXT, corner_radius=10)
        tabs.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        for name in ("연결", "음성", "인식 · 화면", "성격"):
            tabs.add(name)

        self._tab_connection(tabs.tab("연결"))
        self._tab_voice(tabs.tab("음성"))
        self._tab_stt(tabs.tab("인식 · 화면"))
        self._tab_persona(tabs.tab("성격"))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(bar, text="취소", width=100, fg_color=PANEL_SOFT,
                      hover_color="#31314a", font=(FONT, 13),
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(bar, text="저장", width=100, fg_color=ACCENT,
                      hover_color=ACCENT_HOV, font=(FONT, 13, "bold"),
                      command=self._save).pack(side="right", padx=8)

    # ------------------------------------------------------------ 위젯 헬퍼

    def _row(self, parent, label, hint=None):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", pady=(10, 0), padx=4)
        ctk.CTkLabel(wrap, text=label, font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x")
        if hint:
            ctk.CTkLabel(wrap, text=hint, font=(FONT, 11),
                         text_color=MUTED, anchor="w").pack(fill="x")
        return wrap

    def _entry(self, parent, key, label, hint=None, show=None):
        wrap = self._row(parent, label, hint)
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
        var = ctk.DoubleVar(value=val)
        self.vars[key] = var
        show = ctk.CTkLabel(row, text=fmt.format(val), width=52,
                            font=(FONT, 12), text_color=ACCENT)
        show.pack(side="right")
        ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=steps, variable=var,
                      button_color=ACCENT, button_hover_color=ACCENT_HOV,
                      progress_color=ACCENT,
                      command=lambda v: show.configure(text=fmt.format(v))
                      ).pack(side="left", fill="x", expand=True)
        return var

    def _option(self, parent, key, label, hint, values):
        wrap = self._row(parent, label, hint)
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
        var = ctk.BooleanVar(value=bool(self._get(key)))
        self.vars[key] = var
        ctk.CTkSwitch(wrap, text=label, variable=var, font=(FONT, 13),
                      progress_color=ACCENT, text_color=TEXT).pack(anchor="w")
        if hint:
            ctk.CTkLabel(wrap, text=hint, font=(FONT, 11),
                         text_color=MUTED, anchor="w").pack(fill="x")
        return var

    def _open_refmaker(self):
        def apply(path, text):
            self.vars["ref_audio"].set(path)
            self.vars["ref_text"].set(text)

        RefMakerWindow(self, self._get("stt.model_size"),
                       self._get("stt.device"), apply)

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

    # ------------------------------------------------------------ 탭

    def _tab_connection(self, tab):
        f = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True)

        self._entry(f, "api_key", "Anthropic API 키",
                    "console.anthropic.com 에서 발급합니다. 이 파일을 공유하지 마세요.",
                    show="●")
        self._entry(f, "model", "모델", "기본값을 그대로 두면 됩니다.")
        self._entry(f, "chzzk_channel_id", "치지직 채널 ID",
                    "채널 주소 chzzk.naver.com/ 뒤에 오는 문자열. 비우면 채팅 연동을 하지 않습니다.")
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

        ctk.CTkButton(
            f, text="긴 녹음에서 참조 음성 만들기", height=36, corner_radius=10,
            fg_color=PANEL_SOFT, hover_color="#31314a", font=(FONT, 12),
            command=self._open_refmaker
        ).pack(fill="x", padx=4, pady=(8, 0))
        ctk.CTkLabel(
            f, text="긴 음성 파일을 넣으면 쓸 만한 구간을 찾아 잘라주고 대사도 채워줍니다.",
            font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x", padx=4)

        wrap = self._row(f, "보조 참조 음성",
                         "음색만 섞습니다. 대사는 필요 없습니다. 한 줄에 하나씩 경로를 적으세요.")
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
                self.aux_box.insert("end", (("\n" if cur else "") + p))

        ctk.CTkButton(wrap, text="파일 추가", width=100, height=30,
                      fg_color=PANEL_SOFT, hover_color="#31314a",
                      font=(FONT, 12), command=add_aux).pack(anchor="w", pady=(6, 0))

        self._slider(f, "tts.temperature", "표현 다양성 (temperature)",
                     "낮을수록 안정적이고 단조롭습니다. 방송에는 0.7~0.9 가 무난합니다.",
                     0.1, 1.5, 28)
        self._slider(f, "tts.top_p", "top_p", "기본값을 그대로 두면 됩니다.", 0.1, 1.0, 18)
        self._slider(f, "tts.top_k", "top_k", "기본값을 그대로 두면 됩니다.",
                     1, 100, 99, fmt="{:.0f}")

        self._entry(f, "audio.cable_device_name", "출력 장치 이름",
                    "이 이름이 들어간 장치를 찾아 소리를 보냅니다. VTube Studio 는 CABLE Output 을 듣게 설정하세요.")
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
        self._slider(f, "vision.monitor", "캡처할 모니터 번호",
                     "1 이 주 모니터입니다.", 1, 4, 3, "{:.0f}")
        self._slider(f, "chat.queue_size", "채팅 대기 개수",
                     "이보다 많이 쌓이면 오래된 채팅부터 버립니다.", 1, 20, 19, "{:.0f}")
        self._slider(f, "chat.batch", "한 번에 묶어 답할 채팅 수",
                     "채팅이 몰릴 때 이 개수를 묶어 한 번에 반응합니다.", 1, 8, 7, "{:.0f}")

    def _tab_persona(self, tab):
        f = ctk.CTkFrame(tab, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=4, pady=4)

        ctk.CTkLabel(f, text="성격 · 말투", font=(FONT, 13, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x")
        ctk.CTkLabel(f, text="라이카가 어떻게 말할지 정합니다. 예시 대화를 몇 개 적어두면 말투가 잘 잡힙니다.",
                     font=(FONT, 11), text_color=MUTED, anchor="w").pack(fill="x")

        self.persona_box = ctk.CTkTextbox(f, fg_color=PANEL_SOFT, border_width=1,
                                          border_color="#3a3a52", font=(FONT, 12),
                                          text_color=TEXT, wrap="word")
        self.persona_box.pack(fill="both", expand=True, pady=(8, 8))
        self.persona_box.insert("1.0", self.cfg["persona"])

        self._slider(f, "max_tokens", "최대 응답 길이",
                     "작을수록 짧게 말하고 빨리 반응합니다.", 50, 500, 45, "{:.0f}")

    # ------------------------------------------------------------ 저장

    def _save(self):
        for key, var in self.vars.items():
            val = var.get()
            if key in ("max_tokens", "tts.top_k", "vision.interval_sec",
                       "vision.monitor", "chat.queue_size", "chat.batch"):
                val = int(round(float(val)))
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

        self.btn_mic = self._toggle_button(side, "마이크", self._toggle_mic)
        self.btn_vision = self._toggle_button(side, "화면 보기", self._toggle_vision)

        ctk.CTkFrame(side, height=1, fg_color="#2c2c40").pack(fill="x", padx=18, pady=14)

        ctk.CTkLabel(side, text="상태", font=(FONT, 12, "bold"),
                     text_color=MUTED).pack(anchor="w", padx=18)

        self.status_labels = {}
        for key, name in (("tts", "음성 서버"), ("chzzk", "치지직"),
                          ("mic", "마이크"), ("vision", "화면")):
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

    def _toggle_button(self, parent, text, command):
        btn = ctk.CTkButton(parent, text=f"{text}  꺼짐", height=38, corner_radius=10,
                            fg_color=PANEL_SOFT, hover_color="#31314a",
                            font=(FONT, 13), command=command, state="disabled")
        btn.pack(fill="x", padx=18, pady=(0, 8))
        btn._label = text
        return btn

    # ------------------------------------------------------------ 동작

    def _toggle_run(self):
        if self.core and self.core.running:
            self.core.stop()
            self.btn_run.configure(text="방송 시작", fg_color=ACCENT,
                                   hover_color=ACCENT_HOV)
            for b in (self.btn_mic, self.btn_vision):
                b.configure(state="disabled", fg_color=PANEL_SOFT,
                            text=f"{b._label}  꺼짐")
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
        self.btn_run.configure(text="방송 종료", fg_color=DANGER,
                               hover_color=DANGER_HOV)
        self.state_label.configure(text="준비 중", text_color=MUTED)
        for b in (self.btn_mic, self.btn_vision):
            b.configure(state="normal")
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

    def _open_setup(self):
        SetupWindow(self, self.cfg, self._apply_path, self._open_refmaker_direct)

    def _apply_path(self, key, value):
        """준비물 점검에서 찾아낸 경로를 설정에 넣고 저장한다."""
        self.cfg[key] = value
        save_config(self.cfg)
        self._append("system", f"경로를 설정했습니다: {value}")

    def _open_refmaker_direct(self):
        def apply(path, text):
            self.cfg["ref_audio"] = path
            self.cfg["ref_text"] = text
            save_config(self.cfg)
            self._append("system", f"참조 음성을 설정했습니다: {Path(path).name}")

        RefMakerWindow(self, self.cfg["stt"]["model_size"],
                       self.cfg["stt"]["device"], apply)

    def _on_settings_saved(self, cfg):
        self.cfg = cfg
        self._append("system", "설정을 저장했습니다. 다음 방송 시작부터 적용됩니다.")

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
            self.state_label.configure(
                text="방송 중" if value else "대기 중",
                text_color=OK if value else MUTED)
            return
        if key == "busy":
            self.state_label.configure(
                text="말하는 중" if value else "방송 중",
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
