from typing import Callable
from fastapi import FastAPI
from loguru import logger

from src.services.minio.minio_client import MinioClient
from src.services.llm.llm_model import LLModel
from src.services.database.postgresql.connector import PostgresConnector

from src.settings.config import APPLICATION

config = APPLICATION


minio_client = MinioClient(config=APPLICATION)
llm_model = LLModel()
postgres_client = PostgresConnector(config=config)


def create_start_app_handler(app: FastAPI) -> Callable:  # noqa
    async def start_app():
        minio_client.start()
        llm_model.start()
        postgres_client.start()
        from src.search.graph_repository import graph_repository
        from src.services.agent import chat_agent_service
        from src.services.inference.price_prediction import price_prediction_service
        graph_repository.start()
        from starlette.concurrency import run_in_threadpool
        from src.services.auth.demo_account import ensure_demo_account_at_startup
        # bcrypt and the database are blocking; the demo account is optional at startup.
        await run_in_threadpool(ensure_demo_account_at_startup)
        from src.services.listing_submission.scheduled_geocoding import scheduled_geocoding
        scheduled_geocoding.start()
        await chat_agent_service.start()
        price_prediction_service.warm_up()
    return start_app


def create_stop_app_handler(app: FastAPI) -> Callable:  # noqa
    @logger.catch
    async def stop_app() -> None:
        from src.search.graph_repository import graph_repository
        from src.services.agent import chat_agent_service
        from src.services.listing_submission.scheduled_geocoding import scheduled_geocoding
        await scheduled_geocoding.stop()
        graph_repository.stop()
        await chat_agent_service.stop()
        await llm_model.stop()
        minio_client.stop()
        postgres_client.stop()
    return stop_app
