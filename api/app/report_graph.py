# -*- coding: utf-8 -*-
"""
AI 리포트 그래프 (2026-10-07, 계획서 edgeserver-JS/2026-10-07_AI리포트_젯슨LangGraph_계획서v4_랩탑ClaudeCode.md §1.1·§6).

  · 역할 분담(사용자 결정 10-07): GPU 서버의 llama-server 는 LLM 추론만 빌려준다. LangGraph 와 보고서 작성은 이 api 안.
  · LLM 서버(팀원 가이드): OpenAI 호환 `REPORT_LLM_URL`(…/v1), Bearer `HELPAI_LLM_KEY`, 요청 하나당 16384 토큰(입력 + 출력),
    넘으면 곧바로 400 "exceeds the available context size". 모든 호출에 enable_thinking=false.

★ 그래프 단계·결과 JSON 형식은 아직 정해지지 않았다(리포트 내용은 팀 회의 뒤 사용자가 정함). 지금의 STEPS 는 일정·재시도·
  저장 흐름을 실제 LLM 으로 시험하기 위한 **임시 1단계**다. 단계가 정해지면 STEPS 만 바꾼다.
★ LLM 호출 하나의 시간 초과·max_tokens 도 아직 미정(그래프 단계가 정해진 뒤) → 지금 기본값은 팀 가이드 예시(300초·1500).
"""
import os
import time
from typing import Callable, List, Optional, TypedDict

os.environ.setdefault("LANGSMITH_TRACING", "false")   # 사용자 결정 10-07: 외부 추적은 끈 상태를 명시(compose 에도 있음)

LLM_URL = os.environ.get("REPORT_LLM_URL", "").strip()               # 예: http://210.125.70.71:31194/v1
LLM_KEY = os.environ.get("HELPAI_LLM_KEY", "").strip()               # 값은 젯슨 /data/.env 에만
LLM_TIMEOUT_S = float(os.environ.get("REPORT_LLM_TIMEOUT", "300") or "300")         # 임시(가이드 예시)
LLM_MAX_TOKENS = int(os.environ.get("REPORT_LLM_MAX_TOKENS", "1500") or "1500")    # 임시(가이드 예시)
LLM_TEMPERATURE = 0.2                                                  # 가이드 예시
GRAPH_NAME = "임시 1단계(리포트 내용은 팀 회의 뒤 결정)"

SYSTEM = "너는 스마트팜 리포트 작성 도우미다."


class ContextOverflow(Exception):
    """재료가 컨텍스트 한도를 넘어 LLM 서버가 거절함. 같은 재료로는 다시 해도 같다 → 그 회차는 곧바로 실패(사용자 결정 10-07)."""


class State(TypedDict, total=False):
    material: str
    outputs: dict
    calls: list


class Step:
    """그래프 단계 하나. prompt(state) -> 사용자 메시지 텍스트."""

    def __init__(self, name: str, label: str, prompt: Callable[[dict], str]):
        self.name, self.label, self.prompt = name, label, prompt


# 임시 1단계 — 리포트 내용(단계·형식)이 정해지면 바꾼다.
STEPS: List[Step] = [
    Step("draft", "초안", lambda s: "아래 재료로 이 기간의 리포트를 한국어로 작성하라.\n\n" + s["material"]),
]

_llm_factory: Optional[Callable] = None
_llm = None
_graph = None


def ready() -> bool:
    return bool(LLM_URL and LLM_KEY)


def set_llm(factory: Optional[Callable]):
    """시험용: factory() -> ainvoke(messages) 를 가진 객체. None 이면 실제 ChatOpenAI."""
    global _llm_factory, _llm
    _llm_factory, _llm = factory, None


def _model():
    global _llm
    if _llm is None:
        if _llm_factory is not None:
            _llm = _llm_factory()
        else:
            from langchain_openai import ChatOpenAI
            _llm = ChatOpenAI(base_url=LLM_URL, api_key=LLM_KEY, model="local", temperature=LLM_TEMPERATURE,
                              max_tokens=LLM_MAX_TOKENS, timeout=LLM_TIMEOUT_S, max_retries=0,
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    return _llm


def is_context_overflow(e: BaseException) -> bool:
    return getattr(e, "status_code", None) == 400 and "exceeds the available context size" in str(e)


async def _call(step: Step, state: dict) -> (str, dict):
    t0 = time.monotonic()
    try:
        msg = await _model().ainvoke([("system", SYSTEM), ("human", step.prompt(state))])
    except Exception as e:   # noqa: BLE001
        if is_context_overflow(e):
            raise ContextOverflow(str(e)[:200]) from e
        raise
    usage = getattr(msg, "usage_metadata", None) or {}
    meta = getattr(msg, "response_metadata", None) or {}
    call = {"step": step.name, "seconds": round(time.monotonic() - t0, 2),
            "prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
            "finish_reason": meta.get("finish_reason")}
    return str(msg.content or ""), call


def _node(i: int, step: Step):
    async def run(state: State, config) -> dict:
        cb = ((config or {}).get("configurable") or {}).get("on_step")
        if cb is not None:
            cb(i + 1, len(STEPS), step.name, step.label)
        text, call = await _call(step, state)
        return {"outputs": dict(state.get("outputs") or {}, **{step.name: text}),
                "calls": list(state.get("calls") or []) + [call]}
    return run


def graph():
    global _graph
    if _graph is None:
        from langgraph.graph import END, START, StateGraph
        g = StateGraph(State)
        prev = START
        for i, step in enumerate(STEPS):
            g.add_node(step.name, _node(i, step))
            g.add_edge(prev, step.name)
            prev = step.name
        g.add_edge(prev, END)
        _graph = g.compile()
    return _graph


async def run(material: str, on_step: Optional[Callable] = None) -> dict:
    """그래프를 처음부터 끝까지 한 번 실행한다. 중간에 실패하면 예외(다음 시도는 처음부터 — 사용자 결정 10-07)."""
    out = await graph().ainvoke({"material": material, "outputs": {}, "calls": []},
                                config={"configurable": {"on_step": on_step}})
    return {"outputs": out.get("outputs") or {}, "calls": out.get("calls") or []}
