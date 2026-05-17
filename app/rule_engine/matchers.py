import json
from app.datasource.base import GameDetail


def match_quarter_parity(game: GameDetail, params: dict) -> bool:
    """检查指定节次两队总得分的奇偶性（按总分算单双）"""
    quarters = params.get("quarters", [])
    parity = params.get("parity", "odd")
    for q in quarters:
        idx = q - 1
        if idx >= len(game.home_scores) or idx >= len(game.away_scores):
            return False
        total = game.home_scores[idx] + game.away_scores[idx]
        if parity == "odd" and total % 2 == 0:
            return False
        if parity == "even" and total % 2 == 1:
            return False
    return True


def match_total_score(game: GameDetail, params: dict) -> bool:
    """检查两队总得分"""
    op = params.get("operator", ">")
    value = params.get("value", 0)
    total = game.home_total + game.away_total
    if op == ">":
        return total > value
    elif op == ">=":
        return total >= value
    elif op == "<":
        return total < value
    elif op == "<=":
        return total <= value
    elif op == "=":
        return total == value
    return False


def match_quarter_diff(game: GameDetail, params: dict) -> bool:
    """检查单节分差"""
    q = params.get("quarter", 1) - 1
    op = params.get("operator", ">")
    value = params.get("value", 0)
    if q >= len(game.home_scores) or q >= len(game.away_scores):
        return False
    diff = abs(game.home_scores[q] - game.away_scores[q])
    if op == ">":
        return diff > value
    elif op == ">=":
        return diff >= value
    elif op == "<":
        return diff < value
    elif op == "<=":
        return diff <= value
    return False


def match_quarter_sequence(game: GameDetail, params: dict) -> bool:
    """多节次序列匹配（按两队节总分算单双），达到触发节时通知"""
    conditions = params.get("conditions", [])
    trigger_quarter = params.get("trigger_quarter", 4)

    for cond in conditions:
        q = cond["quarter"] - 1
        parity = cond.get("parity", "odd")
        if q >= len(game.home_scores) or q >= len(game.away_scores):
            return False
        total = game.home_scores[q] + game.away_scores[q]
        if parity == "odd" and total % 2 == 0:
            return False
        if parity == "even" and total % 2 == 1:
            return False

    if game.current_quarter < trigger_quarter:
        return False

    return True


MATCHERS = {
    "quarter_parity": match_quarter_parity,
    "total_score": match_total_score,
    "quarter_diff": match_quarter_diff,
    "quarter_sequence": match_quarter_sequence,
}


def check_rule(game: GameDetail, rule) -> bool:
    """对一场比赛执行一条规则匹配"""
    if game.sport_type != rule.sport_type:
        return False
    matcher = MATCHERS.get(rule.rule_type)
    if matcher is None:
        return False
    params = json.loads(rule.params)
    return matcher(game, params)
