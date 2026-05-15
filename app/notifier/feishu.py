import json
import httpx
import logging
from datetime import datetime
from app.config import get_feishu_config
from app.translator import get_bilingual_team, get_bilingual_league

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
    home_disp, away_disp = get_bilingual_team(game_detail.home_team, game_detail.away_team)

    lines = [
        f"**{emoji} 比赛匹配通知**",
        f"**规则**：{rule.name}",
        f"**条件**：{params_text}",
        f"**比赛**：{home_disp} vs {away_disp}",
        f"**比分**：{quarter_text}",
    ]

    if rule.rule_type == "quarter_sequence":
        pattern = get_parity_pattern(game_detail)
        if pattern:
            lines.append(f"**模式**：{pattern}")

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


def get_parity_pattern(game_detail) -> str:
    """根据 Q1/Q2/Q3 奇偶性返回模式标签"""
    scores = game_detail.home_scores
    if len(scores) < 3:
        return ""
    q1_odd = (scores[0] % 2 == 1) and (game_detail.away_scores[0] % 2 == 1)
    q2_odd = (scores[1] % 2 == 1) and (game_detail.away_scores[1] % 2 == 1)
    q3_odd = (scores[2] % 2 == 1) and (game_detail.away_scores[2] % 2 == 1)
    if q1_odd and q2_odd and q3_odd:
        return "【单单单】Q1单 Q2单 Q3单"
    elif q1_odd and q2_odd and not q3_odd:
        return "【单单双】Q1单 Q2单 Q3双"
    else:
        parts = []
        for i, (h, a) in enumerate(zip(scores[:3], game_detail.away_scores[:3])):
            parity = "单" if (h % 2 == 1 and a % 2 == 1) else "双"
            parts.append(f"Q{i+1}{parity}({h}-{a})")
        return "Q1Q2Q3: " + " ".join(parts)
