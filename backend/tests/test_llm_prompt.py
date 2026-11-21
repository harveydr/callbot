"""Intent prompt contract: template, eight turns, and the fixed compliance line."""

from app.llm.prompt import FIRST_SAMPLE, RETRY_SAMPLE, render_intent_prompt, sample_for_attempt


def test_the_intent_prompt_ends_with_the_compliance_contract() -> None:
    history = [f"第{index}轮" for index in range(12)]
    prompt = render_intent_prompt(last_user_text="好的", node_id="n_open", asr_confidence="", history=history)
    assert prompt.endswith("不得承诺未授权内容、不得隐瞒 AI 身份、不得索取身份证号/银行卡完整号。")
    assert "被叫原话：好的" in prompt
    assert "识别置信度：未知" in prompt
    assert prompt.index("前文摘要：") < prompt.index("第4轮")
    assert "第0轮" in prompt.split("第4轮", 1)[0]
    assert "第11轮" in prompt
    assert sample_for_attempt(0) == FIRST_SAMPLE
    assert sample_for_attempt(1) == RETRY_SAMPLE
    assert FIRST_SAMPLE.temperature == 0.3
    assert RETRY_SAMPLE.temperature == 0.0
    assert FIRST_SAMPLE.top_p == 0.8
    assert FIRST_SAMPLE.max_tokens == 150


def test_a_long_history_stays_inside_the_budget() -> None:
    history = ["话" * 2000 for _ in range(12)]
    prompt = render_intent_prompt(last_user_text="好的", node_id="n_ask", asr_confidence="0.4", history=history)
    assert len(prompt) <= 6000
    assert prompt.endswith("不得索取身份证号/银行卡完整号。")
    assert "当前节点：n_ask" in prompt
    assert "识别置信度：0.4" in prompt
