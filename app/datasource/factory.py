from app.datasource.base import DataSource
from app.datasource.apisports import APISportsDataSource
from app.datasource.espn import ESPNDataSource
from app.datasource.thesportsdb import TheSportsDBDataSource
from app.datasource.hupu import HupuDataSource
from app.datasource.dongqiudi import DongqiudiDataSource
from app.datasource.leisu import LeisuDataSource


class SinaDataSource(DataSource):
    """新浪体育数据源（预留）"""
    pass


class JuheApiDataSource(DataSource):
    """聚合数据 API 数据源（预留）"""
    pass


DATA_SOURCE_MAP = {
    "apisports": APISportsDataSource,
    "thesportsdb": TheSportsDBDataSource,
    "espn": ESPNDataSource,
    "dongqiudi": DongqiudiDataSource,
    "hupu": HupuDataSource,
    "leisu": LeisuDataSource,
    "sina": SinaDataSource,
    "juhe_api": JuheApiDataSource,
}


def create_datasource(ds_type: str) -> DataSource:
    cls = DATA_SOURCE_MAP.get(ds_type)
    if cls is None:
        raise ValueError(f"Unknown datasource type: {ds_type}")
    return cls()
