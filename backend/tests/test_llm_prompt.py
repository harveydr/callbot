"""Intent prompt contract: template, eight turns, and the fixed compliance line."""

from app.llm.prompt import FIRST_SAMPLE, RETRY_SAMPLE, render_intent_prompt, render_response_prompt, sample_for_attempt, spoken_line


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


def test_a_response_prompt_keeps_three_fragments_and_eighty_characters() -> None:
    fragments = ["只通知时间。", "请准时。", "门口集合。", "保修三年。"]
    prompt = render_response_prompt(
        company="回声",
        customer_name="张先生",
        node_desc="n_ask",
        must_say="必须播报的开场要点。",
        knowledge="".join(fragments[:3]),
        history=[f"第{index}轮" for index in range(12)],
    )
    assert "回声" in prompt
    assert "张先生" in prompt
    assert "必须播报的开场要点。" in prompt
    assert "门口集合。" in prompt
    assert "保修三年。" not in prompt
    assert prompt.endswith("不得索取身份证号/银行卡完整号。")
    assert spoken_line("「" + ("甲" * 81) + "」*") == "甲" * 80
    long = render_response_prompt(
        company="回声",
        customer_name="",
        node_desc="n_ask",
        must_say="要点",
        knowledge="知" * 3000,
        history=["话" * 2000 for _ in range(12)],
    )
    assert len(long) <= 6000
    assert long.endswith("不得索取身份证号/银行卡完整号。")


def test_a_long_history_stays_inside_the_budget() -> None:
    history = ["话" * 2000 for _ in range(12)]
    prompt = render_intent_prompt(last_user_text="好的", node_id="n_ask", asr_confidence="0.4", history=history)
    assert len(prompt) <= 6000
    assert prompt.endswith("不得索取身份证号/银行卡完整号。")
    assert "当前节点：n_ask" in prompt
    assert "识别置信度：0.4" in prompt
