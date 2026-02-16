import { useState, useEffect, useCallback, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess, type Square } from 'chess.js';
import { X, RotateCcw, Flag, Loader2, Minimize2, Maximize2, GripHorizontal } from 'lucide-react';
import { clsx } from 'clsx';
import { API_URL } from '../config';

interface ChessGameState {
  id: string;
  fen: string;
  claude_color: 'white' | 'black';
  user_color: 'white' | 'black';
  moves: Array<{
    san: string;
    uci: string;
    player: 'claude' | 'zeke';
    timestamp: string;
  }>;
  game_over: boolean;
  result: string | null;
  captured: {
    white: string[];
    black: string[];
  };
}

interface ChessGameProps {
  game: ChessGameState | null;
  onClose: () => void;
  onMove: (move: string) => Promise<void>;
  onNewGame: () => void;
}

// Piece symbols for captured display
const PIECE_SYMBOLS: Record<string, string> = {
  'P': '\u2659', 'N': '\u2658', 'B': '\u2657', 'R': '\u2656', 'Q': '\u2655', 'K': '\u2654',
  'p': '\u265F', 'n': '\u265E', 'b': '\u265D', 'r': '\u265C', 'q': '\u265B', 'k': '\u265A',
};

export function ChessGame({ game, onClose, onMove, onNewGame }: ChessGameProps) {
  const [chess] = useState(() => new Chess());
  const [position, setPosition] = useState('start');
  const [selectedSquare, setSelectedSquare] = useState<Square | null>(null);
  const [possibleMoves, setPossibleMoves] = useState<Square[]>([]);
  const [isMoving, setIsMoving] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);

  // Dragging state
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [modalPosition, setModalPosition] = useState<{ x: number; y: number } | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  // Handle drag start
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    if (!modalRef.current) return;
    setIsDragging(true);
    const rect = modalRef.current.getBoundingClientRect();
    setDragOffset({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top
    });
  }, []);

  // Handle drag move
  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const x = e.clientX - dragOffset.x;
      const y = e.clientY - dragOffset.y;
      // Keep within viewport bounds
      const maxX = window.innerWidth - (modalRef.current?.offsetWidth || 400);
      const maxY = window.innerHeight - (modalRef.current?.offsetHeight || 600);
      setModalPosition({
        x: Math.max(0, Math.min(x, maxX)),
        y: Math.max(0, Math.min(y, maxY))
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset]);

  // Update board when game state changes
  useEffect(() => {
    if (game?.fen) {
      chess.load(game.fen);
      setPosition(game.fen);
      setSelectedSquare(null);
      setPossibleMoves([]);
    } else {
      chess.reset();
      setPosition('start');
    }
  }, [game?.fen, chess]);

  // Determine if it's the user's turn (the user)
  const isUserTurn = game && !game.game_over &&
    ((game.user_color === 'white' && chess.turn() === 'w') ||
     (game.user_color === 'black' && chess.turn() === 'b'));

  const currentTurn = chess.turn() === 'w' ? 'White' : 'Black';

  // Get square styles for highlighting
  const getSquareStyles = useCallback(() => {
    const styles: Record<string, React.CSSProperties> = {};

    // Highlight selected square
    if (selectedSquare) {
      styles[selectedSquare] = {
        backgroundColor: 'rgba(255, 255, 0, 0.4)',
      };
    }

    // Highlight possible moves
    possibleMoves.forEach(square => {
      const piece = chess.get(square);
      styles[square] = {
        background: piece
          ? 'radial-gradient(circle, rgba(255, 0, 0, 0.4) 85%, transparent 85%)'
          : 'radial-gradient(circle, rgba(0, 255, 0, 0.4) 25%, transparent 25%)',
      };
    });

    // Highlight check
    if (chess.isCheck()) {
      const kingSquare = findKingSquare(chess.turn());
      if (kingSquare) {
        styles[kingSquare] = {
          backgroundColor: 'rgba(255, 0, 0, 0.5)',
        };
      }
    }

    return styles;
  }, [selectedSquare, possibleMoves, chess]);

  const findKingSquare = (color: 'w' | 'b'): Square | null => {
    const board = chess.board();
    for (let row = 0; row < 8; row++) {
      for (let col = 0; col < 8; col++) {
        const piece = board[row][col];
        if (piece && piece.type === 'k' && piece.color === color) {
          const files = 'abcdefgh';
          return `${files[col]}${8 - row}` as Square;
        }
      }
    }
    return null;
  };

  const handleSquareClick = useCallback((square: Square) => {
    if (!game || game.game_over || !isUserTurn || isMoving) return;

    const piece = chess.get(square);
    const isOwnPiece = piece &&
      ((game.user_color === 'white' && piece.color === 'w') ||
       (game.user_color === 'black' && piece.color === 'b'));

    // If clicking on own piece, select it
    if (isOwnPiece) {
      setSelectedSquare(square);
      // Get possible moves for this piece
      const moves = chess.moves({ square, verbose: true });
      setPossibleMoves(moves.map(m => m.to as Square));
      return;
    }

    // If we have a selected piece and click elsewhere, try to move
    if (selectedSquare) {
      const moveStr = `${selectedSquare}${square}`;
      handleMove(moveStr);
      setSelectedSquare(null);
      setPossibleMoves([]);
    }
  }, [game, isUserTurn, selectedSquare, chess, isMoving]);

  const handleMove = async (move: string) => {
    setIsMoving(true);
    try {
      await onMove(move);
    } finally {
      setIsMoving(false);
    }
  };

  const onDrop = useCallback((sourceSquare: Square, targetSquare: Square) => {
    if (!game || game.game_over || !isUserTurn || isMoving) return false;

    // Validate it's the user's piece
    const piece = chess.get(sourceSquare);
    if (!piece) return false;

    const isOwnPiece =
      (game.user_color === 'white' && piece.color === 'w') ||
      (game.user_color === 'black' && piece.color === 'b');

    if (!isOwnPiece) return false;

    // Check if move is legal
    const possibleMoves = chess.moves({ square: sourceSquare, verbose: true });
    const isLegal = possibleMoves.some(m => m.to === targetSquare);

    if (!isLegal) return false;

    // Make the move via API
    const moveStr = `${sourceSquare}${targetSquare}`;
    handleMove(moveStr);

    return true;
  }, [game, isUserTurn, chess, isMoving]);

  // Render captured pieces
  const renderCaptured = (pieces: string[]) => {
    if (!pieces.length) return <span className="text-text-secondary">-</span>;
    return pieces.map((p, i) => (
      <span key={i} className="text-lg">{PIECE_SYMBOLS[p]}</span>
    ));
  };

  // Get status message
  const getStatus = () => {
    if (!game) return 'No game active';
    if (game.game_over) return game.result || 'Game over';
    if (chess.isCheck()) return `${currentTurn} is in check!`;
    return `${currentTurn} to move`;
  };

  // Minimized view - just a small floating indicator
  if (isMinimized) {
    return (
      <div
        ref={modalRef}
        className="fixed z-50 bg-bg-primary rounded-lg shadow-xl border border-border-color cursor-move select-none"
        style={{
          left: modalPosition?.x ?? 'auto',
          top: modalPosition?.y ?? 'auto',
          right: modalPosition ? 'auto' : '1rem',
          bottom: modalPosition ? 'auto' : '1rem',
        }}
        onMouseDown={handleDragStart}
      >
        <div className="flex items-center gap-2 px-3 py-2">
          <GripHorizontal className="w-4 h-4 text-text-muted" />
          <span className="text-sm font-medium text-text-primary">Chess</span>
          {game && !game.game_over && (
            <span className={clsx(
              "w-2 h-2 rounded-full",
              chess.turn() === (game.user_color === 'white' ? 'w' : 'b')
                ? "bg-green-500 animate-pulse"
                : "bg-amber-500"
            )} />
          )}
          <button
            onClick={() => setIsMinimized(false)}
            className="p-1 hover:bg-bg-tertiary rounded ml-1"
            title="Expand"
          >
            <Maximize2 className="w-4 h-4 text-text-secondary" />
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bg-tertiary rounded"
            title="Close"
          >
            <X className="w-4 h-4 text-text-secondary" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={modalRef}
      className={clsx(
        "fixed z-50 bg-bg-primary rounded-lg shadow-xl max-w-lg w-full max-h-[95vh] flex flex-col border border-border-color",
        isDragging && "cursor-grabbing"
      )}
      style={{
        left: modalPosition?.x ?? '50%',
        top: modalPosition?.y ?? '50%',
        transform: modalPosition ? 'none' : 'translate(-50%, -50%)',
        width: 'min(28rem, calc(100vw - 2rem))',
      }}
    >
      {/* Header - Draggable */}
      <div
        className={clsx(
          "flex items-center justify-between p-4 border-b border-border-color cursor-grab select-none",
          isDragging && "cursor-grabbing"
        )}
        onMouseDown={handleDragStart}
      >
        <div className="flex items-center gap-2">
          <GripHorizontal className="w-4 h-4 text-text-muted" />
          <h2 className="text-lg font-semibold text-text-primary">
            Chess vs Claude
          </h2>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setIsMinimized(true)}
            className="p-1 hover:bg-bg-tertiary rounded"
            title="Minimize"
          >
            <Minimize2 className="w-5 h-5 text-text-secondary" />
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bg-tertiary rounded"
            title="Close"
          >
            <X className="w-5 h-5 text-text-secondary" />
          </button>
        </div>
      </div>

        {/* Game content */}
        <div className="flex-1 overflow-auto p-4">
          {game ? (
            <>
              {/* Status bar */}
              <div className={clsx(
                "text-center py-2 px-4 rounded-lg mb-4 font-medium",
                game.game_over ? "bg-accent-primary/20 text-accent-primary" :
                chess.isCheck() ? "bg-red-500/20 text-red-400" :
                isUserTurn ? "bg-green-500/20 text-green-400" :
                "bg-bg-tertiary text-text-secondary"
              )}>
                {getStatus()}
                {isMoving && <Loader2 className="inline-block w-4 h-4 ml-2 animate-spin" />}
              </div>

              {/* Captured pieces - Claude's captures */}
              <div className="flex items-center gap-2 mb-2 text-sm">
                <span className="text-text-secondary">Claude captured:</span>
                <div className="flex gap-0.5">
                  {renderCaptured(game.captured[game.claude_color])}
                </div>
              </div>

              {/* Chess board */}
              <div ref={boardRef} className="aspect-square w-full max-w-md mx-auto">
                <Chessboard
                  position={position}
                  onSquareClick={handleSquareClick}
                  onPieceDrop={onDrop}
                  boardOrientation={game.user_color}
                  customSquareStyles={getSquareStyles()}
                  arePiecesDraggable={!!isUserTurn && !isMoving}
                  customBoardStyle={{
                    borderRadius: '4px',
                    boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
                  }}
                  customDarkSquareStyle={{ backgroundColor: '#4a5568' }}
                  customLightSquareStyle={{ backgroundColor: '#a0aec0' }}
                />
              </div>

              {/* Captured pieces - the user's captures */}
              <div className="flex items-center gap-2 mt-2 text-sm">
                <span className="text-text-secondary">You captured:</span>
                <div className="flex gap-0.5">
                  {renderCaptured(game.captured[game.user_color])}
                </div>
              </div>

              {/* Move history */}
              {game.moves.length > 0 && (
                <div className="mt-4">
                  <h3 className="text-sm font-medium text-text-secondary mb-2">Moves:</h3>
                  <div className="flex flex-wrap gap-1 text-sm text-text-primary bg-bg-secondary p-2 rounded max-h-24 overflow-auto">
                    {game.moves.map((m, i) => (
                      <span key={i} className={clsx(
                        "px-1 rounded",
                        m.player === 'claude' ? 'bg-accent-primary/20' : 'bg-green-500/20'
                      )}>
                        {Math.floor(i / 2) + 1}{i % 2 === 0 ? '.' : '...'}{m.san}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Your color indicator */}
              <div className="mt-4 text-center text-sm text-text-secondary">
                You are playing as <span className="font-medium text-text-primary">{game.user_color}</span>
              </div>
            </>
          ) : (
            <div className="text-center py-8">
              <p className="text-text-secondary mb-4">No active game.</p>
              <p className="text-sm text-text-secondary">
                Ask Claude to start a chess game!
              </p>
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div className="flex gap-2 p-4 border-t border-border-color">
          {game && !game.game_over && (
            <button
              onClick={() => {
                // This will need to be handled by the chat
                // For now, just close and let user type "I resign"
                onClose();
              }}
              className="flex items-center gap-2 px-4 py-2 bg-red-500/20 text-red-400 rounded hover:bg-red-500/30"
            >
              <Flag className="w-4 h-4" />
              Resign
            </button>
          )}
          <button
            onClick={onNewGame}
            className="flex items-center gap-2 px-4 py-2 bg-accent-primary/20 text-accent-primary rounded hover:bg-accent-primary/30"
          >
            <RotateCcw className="w-4 h-4" />
            New Game
          </button>
          <button
            onClick={onClose}
            className="ml-auto px-4 py-2 bg-bg-tertiary text-text-primary rounded hover:bg-bg-secondary"
          >
            Close
          </button>
        </div>
    </div>
  );
}

// Hook for managing chess game state (receives updates from useClaude callback)
export function useChessGame() {
  const [game, setGame] = useState<ChessGameState | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  // Track if user manually closed the modal (don't auto-reopen)
  const userClosedRef = useRef(false);

  // NOTE: We intentionally do NOT fetch the game on mount.
  // The chess icon should only appear when a game is started/updated in the current session.
  // Game state is set via WebSocket updates (onChessUpdate callback).

  // Update game from WebSocket callback (called by Chat via onChessUpdate)
  const updateGame = useCallback((newGame: ChessGameState) => {
    const wasNull = !game;
    const isNewGame = newGame && (!game || newGame.id !== game?.id);

    setGame(newGame);

    // Auto-open the game panel when:
    // 1. A new game starts (wasn't open before OR different game ID)
    // 2. User hasn't manually closed it
    if (newGame && isNewGame) {
      userClosedRef.current = false; // Reset manual close flag for new games
      setIsOpen(true);
    } else if (newGame && !newGame.game_over && !userClosedRef.current && wasNull) {
      // Also open if game just became available and user hasn't closed
      setIsOpen(true);
    }
    // Note: Modal stays open during game, including when game ends
    // User must explicitly close it
  }, [game]);

  const makeMove = useCallback(async (move: string) => {
    const response = await fetch(`${API_URL}/chess/move`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ move }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Invalid move');
    }

    const data = await response.json();
    setGame(data.game);

    // If it's Claude's turn, return the prompt to inject
    return data.claude_prompt;
  }, []);

  const openGame = useCallback(() => {
    userClosedRef.current = false;
    setIsOpen(true);
  }, []);

  const closeGame = useCallback(() => {
    userClosedRef.current = true;
    setIsOpen(false);
  }, []);

  const startNewGame = useCallback(() => {
    // Close current game UI - user should ask Claude to start a new game
    userClosedRef.current = false; // Allow new game to auto-open
    setIsOpen(false);
  }, []);

  // Reset game state (called when switching chats)
  const resetGame = useCallback(() => {
    setGame(null);
    setIsOpen(false);
    userClosedRef.current = false;
  }, []);

  return {
    game,
    isOpen,
    openGame,
    closeGame,
    makeMove,
    startNewGame,
    updateGame,
    resetGame,
  };
}
