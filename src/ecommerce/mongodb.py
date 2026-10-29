"""MongoDB connection management."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_mongo_client() -> MongoClient:
    """Create a MongoDB client using the project configuration."""
    mongo_uri = os.getenv("MONGO_URI")

    if not mongo_uri:
        raise RuntimeError("Missing environment variable: MONGO_URI")

    return MongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )


def get_database(client: MongoClient) -> Database:
    """Return the configured application database."""
    database_name = os.getenv("MONGO_DATABASE")

    if not database_name:
        raise RuntimeError("Missing environment variable: MONGO_DATABASE")

    try:
        return client[database_name]
    except ConfigurationError as error:
        raise RuntimeError("Invalid MongoDB database configuration.") from error


def verify_mongo_connection(client: MongoClient) -> bool:
    """Verify that MongoDB is reachable and credentials are accepted."""
    client.admin.command("ping")
    return True
