import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.database import SessionLocal
from app.config import get_datasource_config
from app.datasource.factory import create_datasource
from app.rule_engine.engine import run_detection

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


def _get_poll_interval():
    return get_datasource_config().get("poll_interval_seconds", 60)


async def _detection_job():
    config = get_datasource_config()
    ds = create_datasource(config["type"])
    db = SessionLocal()
    try:
        await run_detection(db, ds)
    except Exception as e:
        logger.exception(f"Detection job failed: {e}")
    finally:
        db.close()


def start_scheduler():
    interval = _get_poll_interval()
    scheduler.add_job(
        _detection_job,
        "interval",
        seconds=interval,
        id="detection",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started, poll interval: {interval}s")


def update_poll_interval(seconds: int):
    scheduler.reschedule_job("detection", trigger="interval", seconds=seconds)
    import yaml, os
    import app.config as _cfg
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("datasource", {})["poll_interval_seconds"] = seconds
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    _cfg._config_cache = None  # 清缓存，下次读取重新加载
    logger.info(f"Poll interval updated to {seconds}s")
