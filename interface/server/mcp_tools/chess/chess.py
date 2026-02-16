"""
Chess MCP tool.

Single tool with action parameter for:
- start: Start a new game, choose Claude's color
- move: Make a move in algebraic notation
- resign: Give up the current game
- cancel: End game without result
"""

import os
import json
import logging
from typing import Any, Dict
from datetime import datetime

import chess as python_chess
from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.chess")

# Game state storage directory
GAMES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/chess"))
os.makedirs(GAMES_DIR, exist_ok=True)

# Active game file (one game at a time)
ACTIVE_GAME_FILE = os.path.join(GAMES_DIR, "active_game.json")


def load_game() -> Dict[str, Any] | None:
    """Load the active game state."""
    if not os.path.exists(ACTIVE_GAME_FILE):
        return None
    try:
        with open(ACTIVE_GAME_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load game: {e}")
        return None


def save_game(game_state: Dict[str, Any]):
    """Save the active game state."""
    try:
        with open(ACTIVE_GAME_FILE, 'w') as f:
            json.dump(game_state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save game: {e}")


def delete_game():
    """Delete the active game."""
    if os.path.exists(ACTIVE_GAME_FILE):
        os.remove(ACTIVE_GAME_FILE)


def get_board_from_fen(fen: str) -> python_chess.Board:
    """Create a board from FEN string."""
    return python_chess.Board(fen)


def get_game_status(board: python_chess.Board) -> Dict[str, Any]:
    """Get the current game status."""
    status = {
        "is_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "is_game_over": board.is_game_over(),
        "turn": "white" if board.turn == python_chess.WHITE else "black",
    }

    if board.is_checkmate():
        winner = "black" if board.turn == python_chess.WHITE else "white"
        status["result"] = f"{winner} wins by checkmate"
    elif board.is_stalemate():
        status["result"] = "Draw by stalemate"
    elif board.is_insufficient_material():
        status["result"] = "Draw by insufficient material"
    elif board.is_fifty_moves():
        status["result"] = "Draw by fifty-move rule"
    elif board.is_repetition():
        status["result"] = "Draw by repetition"

    return status


def get_captured_pieces(board: python_chess.Board) -> Dict[str, list]:
    """Calculate captured pieces from board state."""
    # Starting pieces
    starting = {
        'P': 8, 'N': 2, 'B': 2, 'R': 2, 'Q': 1, 'K': 1,
        'p': 8, 'n': 2, 'b': 2, 'r': 2, 'q': 1, 'k': 1
    }

    # Count current pieces
    current = {}
    for piece in board.piece_map().values():
        symbol = piece.symbol()
        current[symbol] = current.get(symbol, 0) + 1

    # Calculate captured
    captured_by_white = []  # Black pieces captured by white
    captured_by_black = []  # White pieces captured by black

    for piece, count in starting.items():
        current_count = current.get(piece, 0)
        captured_count = count - current_count
        for _ in range(captured_count):
            if piece.isupper():  # White piece captured by black
                captured_by_black.append(piece)
            else:  # Black piece captured by white
                captured_by_white.append(piece)

    return {
        "white": captured_by_white,  # Pieces white has captured
        "black": captured_by_black   # Pieces black has captured
    }


def broadcast_board_update(game_state: Dict[str, Any]):
    """
    Signal to the WebSocket handler that a board update should be broadcast.
    This sets an environment variable that main.py can check after tool execution.
    """
    # Store the update in a temp file that main.py will read
    update_file = os.path.join(GAMES_DIR, "pending_update.json")
    try:
        with open(update_file, 'w') as f:
            json.dump(game_state, f)
    except Exception as e:
        logger.error(f"Failed to write pending update: {e}")


@register_tool("chess")
@tool(
    name="chess",
    description="""Play chess with the user. Use this tool for all chess game actions.

Actions:
- start: Start a new game. Specify which color you (Claude) want to play.
- move: Make a move using algebraic notation (e.g., "e4", "Nf3", "O-O", "exd5")
- resign: Resign the current game
- cancel: End the game without a result

IMPORTANT - UI Behavior:
- There is a visual chess board UI that shows the current position automatically
- DO NOT repeat back the position or render ASCII boards - the UI handles visualization
- Just respond naturally with your move choice and any banter/commentary
- The tool return is minimal because the board UI already updates in real-time

Examples:
- chess(action="start", color="black") - Start game, you play black (the user moves first)
- chess(action="move", move="e4") - Play e4
- chess(action="move", move="Nxf7") - Capture on f7 with knight
- chess(action="resign") - Give up""",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "move", "resign", "cancel"],
                "description": "The action to perform"
            },
            "color": {
                "type": "string",
                "enum": ["white", "black"],
                "description": "For 'start' action: which color Claude plays"
            },
            "move": {
                "type": "string",
                "description": "For 'move' action: move in algebraic notation (e.g., 'e4', 'Nf3', 'O-O')"
            }
        },
        "required": ["action"]
    }
)
async def chess(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle chess game actions."""
    action = args.get("action")

    if action == "start":
        return await handle_start(args)
    elif action == "move":
        return await handle_move(args)
    elif action == "resign":
        return await handle_resign(args)
    elif action == "cancel":
        return await handle_cancel(args)
    else:
        return {
            "content": [{"type": "text", "text": f"Unknown action: {action}"}],
            "is_error": True
        }


async def handle_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start a new chess game."""
    color = args.get("color", "white")

    # Check if game already exists
    existing = load_game()
    if existing and not existing.get("game_over"):
        return {
            "content": [{"type": "text", "text": "A game is already in progress. Use cancel to end it first, or resign to forfeit."}],
            "is_error": True
        }

    # Create new game
    board = python_chess.Board()

    game_state = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "created": datetime.now().isoformat(),
        "fen": board.fen(),
        "claude_color": color,
        "user_color": "black" if color == "white" else "white",
        "moves": [],
        "game_over": False,
        "result": None,
        "captured": {"white": [], "black": []}
    }

    save_game(game_state)
    broadcast_board_update(game_state)

    # Minimal response - UI shows the board
    return {
        "content": [{"type": "text", "text": f"Game started. Playing as {color}."}],
        "_chess_update": game_state  # Signal to WebSocket handler
    }


