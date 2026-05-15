import json
import httpx
import logging
from datetime import datetime
from app.config import get_feishu_config

logger = logging.getLogger(__name__)

EMOJI_MAP = {
    "basketball": "🏀",
    "football": "⚽",
}


async def send_notification(rule, game_detail) -> bool:
    config = get_feishu_config()
    webhook_url = config.get("webhook_url", "")
    if not webhook_url:
        logger.warning("Feishu webhook URL not configured, skipping notification")
        return False

    emoji = EMOJI_MAP.get(game_detail.sport_type, "🏟️")
    params = json.loads(rule.params)

    quarter_text = _format_quarters(game_detail)
    params_text = _format_params(rule.rule_type, params)

    lines = [
        f"**{emoji} 比赛匹配通知**",
        f"**规则**：{rule.name}",
        f"**条件**：{params_text}",
        f"**比赛**：{game_detail.home_team} vs {game_detail.away_team}",
        f"**比分**：{quarter_text}",
    ]

    if rule.rule_type == "quarter_sequence":
        category = get_q3_category(game_detail)
        if category:
            lines.append(f"**分类**：{category}")

    lines.append(f"**状态**：{game_detail.status}")
    lines.append(f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    content = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(lines)}}]

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{emoji} 比赛匹配通知",
                },
                "template": "blue",
            },
            "elements": content,
        },
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=payload)
        if resp.status_code == 200:
            logger.info(f"Feishu notification sent: {rule.name}")
            return True
        else:
            logger.error(f"Feishu notification failed: {resp.status_code} {resp.text}")
            return False


def _format_quarters(game_detail) -> str:
    parts = []
    for i, (h, a) in enumerate(zip(game_detail.home_scores, game_detail.away_scores)):
        parts.append(f"Q{i+1} {h}-{a}")
    parts.append(f"总分 {game_detail.home_total}-{game_detail.away_total}")
    return ", ".join(parts)


def _format_params(rule_type, params) -> str:
    if rule_type == "quarter_parity":
        quarters = params.get("quarters", [])
        parity = "单" if params.get("parity") == "odd" else "双"
        return f"第{'、'.join(str(q) for q in quarters)}节得分都是{parity}数"
    elif rule_type == "total_score":
        op_map = {">": "大于", ">=": "大于等于", "<": "小于", "<=": "小于等于", "=": "等于"}
        op = op_map.get(params.get("operator", ">"), ">")
        return f"两队总得分{op}{params.get('value', 0)}"
    elif rule_type == "quarter_diff":
        op_map = {">": "大于", ">=": "大于等于", "<": "小于", "<=": "小于等于"}
        op = op_map.get(params.get("operator", ">"), ">")
        return f"第{params.get('quarter', 1)}节分差{op}{params.get('value', 0)}"
    elif rule_type == "quarter_sequence":
        return _format_sequence_params(params)
    return json.dumps(params, ensure_ascii=False)


def _format_sequence_params(params) -> str:
    parts = []
    for c in params.get("conditions", []):
        parity = "单" if c.get("parity") == "odd" else "双"
        parts.append(f"Q{c['quarter']}{parity}")
    return f"{'、'.join(parts)} → Q{params.get('trigger_quarter', 4)}时通知"


def get_q3_category(game_detail) -> str:
    """根据 Q3 奇偶性返回分类标签"""
    if len(game_detail.home_scores) < 3 or len(game_detail.away_scores) < 3:
        return ""
    h_q3 = game_detail.home_scores[2]
    a_q3 = game_detail.away_scores[2]
    if h_q3 % 2 == 0 and a_q3 % 2 == 0:
        return "类别A: Q1单 Q2单 Q3双"
    elif h_q3 % 2 == 1 and a_q3 % 2 == 1:
        return "类别B: Q1单 Q2单 Q3单"
    else:
        return f"混合: Q3 {h_q3}-{a_q3}"
