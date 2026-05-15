from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from app.database import Base


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    sport_type = Column(String(20), nullable=False)  # basketball / football
    rule_type = Column(String(50), nullable=False)
    params = Column(Text, nullable=False, default="{}")  # JSON
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MatchHistory(Base):
    __tablename__ = "match_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, nullable=False)
    game_id = Column(String(50), nullable=False)
    home_team = Column(String(100), nullable=False)
    away_team = Column(String(100), nullable=False)
    home_score = Column(Integer, default=0)
    away_score = Column(Integer, default=0)
    detail = Column(Text, default="{}")
    matched_at = Column(DateTime, default=datetime.utcnow)