async def handle_move(args: Dict[str, Any]) -> Dict[str, Any]:
    """Make a chess move."""
    move_san = args.get("move", "").strip()

    if not move_san:
        return {
            "content": [{"type": "text", "text": "No move specified. Use algebraic notation like 'e4', 'Nf3', 'O-O'."}],
            "is_error": True
        }

    # Load game
    game_state = load_game()
    if not game_state:
        return {
            "content": [{"type": "text", "text": "No active game. Start one with chess(action='start', color='white'|'black')."}],
            "is_error": True
        }

    if game_state.get("game_over"):
        return {
            "content": [{"type": "text", "text": "Game is already over. Start a new game."}],
            "is_error": True
        }

    # Create board from current position
    board = get_board_from_fen(game_state["fen"])

    # Check if it's Claude's turn
    current_turn = "white" if board.turn == python_chess.WHITE else "black"
    if current_turn != game_state["claude_color"]:
        return {
            "content": [{"type": "text", "text": f"It's not your turn. Waiting for the user ({game_state['user_color']}) to move."}],
            "is_error": True
        }

    # Try to parse and validate move
    try:
        move = board.parse_san(move_san)
    except python_chess.InvalidMoveError:
        return {
            "content": [{"type": "text", "text": f"Invalid move notation: '{move_san}'. Use standard algebraic notation."}],
            "is_error": True
        }
    except python_chess.AmbiguousMoveError:
        return {
            "content": [{"type": "text", "text": f"Ambiguous move: '{move_san}'. Please specify which piece (e.g., Nbd2 or N1f3)."}],
            "is_error": True
        }

    if move not in board.legal_moves:
        return {
            "content": [{"type": "text", "text": f"Illegal move: '{move_san}'. That move is not allowed in this position."}],
            "is_error": True
        }

    # Make the move
    san = board.san(move)  # Get proper SAN before pushing
    board.push(move)

    # Update game state
    game_state["fen"] = board.fen()
    game_state["moves"].append({
        "san": san,
        "uci": move.uci(),
        "player": "claude",
        "timestamp": datetime.now().isoformat()
    })
    game_state["captured"] = get_captured_pieces(board)

    # Check game status
    status = get_game_status(board)

    if status["is_checkmate"]:
        game_state["game_over"] = True
        game_state["result"] = status["result"]
    elif status["is_stalemate"]:
        game_state["game_over"] = True
        game_state["result"] = status["result"]
    elif status["is_game_over"]:
        game_state["game_over"] = True
        game_state["result"] = status.get("result", "Game over")

    save_game(game_state)
    broadcast_board_update(game_state)

    # Minimal response - UI shows the board and move
    return {
        "content": [{"type": "text", "text": f"Played {san}."}],
        "_chess_update": game_state
    }


