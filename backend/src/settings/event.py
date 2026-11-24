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
        await chat_agent_service.start()
        price_prediction_service.warm_up()
    return start_app


def create_stop_app_handler(app: FastAPI) -> Callable:  # noqa
    @logger.catch
    async def stop_app() -> None:
        from src.search.graph_repository import graph_repository
        from src.services.agent import chat_agent_service
        graph_repository.stop()
        await chat_agent_service.stop()
        await llm_model.stop()
        minio_client.stop()
        postgres_client.stop()
    return stop_app
