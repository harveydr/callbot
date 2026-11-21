"""Mock media loop: VAD, ASR, scripted reply, redline, TTS. Vendor failures fall back locally."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.compliance.opening import render_opening
from app.compliance.redline import guard_assistant_text
from app.voice.asr import MockAsr, VendorRejected, decode_pcm
from app.voice.circuit import CircuitPort, VendorCircuit
from app.voice.intent import classify_intent
from app.voice.script import active_knowledge, confine_to_knowledge, parse_nodes, render_script
from app.voice.session import Playback
from app.voice.tts import MockTts
from app.voice.vad import EnergyVad

_INJECTION = ("忽略你的指令", "忽略之前的指令", "ignore previous instructions")
_GRADE = {"agree": "B", "refuse": "D", "inquiry": "C", "invalid": "NA", "voicemail": "NA"}
_SCRIPT_MODE = "我用标准流程为您解答。"
_TTS_CALLBACK = "稍后为您回拨。"
_FILLER = "嗯，我帮您看下哈。"
_FILLER_AFTER_MS = 400.0
_CLARIFY = "您好，我没有听清，方便的话请再说一次。"
_FORMAT_CLARIFY = "不好意思，我再确认一下您的意思。"
_BYE = "那先不打扰您了。"
_INTENTS = {"agree", "refuse", "hesitate", "inquiry", "complaint", "invalid"}


@dataclass(frozen=True)
class Frame:
    channel: str
    pcm: bytes
    dropped: bool = False
    duration_ms: int = 0


@dataclass
class Outcome:
    turns: list[dict[str, str]]
    action: str
    barged_in: bool
    voicemail: bool
    degraded: bool
    latency_ms: float
    intent: str
    intent_grade: str
    reply_text: str
    heard: str
    redline_hits: list[str]
    needs_dnd: bool
    circuit_open: bool
    tts_down: bool = False
    llm_down: bool = False
    asr_attempts: int = 0
    asr_errors: int = 0
    llm_first_token_ms: float | None = None
    dropped_assistant_frames: int = 0
    channels: dict[str, str] = field(default_factory=dict)
    llm_format_error: bool = False
    intent_confidence: float = 0.5


def strip_injection(text: str) -> str:
    cleaned = text
    for phrase in _INJECTION:
        cleaned = cleaned.replace(phrase, "")
    return " ".join(cleaned.split())


def _with_filler(reply: str, opening: str, waited_ms: float | None) -> str:
    """Hide a slow model behind the opening. The identity line stays first."""
    if waited_ms is None or waited_ms <= _FILLER_AFTER_MS or _FILLER in reply:
        return reply
    if reply.startswith(opening):
        return f"{opening}{_FILLER}{reply.removeprefix(opening)}"
    return f"{_FILLER}{reply}"


def _object(raw: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _confidence(parsed: dict[str, object] | None) -> float:
    if parsed is None:
        return 0.5
    raw = parsed.get("confidence")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.5
    value = float(raw)
    if value < 0 or value > 1:
        return 0.5
    return value


def _payloads(llm_mode: str, heard: str) -> list[str]:
    if llm_mode == "bad_json":
        return ["NOT_JSON", "{"]
    if llm_mode == "bad_then_ok":
        return ["NOT_JSON", json.dumps({"intent": "agree"}, ensure_ascii=False)]
    if llm_mode == "bare":
        return [json.dumps({"intent": "agree"}, ensure_ascii=False)]
    if llm_mode == "hostile":
        return [json.dumps({"intent": "agree", "say": "我们保证收益", "skip_compliance": True}, ensure_ascii=False)]
    if llm_mode == "priced":
        return [json.dumps({"intent": "inquiry", "say": "费用是 99 元，合同自动续费。"}, ensure_ascii=False)]
    if llm_mode == "colloquial":
        return [json.dumps({"intent": "inquiry", "say": "一共九十九块。"}, ensure_ascii=False)]
    return [json.dumps({"intent": classify_intent(heard)}, ensure_ascii=False)]


def _fallback_ms(script: list[dict[str, object]] | None) -> int:
    """How long to wait before a silence counts. Zero means the script did not set one."""
    items = script or []
    by_key: dict[str, dict[str, object]] = {}
    for item in items:
        key = item.get("node_key") or item.get("id")
        if isinstance(key, str) and key:
            by_key[key] = item
    current: dict[str, object] | None = by_key.get("n_open") or (items[0] if items else None)
    seen: set[str] = set()
    while isinstance(current, dict):
        key = current.get("node_key") or current.get("id") or ""
        if not isinstance(key, str) or key in seen:
            break
        seen.add(key)
        content = current.get("content")
        if not isinstance(content, dict):
            break
        raw = content.get("fallback_after_ms")
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            return raw
        nxt = content.get("next")
        if isinstance(nxt, list):
            nxt = next((item for item in nxt if isinstance(item, str)), None)
        if not isinstance(nxt, str):
            routes = content.get("routes")
            route_next = routes.get("next") if isinstance(routes, dict) else None
            nxt = route_next if isinstance(route_next, str) else None
        current = by_key.get(nxt) if isinstance(nxt, str) else None
    return 0


def _node_text(script: list[dict[str, object]] | None, key: str) -> str:
    for item in script or []:
        if item.get("node_key") != key and item.get("id") != key:
            continue
        content = item.get("content")
        if isinstance(content, dict):
            text = content.get("text") or content.get("must_say")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _reply(intent: str, opening: str) -> tuple[str, str]:
    if intent == "voicemail":
        return "hangup", f"{opening}检测到语音信箱，本次不留言。"
    if intent == "refuse":
        return "hangup", f"{opening}好的，不再打扰您。"
    if intent == "agree":
        return "speak", f"{opening}感谢您的时间，我确认一下事项。"
    if intent == "invalid":
        return "speak", f"{opening}您好，我没有听清，方便的话请再说一次。"
    return "speak", f"{opening}我记下了，让同事回电给您。"


async def _speak(speaker: MockTts, circuit: CircuitPort, text: str) -> bool:
    """Return True when synthesis was skipped or the vendor rejected it."""
    if not text:
        return False
    if not circuit.allow_vendor():
        return True
    try:
        await speaker.synthesize(text)
    except VendorRejected as exc:
        circuit.observe(exc.status)
        return circuit.open
    circuit.observe_success()
    return False


async def _asr_text(asr: MockAsr, circuit: CircuitPort, pcm: bytes) -> tuple[str, bool, bool, bool]:
    """Return text, degraded, whether the vendor was called, and whether that call failed."""
    if not circuit.allow_vendor():
        return decode_pcm(pcm), True, False, False
    try:
        text = await asr.transcribe(pcm)
    except VendorRejected as exc:
        circuit.observe(exc.status)
        return decode_pcm(pcm), True, True, True
    circuit.observe_success()
    return text, False, True, False


async def run_session(
    frames: list[Frame],
    *,
    company: str,
    purpose: str,
    asr: MockAsr | None = None,
    tts: MockTts | None = None,
    llm_mode: str = "ok",
    circuit: CircuitPort | None = None,
    tts_circuit: CircuitPort | None = None,
    llm_circuit: CircuitPort | None = None,
    script: list[dict[str, object]] | None = None,
    require_guardian: bool = False,
    llm_clock: Callable[[], float] | None = None,
    prior_silence: int = 0,
) -> Outcome:
    started = time.perf_counter()
    recognizer = asr or MockAsr()
    speaker = tts or MockTts()
    vad = EnergyVad()
    circuit = circuit or VendorCircuit()
    tts_circuit = tts_circuit or VendorCircuit()
    llm_circuit = llm_circuit or VendorCircuit()
    playback = Playback()
    opening = render_opening(company=company, purpose=purpose)
    playback.speak(opening)
    tts_down = await _speak(speaker, tts_circuit, opening)
    heard_parts: list[str] = []
    silence_frames = 0
    silence_ms = 0
    degraded = tts_down
    asr_attempts = 0
    asr_errors = 0
    for frame in frames:
        if frame.dropped:
            continue
        if frame.channel == "assistant":
            if playback.playing:
                playback.speak(decode_pcm(frame.pcm) or "assistant-audio")
                if not tts_down:
                    tts_down = await _speak(speaker, tts_circuit, decode_pcm(frame.pcm))
            else:
                playback.drop_assistant()
            continue
        if frame.channel != "caller":
            continue
        if not vad.is_speech(frame.pcm):
            silence_frames += 1
            if frame.duration_ms > 0:
                silence_ms += frame.duration_ms
            continue
        text, used_fallback, attempted, failed = await _asr_text(recognizer, circuit, frame.pcm)
        if attempted:
            asr_attempts += 1
            asr_errors += 1 if failed else 0
        degraded = degraded or used_fallback
        playback.hear(text)
        if text:
            heard_parts.append(text)
    heard = strip_injection("".join(heard_parts))
    timeout_ms = _fallback_ms(script)
    silent = silence_frames > 0 and not heard
    no_input = silent and silence_ms >= timeout_ms
    hold = silent and not no_input
    clock = llm_clock or time.perf_counter
    llm_started = clock()
    if llm_mode == "down" and not no_input and not hold:
        llm_circuit.observe(503)
    llm_down = False if no_input or hold else not llm_circuit.allow_vendor()
    llm_first_token_ms: float | None = None
    model_say = ""
    format_error = False
    confidence = 0.5
    if no_input or hold:
        intent = "invalid"
    elif llm_down:
        intent = classify_intent(heard)
        degraded = True
    else:
        parsed: dict[str, object] | None = None
        for raw in _payloads(llm_mode, heard):
            parsed = _object(raw)
            if parsed is not None:
                break
        format_error = parsed is None
        if llm_mode == "ok" and not format_error:
            llm_circuit.observe_success()
        if format_error:
            intent = "inquiry"
            degraded = True
        elif llm_mode == "hostile":
            intent = classify_intent(heard)
        else:
            model_intent = parsed.get("intent") if parsed is not None else None
            if isinstance(model_intent, str) and model_intent in _INTENTS:
                intent = model_intent
            else:
                intent = "inquiry" if llm_mode != "ok" else classify_intent(heard)
            say = parsed.get("say") if parsed is not None else None
            if llm_mode in {"priced", "colloquial"} and isinstance(say, str):
                model_say = say
        confidence = 0.5 if format_error else _confidence(parsed)
        llm_first_token_ms = (clock() - llm_started) * 1000
    script_body, script_action, mandatory = render_script(
        parse_nodes(script), intent=intent, require_guardian=require_guardian
    )
    if mandatory:
        action = script_action
        reply = opening + script_body
    else:
        action, reply = _reply(intent, opening)
    if no_input and prior_silence >= 1:
        bye = _node_text(script, "n_bye") or _BYE
        action = "hangup"
        reply = f"{opening}{bye}"
        mandatory = [bye]
    elif no_input and _CLARIFY not in reply:
        action = "speak"
        reply = f"{reply}{_CLARIFY}"
    elif format_error and _FORMAT_CLARIFY not in reply:
        reply = f"{reply}{_FORMAT_CLARIFY}"
    nodes = parse_nodes(script)
    knowledge = active_knowledge(nodes, intent=intent)
    if model_say and knowledge is not None:
        reply = f"{reply}{model_say}"
    reply = confine_to_knowledge(reply, opening, knowledge, mandatory)
    guarded, hits, guard_action = guard_assistant_text(reply, prior_hits=0)
    for line in mandatory:
        if line not in guarded:
            guarded = f"{line}{guarded}"
    if llm_down and _SCRIPT_MODE not in guarded:
        guarded = f"{opening}{_SCRIPT_MODE}{guarded.removeprefix(opening)}"
    if tts_down:
        action = "hangup"
        if _TTS_CALLBACK not in guarded:
            guarded = f"{guarded}{_TTS_CALLBACK}"
    elif guard_action == "hangup":
        action = "hangup"
    guarded = _with_filler(guarded, opening, None if llm_down else llm_first_token_ms)
    if not tts_down and _FILLER in guarded:
        playback.speak(_FILLER)
        if await _speak(speaker, tts_circuit, _FILLER):
            tts_down = True
            action = "hangup"
            if _TTS_CALLBACK not in guarded:
                guarded = f"{guarded}{_TTS_CALLBACK}"
    elapsed = (time.perf_counter() - started) * 1000
    channels = {turn["speaker"]: turn["channel"] for turn in playback.turns}
    return Outcome(
        turns=playback.turns,
        action=action,
        barged_in=playback.barged_in,
        voicemail=intent == "voicemail",
        degraded=degraded or circuit.open or tts_down or llm_down,
        latency_ms=elapsed,
        intent=intent,
        intent_grade=_GRADE.get(intent, "NA"),
        reply_text=guarded,
        heard=heard,
        redline_hits=hits,
        needs_dnd=intent == "refuse",
        circuit_open=circuit.open,
        tts_down=tts_down,
        llm_down=llm_down,
        asr_attempts=asr_attempts,
        asr_errors=asr_errors,
        llm_first_token_ms=llm_first_token_ms,
        dropped_assistant_frames=playback.dropped_assistant_frames,
        channels=channels,
        llm_format_error=format_error,
        intent_confidence=confidence,
    )
