# =============================================================================
# 대사를 만드는 곳 (LLM 백엔드)
#
# 라이카가 LLM 을 부르는 곳은 이 파일 하나다. laika_core 는 Anthropic 형식으로
# 요청을 만들어 넘기고, 여기서 고른 백엔드에 맞게 바꿔 보낸 뒤 같은 형식으로 돌려받는다.
#
#   anthropic : Anthropic API (지금까지 쓰던 방식)
#   local     : OpenAI 호환 서버 (Ollama, llama.cpp 서버, vLLM, LM Studio 등)
#               같은 PC 든 다른 PC(서버) 든 주소만 맞으면 된다.
#
# 왜 OpenAI 호환 형식인가
#   위 서버들이 모두 /v1/chat/completions 를 같은 형식으로 받는다.
#   서버 프로그램을 바꿔도 이 파일은 그대로 둘 수 있다.
#   requests 만 쓰므로 새로 깔 패키지가 없다.
# ---------------------------------------------------------------------------
# 하드코딩을 피한 부분
#   백엔드 종류, 로컬 서버 주소, 모델 이름, 제한 시간, 서버별 추가 옵션
#   : config.json 의 llm 항목
# =============================================================================

import json
import re

DEFAULTS = {
    "backend": "anthropic",
    # Ollama 의 OpenAI 호환 주소가 기본값이다. llama.cpp 서버는 보통 http://127.0.0.1:8080/v1
    "local_url": "http://127.0.0.1:11434/v1",
    "local_model": "",
    "local_api_key": "",
    "timeout_sec": 60,
    # 서버마다 다른 옵션을 요청 본문에 그대로 덧붙인다.
    # 예) 사고 모드 끄기 - Ollama: {"think": false}
    #     llama.cpp·vLLM: {"chat_template_kwargs": {"enable_thinking": false}}
    "local_extra": {},
}


def llm_cfg(cfg):
    c = dict(DEFAULTS)
    c.update((cfg or {}).get("llm") or {})
    return c


class Result:
    """백엔드와 상관없이 같은 모양으로 돌려준다.

    tool_input : speak 도구가 받은 인자(dict). 도구를 안 썼으면 None
    text       : 도구 밖에서 온 글
    stop_reason: Anthropic 이름으로 맞춘다 ("max_tokens", "tool_use", "end_turn" ...)
    """

    def __init__(self, tool_input=None, text="", stop_reason="", output_tokens=None):
        self.tool_input = tool_input
        self.text = text
        self.stop_reason = stop_reason
        self.output_tokens = output_tokens


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, cfg):
        import anthropic
        self.client = anthropic.Anthropic(api_key=cfg["api_key"])

    def create(self, req, tool_name="speak"):
        resp = self.client.messages.create(**req)
        tool_input, text = None, ""
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and block.name == tool_name:
                tool_input = dict(block.input or {})
            elif getattr(block, "type", "") == "text":
                text += block.text
        usage = getattr(resp, "usage", None)
        return Result(tool_input, text, getattr(resp, "stop_reason", "") or "",
                      getattr(usage, "output_tokens", None) if usage else None)


# OpenAI 의 finish_reason 을 Anthropic 이름으로 바꾼다. laika_core 는 Anthropic 이름으로 판정한다.
_FINISH = {"length": "max_tokens", "tool_calls": "tool_use", "stop": "end_turn",
           "function_call": "tool_use"}
# 사고 모드 모델이 본문에 생각을 섞어 보내는 경우가 있다. 그대로 읽으면 생각까지 말해 버린다.
_THINK = re.compile(r"<think>.*?</think>", re.S)


def to_openai(req, model, extra=None):
    """Anthropic 형식 요청을 OpenAI chat/completions 본문으로 바꾼다."""
    msgs = []
    if req.get("system"):
        msgs.append({"role": "system", "content": req["system"]})
    for m in req.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for b in c:
                if b.get("type") == "text":
                    parts.append({"type": "text", "text": b.get("text", "")})
                elif b.get("type") == "image":
                    src = b.get("source") or {}
                    parts.append({"type": "image_url", "image_url": {
                        "url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"}})
            c = parts
        msgs.append({"role": m.get("role"), "content": c})
    body = {"model": model, "messages": msgs, "max_tokens": req.get("max_tokens", 200)}
    if req.get("tools"):
        body["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema", {})}} for t in req["tools"]]
    tc = req.get("tool_choice")
    if tc and tc.get("type") == "tool":
        body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    body.update(extra or {})
    return body


def from_openai(data, tool_name="speak"):
    """OpenAI 응답을 Result 로 바꾼다. 도구 인자는 문자열(JSON)로 오는 서버와 dict 로 오는 서버가 있다."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_input = None
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        if fn.get("name") == tool_name:
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict):
                tool_input = args
    text = _THINK.sub("", msg.get("content") or "").strip()
    usage = data.get("usage") or {}
    return Result(tool_input, text,
                  _FINISH.get(choice.get("finish_reason"), choice.get("finish_reason") or ""),
                  usage.get("completion_tokens"))


class LocalBackend:
    name = "local"

    def __init__(self, cfg):
        c = llm_cfg(cfg)
        self.url = c["local_url"].rstrip("/") + "/chat/completions"
        self.model = c["local_model"]
        self.timeout = float(c["timeout_sec"])
        self.extra = c.get("local_extra") or {}
        self.headers = {"content-type": "application/json"}
        if c.get("local_api_key"):
            self.headers["authorization"] = f"Bearer {c['local_api_key']}"

    def create(self, req, tool_name="speak"):
        import requests
        body = to_openai(req, self.model, self.extra)
        r = requests.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"로컬 LLM 서버 오류 HTTP {r.status_code}: {r.text[:300]}")
        return from_openai(r.json(), tool_name)


def make_backend(cfg):
    kind = llm_cfg(cfg)["backend"]
    if kind == "local":
        return LocalBackend(cfg)
    if kind == "anthropic":
        return AnthropicBackend(cfg)
    raise ValueError(f"llm.backend 는 anthropic 또는 local 이어야 합니다: {kind!r}")


def preflight(cfg):
    """백엔드별로 빠진 설정을 찾는다. 문제 목록을 돌려준다."""
    c = llm_cfg(cfg)
    if c["backend"] == "local":
        out = []
        if not c["local_url"].strip():
            out.append("로컬 LLM 서버 주소가 비어 있습니다.")
        if not c["local_model"].strip():
            out.append("로컬 LLM 모델 이름이 비어 있습니다.")
        return out
    if c["backend"] != "anthropic":
        return [f"llm.backend 값이 잘못됐습니다: {c['backend']!r} (anthropic 또는 local)"]
    if not (cfg.get("api_key") or "").strip():
        return ["Anthropic API 키가 비어 있습니다."]
    return []
