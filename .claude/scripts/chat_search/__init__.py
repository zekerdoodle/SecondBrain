"""
Chat Search Module

Provides hybrid keyword + semantic search over chat history.
"""

from .indexer import ChatSearchIndex, build_index, update_index
from .searcher import ChatSearcher

__all__ = [
    "ChatSearchIndex",
    "build_index",
    "update_index",
    "ChatSearcher",
]
