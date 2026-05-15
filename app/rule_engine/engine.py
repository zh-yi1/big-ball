import json
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Rule, MatchHistory
from app.datasource.base import DataSource
from app.rule_engine.matchers import check_rule
from app.notifier.feishu import send_notification

logger = logging.getLogger(__name__)


async def run_detection(db: Session, datasource: DataSource):
    """执行一轮检测"""
    rules = db.query(Rule).filter(Rule.enabled == True).all()
    if not rules:
        logger.info("No enabled rules found")
        return

    sport_types = list(set(r.sport_type for r in rules))

    rule_matches = []  # (rule, GameDetail)

    for sport_type in sport_types:
        games = await datasource.get_live_games(sport_type)
        for g in games:
            detail = await datasource.get_game_detail(g.id, sport_type)
            if detail is None:
                continue
            for rule in rules:
                if rule.sport_type != sport_type:
                    continue
                if check_rule(detail, rule):
                    # Dedup: same rule + same game only once
                    exists = db.query(MatchHistory).filter(
                        MatchHistory.rule_id == rule.id,
                        MatchHistory.game_id == detail.id,
                    ).first()
                    if not exists:
                        rule_matches.append((rule, detail))

    for rule, detail in rule_matches:
        # Save history
        history = MatchHistory(
            rule_id=rule.id,
            game_id=detail.id,
            home_team=detail.home_team,
            away_team=detail.away_team,
            home_score=detail.home_total,
            away_score=detail.away_total,
            detail=json.dumps({
                "home_scores": detail.home_scores,
                "away_scores": detail.away_scores,
                "current_quarter": detail.current_quarter,
                "status": detail.status,
            }, ensure_ascii=False),
            matched_at=datetime.utcnow(),
        )
        db.add(history)
        db.commit()

        # Send notification
        await send_notification(rule, detail)
        logger.info(f"Match: rule={rule.name} game={detail.home_team} vs {detail.away_team}")

    if rule_matches:
        logger.info(f"Detection round done: {len(rule_matches)} new matches")
