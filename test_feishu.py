"""
规则检测测试程序 — 每 6 分钟检测一次，命中规则推送到飞书
用法: python test_feishu.py
完成后 Ctrl+C 退出
"""
import asyncio
import json
import sys
import logging
from datetime import datetime, timezone, timedelta
import httpx
from app.config import get_feishu_config, get_datasource_config
from app.datasource.factory import create_datasource
from app.datasource.base import GameDetail
from app.models import Rule
from app.database import SessionLocal, init_db
from app.rule_engine.matchers import check_rule
from app.notifier.feishu import send_notification

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))
POLL_SECONDS = 300  # 5 分钟


def load_rules():
    db = SessionLocal()
    try:
        return db.query(Rule).filter(Rule.enabled == True).all()
    finally:
        db.close()


def rule_to_detail(game, rule) -> GameDetail:
    """将 Game 转为 GameDetail 供规则引擎使用"""
    return GameDetail(
        id=game.id,
        sport_type=game.sport_type,
        home_team=game.home_team,
        away_team=game.away_team,
        status=game.status,
        current_quarter=game.current_quarter,
        home_total=game.home_total,
        away_total=game.away_total,
        home_scores=getattr(game, '_home_scores', []) or [],
        away_scores=getattr(game, '_away_scores', []) or [],
        raw_data={},
    )


async def main():
    webhook_url = get_feishu_config().get("webhook_url", "")
    if not webhook_url:
        print("❌ 请先在 config.yaml 中配置 feishu.webhook_url")
        sys.exit(1)

    init_db()
    ds_type = get_datasource_config()["type"]
    ds = create_datasource(ds_type)
    print(f"📡 数据源: {ds_type}")
    print(f"🔗 Webhook: 已配置")
    print(f"⏱️  每 {POLL_SECONDS // 60} 分钟检测一次，命中规则推飞书，Ctrl+C 退出\n")

    count = 0
    while True:
        count += 1
        now = datetime.now(CN_TZ).strftime("%H:%M:%S")

        try:
            rules = load_rules()
            result = await ds.get_today_games(force=True)
        except Exception as e:
            print(f"[{count}] [{now}] ❌ 数据获取失败: {e}")
            await asyncio.sleep(POLL_SECONDS)
            continue

        if not rules:
            print(f"[{count}] [{now}] ⚠️ 没有启用的规则，跳过")
            await asyncio.sleep(POLL_SECONDS)
            continue

        total_games = len(result["basketball"]) + len(result["football"])
        hits = 0
        for sport in ("basketball", "football"):
            for game in result[sport]:
                detail = rule_to_detail(game, None)
                for rule in rules:
                    if rule.sport_type != sport:
                        continue
                    if check_rule(detail, rule):
                        hits += 1
                        try:
                            await send_notification(rule, detail)
                            print(f"  🔔 [{rule.name}] {game.home_team} vs {game.away_team}")
                        except Exception as e:
                            print(f"  ❌ 通知发送失败: {e}")

        emoji = "🏀" if hits > 0 else "📭"
        print(f"[{count}] [{now}] {emoji} 篮球{len(result['basketball'])}场 "
              f"足球{len(result['football'])}场 规则{rules.__len__()}条 命中{hits}次")

        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 已退出")
