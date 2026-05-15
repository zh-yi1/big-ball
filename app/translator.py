import json
import logging
import httpx
from pathlib import Path

logger = logging.getLogger(__name__)

TRANSLATION_FILE = Path(__file__).parent.parent / "data" / "translations.json"
MINIMAX_URL = "https://api.minimaxi.com/anthropic/v1/messages"


def load_cache() -> dict:
    if TRANSLATION_FILE.exists():
        with open(TRANSLATION_FILE, "r") as f:
            return json.load(f)
    return {"league": {}, "team": {}}


def save_cache(cache: dict):
    TRANSLATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRANSLATION_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def tr_league(name: str) -> str:
    """翻译联赛名，优先缓存，未命中返回原名"""
    if not name:
        return name
    cache = _get_cache()
    return cache.get("league", {}).get(name, name)


def tr_team(name: str) -> str:
    """翻译队名，优先缓存，未命中返回原名"""
    if not name:
        return name
    cache = _get_cache()
    return cache.get("team", {}).get(name, name)


def get_bilingual_team(home: str, away: str) -> tuple[str, str]:
    """返回双语队名 (中文/EN, 中文/EN)"""
    cache = _get_cache()
    home_cn = cache.get("team", {}).get(home, "")
    away_cn = cache.get("team", {}).get(away, "")
    if home_cn:
        home = f"{home_cn} / {home}"
    if away_cn:
        away = f"{away_cn} / {away}"
    return home, away


def get_bilingual_league(name: str) -> str:
    """返回双语联赛名"""
    if not name:
        return name
    cache = _get_cache()
    cn = cache.get("league", {}).get(name, "")
    if cn and cn != name:
        return f"{cn} / {name}"
    return name


# 模块级缓存
_cache = None


def _get_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = load_cache()
    return _cache


def reload_cache():
    global _cache
    _cache = None


async def translate_missing(api_key: str, names: list[str], category: str = "league"):
    """批量翻译未命中的名称，结果写回缓存"""
    cache = _get_cache()
    store = cache.setdefault(category, {})
    missing = [n for n in set(names) if n and n not in store]
    if not missing:
        return

    logger.info(f"Translating {len(missing)} {category} names via MiniMax...")
    prompt = (
        f"Translate these {category} names to Simplified Chinese. "
        f"Return ONLY a JSON object mapping each original name to its Chinese translation. "
        f"If a name is already an abbreviation like NBA/WNBA/CBA, keep it as is. "
        f"Names:\n{json.dumps(missing, ensure_ascii=False)}"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                MINIMAX_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Anthropic-Version": "2023-06-01",
                },
                json={
                    "model": "MiniMax-M2.7",
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("content", [{}])[0].get("text", "")
                # Extract JSON from response
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    result = json.loads(text[start:end])
                    store.update(result)
                    save_cache(cache)
                    reload_cache()
                    logger.info(f"Translated {len(result)} names, saved to cache")
            else:
                logger.warning(f"MiniMax API error: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Translation API failed: {e}")


async def translate_all_missing(api_key: str, leagues: set[str], teams: set[str]):
    """批量翻译所有缺失的联赛名和队名"""
    await translate_missing(api_key, list(leagues), "league")
    await translate_missing(api_key, list(teams), "team")
