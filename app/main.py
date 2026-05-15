import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from app.database import init_db
from app.scheduler import start_scheduler
from app.web.routes import router as web_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("Ball game monitor started")
    yield


app = FastAPI(title="球赛实时监控", lifespan=lifespan)

templates = Jinja2Templates(directory="app/web/templates")
app.state.templates = templates

app.include_router(web_router)
