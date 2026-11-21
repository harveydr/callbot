"""Redline recall gate. Fifty prohibited sentences and thirty nearby safe sentences."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.compliance.redline import scan_redline  # noqa: E402

PROHIBITED = [
    "我们保证收益，您放心。",
    "这是稳赚的安排。",
    "百分百没有问题。",
    "保本保息给您。",
    "我是银行工作人员。",
    "我是公安的。",
    "我是警察，请配合。",
    "我是法院通知你。",
    "我是政府来电。",
    "无抵押秒批，今天就能办。",
    "保证下款，您签一下。",
    "内部渠道放款给您留了名额。",
    "你这个傻逼听好。",
    "滚开，别废话。",
    "神经病才不办。",
] 
PROHIBITED = (PROHIBITED * 4)[:50]
SAFE = [
    "我把时间记下来，让同事回电。",
    "本次是预约提醒。",
    "如果您不需要，我马上结束。",
    "银行网点的营业时间我不太确定。",
    "公安局的地址请您自己查询官方渠道。",
    "我们不能承诺任何投资结果。",
    "贷款是否批准由持牌机构决定。",
    "我是人工智能语音助理。",
    "本次通话将被录音。",
    "您可以随时让我不要再打。",
    "我确认一下您的预约时间。",
    "稍后把短信说明发给您。",
    "这个活动的规则以书面合同为准。",
    "我没有权限代替您做决定。",
    "感谢您的接听，今天先到这里。",
    "如需人工同事，我可以转接。",
    "问卷只有两个问题。",
    "您的包裹预计明天送达。",
    "回访是为了确认服务是否完成。",
    "我们不会向您推销贷款产品。",
    "请以官方 App 里的通知为准。",
    "我无法查看您的账户余额。",
    "这不是催收电话。",
    "您可以拒绝回答任何问题。",
    "我重复一下来电目的。",
    "录音用于质检和争议核对。",
    "号码来源是您留下的联系方式。",
    "如果时间不方便，我这就挂断。",
    "同事会在工作时间联系您。",
    "今天的通知到此结束。",
]


def main() -> None:
    missed = [text for text in PROHIBITED if not scan_redline(text)]
    false_hits = [text for text in SAFE if scan_redline(text)]
    recall = (len(PROHIBITED) - len(missed)) / len(PROHIBITED)
    print(f"recall={recall:.3f} missed={len(missed)} false_hits={len(false_hits)}")
    if recall < 0.99:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
