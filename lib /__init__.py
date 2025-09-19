# lib/__init__.py
# This file makes the lib directory a Python package
# It can be empty or contain package-level imports

from .database import get_db_connection, execute_query
from .board_explainer import GenericBoardStyleMedicalExplainer

__all__ = [
    'get_db_connection',
    'execute_query', 
    'GenericBoardStyleMedicalExplainer'
]
