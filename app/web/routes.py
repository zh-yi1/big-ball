import json
import asyncio
from datetime import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Rule, MatchHistory
from app.scheduler import update_poll_interval

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
    name: str = Form(...),
    sport_type: str = Form(...),
    rule_type: str = Form(...),
    params_json: str = Form(default="{}"),
    enabled: bool = Form(default=True),
    db: Session = Depends(get_db),
):
    try:
        json.loads(params_json)
    except json.JSONDecodeError:
        params_json = "{}"
    rule = Rule(
        name=name,
        sport_type=sport_type,
        rule_type=rule_type,
        params=params_json,
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