async def handle_resign(args: Dict[str, Any]) -> Dict[str, Any]:
    """Resign the current game."""
    game_state = load_game()
    if not game_state:
        return {
            "content": [{"type": "text", "text": "No active game to resign."}],
            "is_error": True
        }

    if game_state.get("game_over"):
        return {
            "content": [{"type": "text", "text": "Game is already over."}],
            "is_error": True
        }

    game_state["game_over"] = True
    game_state["result"] = f"the user wins - Claude resigned"

    save_game(game_state)
    broadcast_board_update(game_state)

    # Minimal response - let Claude add natural commentary
    return {
        "content": [{"type": "text", "text": "Resigned."}],
        "_chess_update": game_state
    }


async def handle_cancel(args: Dict[str, Any]) -> Dict[str, Any]:
    """Cancel the current game without result."""
    game_state = load_game()
    if not game_state:
        return {
            "content": [{"type": "text", "text": "No active game to cancel."}],
            "is_error": True
        }

    delete_game()

    # Also clean up pending update file
    update_file = os.path.join(GAMES_DIR, "pending_update.json")
    if os.path.exists(update_file):
        os.remove(update_file)

    return {
        "content": [{"type": "text", "text": "Game cancelled. No result recorded."}],
        "_chess_cancelled": True
    }


# --- Helper for the user's moves (called by WebSocket handler) ---

def make_zeke_move(move_san: str) -> Dict[str, Any]:
    """
    Make a move for the user (the user).
    Called from WebSocket handler when user makes a move on the board.
    Returns updated game state or error.
    """
    game_state = load_game()
    if not game_state:
        return {"error": "No active game"}

    if game_state.get("game_over"):
        return {"error": "Game is already over"}

    board = get_board_from_fen(game_state["fen"])

    # Check if it's the user's turn
    current_turn = "white" if board.turn == python_chess.WHITE else "black"
    if current_turn != game_state["user_color"]:
        return {"error": "It's Claude's turn"}

    # Try to parse move - could be SAN or UCI
    try:
        # Try SAN first
        move = board.parse_san(move_san)
    except:
        try:
            # Try UCI
            move = python_chess.Move.from_uci(move_san)
            if move not in board.legal_moves:
                return {"error": f"Illegal move: {move_san}"}
        except:
            return {"error": f"Invalid move: {move_san}"}

    if move not in board.legal_moves:
        return {"error": f"Illegal move: {move_san}"}

    # Get SAN before pushing
    san = board.san(move)
    board.push(move)

    # Update game state
    game_state["fen"] = board.fen()
    game_state["moves"].append({
        "san": san,
        "uci": move.uci(),
        "player": "zeke",
        "timestamp": datetime.now().isoformat()
    })
    game_state["captured"] = get_captured_pieces(board)

    # Check game status
    status = get_game_status(board)

    if status["is_checkmate"]:
        game_state["game_over"] = True
        game_state["result"] = status["result"]
    elif status["is_stalemate"] or status["is_game_over"]:
        game_state["game_over"] = True
        game_state["result"] = status.get("result", "Game over")

    save_game(game_state)
    broadcast_board_update(game_state)

    return {
        "success": True,
        "game_state": game_state,
        "status": status
    }


def get_current_game() -> Dict[str, Any] | None:
    """Get the current game state for the UI."""
    return load_game()
