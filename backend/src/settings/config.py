import logging
import os
import sys
from dotenv import dotenv_values, find_dotenv
from loguru import logger

from src.settings.logging_config import InterceptHandler
# load config from .env file
config = dotenv_values(find_dotenv())


def config_value(name, default=None):
    """Prefer process environment (Docker/CI), then values from the local .env."""
    return os.getenv(name, config.get(name, default))

DEBUG = str(config_value("DEBUG", "False")).lower() in {"true", "1", "yes"}

APPLICATION = {
    "version": config_value("VERSION", "1.0.0"),
    "project_name": config_value("PROJECT_NAME"),
    "debug": DEBUG,
    "access_token_secret_key": config_value("ACCESS_TOKEN_SECRET_KEY"),
    "algorithm": config_value("ALGORITHM"),
    "access_token_expire_minutes": config_value("ACCESS_TOKEN_EXPIRE_MINUTES"),
    "refresh_token_secret_key": config_value("REFRESH_TOKEN_SECRET_KEY"),
    "refresh_token_expire_minutes": config_value("REFRESH_TOKEN_EXPIRE_MINUTES"),
    # Minio
    "minio_end_point": config_value("MINIO_END_POINT"),
    "minio_access_key_id": config_value("MINIO_ACCESS_KEY_ID"),
    "minio_secret_access_key": config_value("MINIO_SECRET_ACCESS_KEY"),
    "minio_bucket_name": config_value("MINIO_BUCKET_NAME"),
    "minio_secure": config_value("MINIO_SECURE"),
    "gemini_api_keys": config_value("GEMINI_API_KEYS"),
    # LLM (Gemini or any Chat Completions compatible endpoint)
    "llm_provider": config_value("BACKEND_LLM_PROVIDER", "gemini"),
    "llm_model": config_value("BACKEND_LLM_MODEL"),
    "llm_base_url": config_value("BACKEND_LLM_BASE_URL"),
    "llm_api_key": (
        config_value("BACKEND_LLM_API_KEY")
        or config_value("OPENAI_API_KEY")
        or config_value("GROQ_API_KEY")
        or config_value("XAI_API_KEY")
    ),
    # Postgres
    "host_db": config_value("HOST_DB"),
    "port_db": config_value("PORT_DB"),
    "username_db": config_value("USERNAME_DB"),
    "password_db": config_value("PASSWORD_DB"),
    "database": config_value("DATABASE"),
    # Optional online graph retrieval
    "neo4j_uri": config_value("NEO4J_URI", "bolt://neo4j:7687"),
    "neo4j_user": config_value("NEO4J_USER", "neo4j"),
    "neo4j_password": config_value("NEO4J_PASSWORD"),
    "neo4j_database": config_value("NEO4J_DATABASE", "neo4j"),
    "search_use_neo4j": (
        str(config_value("SEARCH_USE_NEO4J", "false")).lower()
        in {"1", "true", "yes"}
    ),
}

# Containers only surface stderr (docker logs), so warnings and errors must reach
# it in every environment; DEBUG adds INFO for local development.
STDERR_LOG_LEVEL = "INFO" if APPLICATION["debug"] else "WARNING"
# Rotated log files to keep next to the active one; bounds disk use to ~60 MB.
LOG_FILE_RETENTION = 5

logging.getLogger().handlers = [InterceptHandler()]
logger.configure(handlers=[{"sink": sys.stderr, "level": STDERR_LOG_LEVEL}])
logger.add("./logs/app.log", rotation="10 MB", retention=LOG_FILE_RETENTION)
