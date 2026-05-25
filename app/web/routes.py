import json
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Rule, MatchHistory
from app.scheduler import update_poll_interval

CN_TZ = timezone(timedelta(hours=8))

router = APIRouter()

RULE_TYPE_LABELS = {
    "quarter_parity": "节次得分奇偶",
    "total_score": "总得分比较",
    "quarter_diff": "单节分差",
    "quarter_sequence": "多节序列匹配",
}

SPORT_LABELS = {
    "basketball": "🏀 篮球",
    "football": "⚽ 足球",
}


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    from app.config import get_datasource_config
    rules = db.query(Rule).order_by(Rule.updated_at.desc()).all()
    history = db.query(MatchHistory).order_by(MatchHistory.matched_at.desc()).limit(50).all()
    interval = get_datasource_config().get("poll_interval_seconds", 300)
    return request.app.state.templates.TemplateResponse("index.html", {
        "request": request,
        "rules": rules,
        "history": history,
        "rule_type_labels": RULE_TYPE_LABELS,
        "sport_labels": SPORT_LABELS,
        "poll_interval": interval,
    })


@router.post("/rules/add")
def add_rule(
    pattern: str = Form(default=""),
    sport_type: str = Form(default="basketball"),
    rule_type: str = Form(default="quarter_sequence"),
    enabled: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    # 将 pattern(如 "001") 转为 quarter_sequence 参数
    pattern = (pattern or "").strip()
    if len(pattern) != 3 or not pattern.isdigit():
        return RedirectResponse("/", status_code=303)
    conditions = []
    parity_map = {"0": "even", "1": "odd"}
    for i, ch in enumerate(pattern):
        if ch not in parity_map:
            return RedirectResponse("/", status_code=303)
        conditions.append({"quarter": i + 1, "parity": parity_map[ch]})
    params = json.dumps({"conditions": conditions, "trigger_quarter": 4})
    name = f"模式{pattern}"

    rule = Rule(
        name=name,
        sport_type=sport_type,
        rule_type=rule_type,
        params=params,
        enabled=enabled,
    )
    db.add(rule)
    db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/rules/{rule_id}/edit")
def edit_rule(
    rule_id: int,
    name: str = Form(...),
    sport_type: str = Form(...),
    rule_type: str = Form(...),
    params_json: str = Form(default="{}"),
    enabled: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if rule:
        rule.name = name
        rule.sport_type = sport_type
        rule.rule_type = rule_type
        rule.params = params_json
        rule.enabled = enabled
        rule.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if rule:
        rule.enabled = not rule.enabled
        rule.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(Rule).filter(Rule.id == rule_id).first()
    if rule:
        db.delete(rule)
        db.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/settings/poll-interval")
def set_poll_interval(seconds: int = Form(...)):
    update_poll_interval(seconds)
    return RedirectResponse("/", status_code=303)


def _fmt_time(raw: str) -> str:
    """提取 HH:MM，数据源已转为北京时间"""
    if not raw:
        return ""
    # raw is already "HH:MM" or "YYYY-MM-DD HH:MM:SS"
    parts = raw.strip().split(" ")
    time_part = parts[-1] if parts else raw
    return time_part[:5] if len(time_part) >= 5 else time_part


@router.get("/api/today")
async def api_today_games(request: Request, refresh: bool = False):
    """返回今日赛程 JSON。refresh=true 时跳过缓存。"""
    from app.config import get_datasource_config
    from app.datasource.factory import create_datasource

    config = get_datasource_config()
    ds = create_datasource(config["type"])
    try:
        result = await ds.get_today_games(force=refresh)
    except Exception:
        result = {"basketball": [], "football": []}

    output = {}
    from app.translator import get_team_parts, get_bilingual_league
    for sport, games in result.items():
        output[sport] = [
            {
                "id": g.id,
                "home_cn": get_team_parts(g.home_team)[0],
                "home_en": get_team_parts(g.home_team)[1] or g.home_team,
                "away_cn": get_team_parts(g.away_team)[0],
                "away_en": get_team_parts(g.away_team)[1] or g.away_team,
                "status": g.status,
                "home_total": g.home_total,
                "away_total": g.away_total,
                "start_time": _fmt_time(g.start_time),
                "start_time_full": g.start_time,
                "league": g.league,
                "league_disp": get_bilingual_league(g.league),
                "current_quarter": g.current_quarter,
                "home_scores": getattr(g, '_home_scores', []) or [],
                "away_scores": getattr(g, '_away_scores', []) or [],
            }
            for g in games
            if g.status != "已结束"
        ]
    from fastapi.responses import JSONResponse
    return JSONResponse({"datasource": config["type"], "games": output})


@router.get("/api/basketball")
async def api_basketball_games(request: Request, refresh: bool = False):
    """返回今日所有篮球比赛（含已结束），不分状态"""
    from app.config import get_datasource_config
    from app.datasource.factory import create_datasource
    from app.translator import get_team_parts, get_bilingual_league

    config = get_datasource_config()
    ds = create_datasource(config["type"])
    try:
        result = await ds.get_today_games(force=refresh)
    except Exception:
        return JSONResponse({"games": []})

    games = result.get("basketball", [])
    output = [
        {
            "id": g.id,
            "home_cn": get_team_parts(g.home_team)[0],
            "home_en": get_team_parts(g.home_team)[1] or g.home_team,
            "away_cn": get_team_parts(g.away_team)[0],
            "away_en": get_team_parts(g.away_team)[1] or g.away_team,
            "status": g.status,
            "home_total": g.home_total,
            "away_total": g.away_total,
            "start_time": _fmt_time(g.start_time),
            "start_time_full": g.start_time,
            "league": g.league,
            "league_disp": get_bilingual_league(g.league),
            "current_quarter": g.current_quarter,
            "home_scores": getattr(g, '_home_scores', []) or [],
            "away_scores": getattr(g, '_away_scores', []) or [],
        }
        for g in games
    ]
    from fastapi.responses import JSONResponse
    return JSONResponse({"datasource": config["type"], "games": output})


@router.get("/api/matches")
async def api_matched_games(request: Request, refresh: bool = False):
    from app.config import get_datasource_config
    from app.datasource.factory import create_datasource
    from app.datasource.base import GameDetail
    from app.rule_engine.matchers import check_rule
    from app.translator import get_team_parts, get_bilingual_league
    from app.database import SessionLocal
    from app.notifier.feishu import get_parity_pattern

    config = get_datasource_config()
    ds = create_datasource(config["type"])
    try:
        result = await ds.get_today_games(force=refresh)
    except Exception:
        return JSONResponse({"matches": []})

    db = SessionLocal()
    try:
        rules = db.query(Rule).filter(Rule.enabled == True).all()
    finally:
        db.close()

    matches = []
    for sport, games in result.items():
        for g in games:
            hour = _parse_hour(g.start_time)
            if hour is not None and (hour < 7 or hour > 23):
                continue
            detail = GameDetail(
                id=g.id, sport_type=g.sport_type,
                home_team=g.home_team, away_team=g.away_team,
                status=g.status, current_quarter=g.current_quarter,
                home_total=g.home_total, away_total=g.away_total,
                home_scores=getattr(g, '_home_scores', []) or [],
                away_scores=getattr(g, '_away_scores', []) or [],
                raw_data={},
            )
            for rule in rules:
                if rule.sport_type != g.sport_type:
                    continue
                if check_rule(detail, rule):
                    pattern = get_parity_pattern(detail) if rule.rule_type == "quarter_sequence" else ""
                    matches.append({
                        "rule_name": rule.name,
                        "sport": g.sport_type,
                        "home_cn": get_team_parts(g.home_team)[0],
                        "home_en": get_team_parts(g.home_team)[1] or g.home_team,
                        "away_cn": get_team_parts(g.away_team)[0],
                        "away_en": get_team_parts(g.away_team)[1] or g.away_team,
                        "status": g.status,
                        "home_total": g.home_total,
                        "away_total": g.away_total,
                        "start_time": _fmt_time(g.start_time),
                        "league": g.league,
                        "league_disp": get_bilingual_league(g.league),
                        "home_scores": detail.home_scores,
                        "away_scores": detail.away_scores,
                        "pattern": pattern,
                    })
                    break
    from fastapi.responses import JSONResponse
    return JSONResponse({"datasource": config["type"], "matches": matches})


def _parse_hour(time_str: str):
    if not time_str:
        return None
    try:
        return int(time_str.strip().split(":")[0])
    except (ValueError, IndexError):
        return None


@router.get("/api/history-matches")
async def api_history_matches(request: Request):
    """逐日检测本月1号到今天所有符合规则的比赛（用于测试 API 准确性）"""
    from app.config import get_datasource_config
    from app.datasource.factory import create_datasource
    from app.datasource.base import GameDetail
    from app.rule_engine.matchers import check_rule
    from app.translator import get_team_parts, get_bilingual_league
    from app.database import SessionLocal
    from app.notifier.feishu import get_parity_pattern

    db = SessionLocal()
    try:
        rules = db.query(Rule).filter(Rule.enabled == True).all()
    finally:
        db.close()

    if not rules:
        return JSONResponse({"matches": [], "days_scanned": 0})

    config = get_datasource_config()
    today = datetime.now(CN_TZ)
    day = datetime(today.year, today.month, 1, tzinfo=CN_TZ)
    all_matches = []
    days = 0

    while day <= today:
        date_str = day.strftime("%Y-%m-%d")
        days += 1
        try:
            ds = create_datasource(config["type"])
            # 直接调内部 fetch，不走缓存
            from app.datasource.apisports import APISportsDataSource, BB_API, FB_API
            if isinstance(ds, APISportsDataSource):
                bb_data = await ds._fetch(f"{BB_API}?date={date_str}&timezone=Asia/Shanghai")
                fb_data = await ds._fetch(f"{FB_API}?date={date_str}&timezone=Asia/Shanghai")
                bb_raw = bb_data.get("response", [])
                fb_raw = fb_data.get("response", [])
                # Parse manually
                games = []
                for g in bb_raw:
                    g = ds._parse_bb_games([g])[0] if ds._parse_bb_games([g]) else None
                    if g:
                        games.append(g)
                for f in fb_raw:
                    g = ds._parse_fb_games([f])[0] if ds._parse_fb_games([f]) else None
                    if g:
                        games.append(g)
            else:
                result = await ds.get_today_games()
                games = result.get("basketball", []) + result.get("football", [])
        except Exception:
            day += timedelta(days=1)
            continue

        for g in games:
            if g.status == "未开始" and day.date() == today.date():
                continue  # 今天的未开始比赛跳过，等开始后再看
            detail = GameDetail(
                id=g.id, sport_type=g.sport_type,
                home_team=g.home_team, away_team=g.away_team,
                status=g.status, current_quarter=g.current_quarter,
                home_total=g.home_total, away_total=g.away_total,
                home_scores=getattr(g, '_home_scores', []) or [],
                away_scores=getattr(g, '_away_scores', []) or [],
                raw_data={},
            )
            for rule in rules:
                if rule.sport_type != g.sport_type:
                    continue
                if check_rule(detail, rule):
                    pattern = get_parity_pattern(detail) if rule.rule_type == "quarter_sequence" else ""
                    all_matches.append({
                        "id": g.id,
                        "date": date_str,
                        "rule_name": rule.name,
                        "sport": g.sport_type,
                        "home_cn": get_team_parts(g.home_team)[0],
                        "home_en": get_team_parts(g.home_team)[1] or g.home_team,
                        "away_cn": get_team_parts(g.away_team)[0],
                        "away_en": get_team_parts(g.away_team)[1] or g.away_team,
                        "status": g.status,
                        "home_total": g.home_total,
                        "away_total": g.away_total,
                        "start_time": _fmt_time(g.start_time),
                        "start_time_full": g.start_time,
                        "league": g.league,
                        "league_disp": get_bilingual_league(g.league),
                        "current_quarter": g.current_quarter,
                        "home_scores": detail.home_scores,
                        "away_scores": detail.away_scores,
                        "pattern": pattern,
                    })
                    break
        day += timedelta(days=1)

    from fastapi.responses import JSONResponse
    return JSONResponse({"matches": all_matches, "days_scanned": days})


@router.post("/detect-now")
async def detect_now(db: Session = Depends(get_db)):
    """手动触发一次检测：拉取今日所有比赛，逐场匹配规则，命中推飞书"""
    from app.config import get_datasource_config
    from app.datasource.factory import create_datasource
    from app.datasource.base import GameDetail
    from app.rule_engine.matchers import check_rule
    from app.notifier.feishu import send_notification
    from app.database import SessionLocal
    from datetime import datetime

    _db = SessionLocal()
    try:
        config = get_datasource_config()
        ds = create_datasource(config["type"])
        rules = _db.query(Rule).filter(Rule.enabled == True).all()

        try:
            result = await ds.get_today_games(force=True)
        except Exception:
            return RedirectResponse("/", status_code=303)

        for sport, games in result.items():
            for g in games:
                if g.status == "已结束":
                    continue
                detail = GameDetail(
                    id=g.id, sport_type=g.sport_type,
                    home_team=g.home_team, away_team=g.away_team,
                    status=g.status, current_quarter=g.current_quarter,
                    home_total=g.home_total, away_total=g.away_total,
                    home_scores=getattr(g, '_home_scores', []) or [],
                    away_scores=getattr(g, '_away_scores', []) or [],
                    raw_data={},
                )
                for rule in rules:
                    if rule.sport_type != g.sport_type:
                        continue
                    if check_rule(detail, rule):
                        exists = _db.query(MatchHistory).filter(
                            MatchHistory.rule_id == rule.id,
                            MatchHistory.game_id == g.id,
                        ).first()
                        if not exists:
                            _db.add(MatchHistory(
                                rule_id=rule.id, game_id=g.id,
                                home_team=g.home_team, away_team=g.away_team,
                                home_score=g.home_total, away_score=g.away_total,
                                detail=json.dumps({
                                    "home_scores": detail.home_scores,
                                    "away_scores": detail.away_scores,
                                    "status": g.status,
                                }, ensure_ascii=False),
                                matched_at=datetime.utcnow(),
                            ))
                            _db.commit()
                            await send_notification(rule, detail)
    finally:
        _db.close()

    return RedirectResponse("/", status_code=303)
