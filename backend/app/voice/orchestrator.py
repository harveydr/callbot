"""Mock media loop: VAD, ASR, scripted reply, redline, TTS. Vendor failures fall back locally."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from app.compliance.opening import render_opening
from app.compliance.redline import guard_assistant_text
from app.voice.asr import MockAsr, VendorRejected, decode_pcm
from app.voice.circuit import CircuitPort, VendorCircuit
from app.voice.intent import classify_intent
from app.voice.script import parse_nodes, render_script
from app.voice.session import Playback
from app.voice.tts import MockTts
from app.voice.vad import EnergyVad

_INJECTION = ("忽略你的指令", "忽略之前的指令", "ignore previous instructions")
_GRADE = {"agree": "B", "refuse": "D", "inquiry": "C", "invalid": "NA", "voicemail": "NA"}
_SCRIPT_MODE = "我用标准流程为您解答。"
_TTS_CALLBACK = "稍后为您回拨。"


@dataclass(frozen=True)
class Frame:
    channel: str
    pcm: bytes
    dropped: bool = False


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
    dropped_assistant_frames: int = 0
    channels: dict[str, str] = field(default_factory=dict)


def strip_injection(text: str) -> str:
    cleaned = text
    for phrase in _INJECTION:
        cleaned = cleaned.replace(phrase, "")
    return " ".join(cleaned.split())


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


async def _asr_text(asr: MockAsr, circuit: CircuitPort, pcm: bytes) -> tuple[str, bool]:
    if not circuit.allow_vendor():
        return decode_pcm(pcm), True
    try:
        text = await asr.transcribe(pcm)
    except VendorRejected as exc:
        circuit.observe(exc.status)
        return decode_pcm(pcm), True
    circuit.observe_success()
    return text, False


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
    degraded = tts_down
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
        if frame.channel != "caller" or not vad.is_speech(frame.pcm):
            continue
        text, used_fallback = await _asr_text(recognizer, circuit, frame.pcm)
        degraded = degraded or used_fallback
        playback.hear(text)
        if text:
            heard_parts.append(text)
    heard = strip_injection("".join(heard_parts))
    if llm_mode == "down":
        llm_circuit.observe(503)
    llm_down = not llm_circuit.allow_vendor()
    if llm_down:
        intent = classify_intent(heard)
        degraded = True
    else:
        if llm_mode == "bad_json":
            raw = "NOT_JSON"
            degraded = True
        elif llm_mode == "hostile":
            raw = json.dumps({"intent": "agree", "say": "我们保证收益", "skip_compliance": True}, ensure_ascii=False)
        else:
            raw = json.dumps({"intent": classify_intent(heard)}, ensure_ascii=False)
            llm_circuit.observe_success()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
            degraded = True
        model_intent = parsed.get("intent") if isinstance(parsed, dict) else None
        intent = model_intent if isinstance(model_intent, str) and llm_mode == "ok" else classify_intent(heard)
        if llm_mode == "hostile":
            intent = classify_intent(heard)
    script_body, script_action, mandatory = render_script(parse_nodes(script), intent=intent)
    if mandatory:
        action = script_action
        reply = opening + script_body
    else:
        action, reply = _reply(intent, opening)
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
        dropped_assistant_frames=playback.dropped_assistant_frames,
        channels=channels,
    )
