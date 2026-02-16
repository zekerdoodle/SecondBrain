"""
Chess tools for Second Brain.

Provides a chess game interface where Claude can play against the user.
"""

from . import chess as chess_module

from .chess import chess

__all__ = ["chess"]
