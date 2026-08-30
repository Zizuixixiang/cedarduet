import { Board, PlayerMove, SwapMove } from './Board';
import { Piece, PieceRank } from './Piece';
import { ClientPieceView, FilteredBoardState, GameStateDTO, GameStatus, PlayerSession, PlayerStatus } from '../types';

/*
 * The Game object
 */

export class Game {
    status: GameStatus;
    activePlayer: PlayerStatus | null;
    players: PlayerStatus[];
    board: Board;
    capturedPieces: Piece[];
    validMoves: PlayerMove[];
    validSwap: SwapMove[];
    lastMove: PlayerMove | null;
    modifiedOn: number;
    colorMap: { [key: string]: number };

    /**
     * Create new game and initialize
     */
    constructor(params: PlayerSession) {
        // pending/ongoing/defeat/forfeit
        this.status = GameStatus.PENDING;

        this.activePlayer = null;

        this.players = [
            { color: null, name: null, joined: false, isSetup: false, flagCaptured: false, hasCommander: true, hasMoveablePieces: true, forfeited: false },
            { color: null, name: null, joined: false, isSetup: false, flagCaptured: false, hasCommander: true, hasMoveablePieces: true, forfeited: false }
        ];

        this.board = new Board();

        this.capturedPieces = [];

        this.validMoves = this.board.getMovesForPlayer('blue');

        this.validSwap = this.board.getSwapForAll();

        this.lastMove = null;

        this.modifiedOn = Date.now();

        this.colorMap = {};

        // Set player colors
        // params.playerColor is the color of the player who created the game
        if (params.playerColor === 'red') {
            this.players[0].color = 'red';
            this.players[1].color = 'blue';

            this.colorMap = { 'r': 0, 'b': 1 };
        } else if (params.playerColor === 'blue') {
            this.players[0].color = 'blue';
            this.players[1].color = 'red';

            this.colorMap = { 'b': 0, 'r': 1 };
        }
    }

    /**
     * Get the current game state as a plain object for data transfer
     * @param playerColor - The color of the player requesting the state (for filtering)
     */
    getState(playerColor?: string): GameStateDTO {
        const baseState: GameStateDTO = {
            status: this.status,
            activePlayer: this.activePlayer,
            players: this.players,
            capturedPieces: playerColor ?
                this.capturedPieces.filter(p => p.colorChar === playerColor) :
                this.capturedPieces,
            validMoves: this.validMoves,
            validSwap: this.validSwap,
            lastMove: this.lastMove,
            modifiedOn: this.modifiedOn,
            colorMap: this.colorMap,
            // No playerColor means an unfiltered admin/spectator view
            board: playerColor ? this.getFilteredBoardState(playerColor) : this.board.boardState
        };

        return baseState;
    }

    /**
     * Get filtered board state for a specific player
     * Hides opponent piece ranks to maintain game security
     */
    private getFilteredBoardState(playerColor: string): FilteredBoardState {
        const filteredState: FilteredBoardState = {};

        Object.keys(this.board.boardState).forEach(square => {
            const piece = this.board.boardState[square];
            if (!piece) {
                filteredState[square] = null; // Empty square
            } else if (piece.colorChar === playerColor[0]) {
                // Own piece - show full information
                filteredState[square] = {
                    colorChar: piece.colorChar,
                    rank: piece.rank
                };
            } else {
                // Opponent piece - hide the rank for security!

                // Find opponent player
                const opponentIndex = this.colorMap[piece.colorChar];
                const opponent = this.players[opponentIndex];

                // Exception: reveal enemy flag if commander is dead
                const shouldRevealFlag = piece.getRank() === PieceRank.FLAG && opponent && !opponent.hasCommander;

                const view: ClientPieceView = shouldRevealFlag
                    ? { colorChar: piece.colorChar, rank: piece.rank }
                    : { colorChar: piece.colorChar, rank: 'hidden' }; // Don't expose actual rank!

                filteredState[square] = view;
            }
        });
        return filteredState;
    }

    /**
     * Add player to game, and after both players have joined activate the game.
     * Returns true on success and false on failure.
     */
    addPlayer(playerData: PlayerSession): boolean {
        // Check for an open spot
        const p = this.players.find(player => player.color === playerData.playerColor && !player.joined);
        if (!p) { return false; }

        // Set player info
        p.name = playerData.playerName;
        p.joined = true;

        this.modifiedOn = Date.now();

        return true;
    }

    /**
     * Remove player from game
     */
    removePlayer(playerData: PlayerSession): boolean {
        // Find player in question
        const p = this.players.find(player => player.color === playerData.playerColor);
        if (!p) { return false; }

        // Check if player is actually joined
        if (!p.joined) { return false; }

        // Set player info
        p.joined = false;
        p.name = null;

        this.modifiedOn = Date.now();

        return true;
    }

    /*
    Finalize the setup
    */
    finishSetup(playerData: PlayerSession): boolean {
        const DEFAULT_PLAYER_COLOR = 'blue';
        // Find player in question
        const p = this.players.find(player => player.color === playerData.playerColor);
        if (!p) { return false; }

        // Check if player is actually joined
        if (!p.joined) { return false; }

        // Set player info
        p.isSetup = true;

        // If both players have joined and finish setting up, start the game
        if (
            this.players[0].joined &&
            this.players[0].isSetup &&
            this.players[1].joined &&
            this.players[1].isSetup &&
            this.status === GameStatus.PENDING
        ) {
            this.activePlayer = this.players.find(player => player.color === DEFAULT_PLAYER_COLOR) || null;
            // Generate valid moves based on latest board configuration
            this.validMoves = this.board.getMovesForPlayer(DEFAULT_PLAYER_COLOR);
            this.validSwap = [];
            this.status = GameStatus.ONGOING;
        }

        this.modifiedOn = Date.now();

        return true;
    }

    /**
     * Randomly shuffle a player's own pieces during the setup phase
     */
    shuffleSetup(playerData: PlayerSession): boolean {
        // Find player in question
        const p = this.players.find(player => player.color === playerData.playerColor);
        if (!p) { return false; }

        // Check if player is actually joined
        if (!p.joined) { return false; }

        // Can't reshuffle after setup is finalized
        if (p.isSetup || this.status !== GameStatus.PENDING) { return false; }

        this.board.shufflePlayerPieces(p.color!);

        // Regenerate valid swaps based on the new board configuration
        this.validSwap = this.board.getSwapForAll();

        this.modifiedOn = Date.now();

        return true;
    }

    /**
     * Swap pieces during setup phase
     */
    swapPieces(moveString: string): boolean {
        // Test if swap is valid
        const validSwap = this.validSwap.find(swap =>
            JSON.stringify(swap) === JSON.stringify(this.parseMoveString(moveString))
        );
        if (!validSwap) {
            return false;
        }

        // Apply swap
        const piece1: Piece = this.board.getPieceAtSquare(validSwap.startSquare)!;
        const piece2: Piece = this.board.getPieceAtSquare(validSwap.endSquare)!;

        this.board.placePieceAtSquare(validSwap.startSquare, piece2);
        this.board.placePieceAtSquare(validSwap.endSquare, piece1);

        // Regenerate valid swaps
        this.validSwap = this.board.getSwapForAll();

        this.modifiedOn = Date.now();

        return true;
    }

    evaluateMoveAndModifyBoard(validMove: PlayerMove): boolean {
        // Evaluation is only required for attacks
        // since each unit's rank is hidden from the other player
        let evaluatedMove = validMove;
        if (validMove.type === "attack") {
            const updatedEvaluatedMove = this.board.evaluateMove(validMove.startSquare, validMove.endSquare);
            if (!updatedEvaluatedMove) {
                return false;
            }
            evaluatedMove = updatedEvaluatedMove;
        }

        // Apply move
        const selectedPiece: Piece = this.board.getPieceAtSquare(evaluatedMove.startSquare)!;
        switch (evaluatedMove.type) {
            case 'move':
                this.board.placePieceAtSquare(evaluatedMove.endSquare, selectedPiece);
                this.board.setSquareEmpty(evaluatedMove.startSquare);
                break;

            case 'capture':
                this.capturedPieces.push(this.board.getPieceAtSquare(evaluatedMove.endSquare)!);
                this.board.setSquareEmpty(evaluatedMove.endSquare);

                this.board.placePieceAtSquare(evaluatedMove.endSquare, selectedPiece);
                this.board.setSquareEmpty(evaluatedMove.startSquare);
                break;

            case 'dies':
                this.board.setSquareEmpty(evaluatedMove.startSquare);
                break;

            case 'equal':
                this.capturedPieces.push(this.board.getPieceAtSquare(evaluatedMove.endSquare)!);
                this.board.setSquareEmpty(evaluatedMove.startSquare);
                this.board.setSquareEmpty(evaluatedMove.endSquare);
                break;

            default: break;
        };

        return true;
    }

    /**
     * Apply move and regenerate game state.
     * Returns true on success and false on failure.
     */
    move(moveString: string): boolean {
        if (this.status === GameStatus.PENDING) {
            return this.swapPieces(moveString);
        }

        // player can't swap units during the game
        this.validSwap = [];

        // Test if move is valid
        const validMove = this.validMoves.find(move =>
            JSON.stringify(move) === JSON.stringify(this.parseMoveString(moveString))
        );
        if (!validMove) {
            return false;
        }

        const moveMade = this.evaluateMoveAndModifyBoard(validMove!);
        if (!moveMade) {
            return false;
        }

        // Set this move as last move
        this.lastMove = validMove;

        // Get inactive player
        const inactivePlayer = this.players.find(p => p !== this.activePlayer);

        // Regenerate valid moves
        if (inactivePlayer && inactivePlayer.color) {
            this.validMoves = this.board.getMovesForPlayer(inactivePlayer.color);
        }

        // Set flag-captured status for both players
        this.players.forEach((p: PlayerStatus) => {
            if (p.color) {
                p.flagCaptured = this.board.isPlayerFlagCaptured(p.color);
                p.hasCommander = this.board.isCommanderAlive(p.color);
            }
        });

        // Test for flag capture or no moveable pieces remaining
        if (inactivePlayer && this.validMoves.length === 0) {
            inactivePlayer.hasMoveablePieces = false;
        }

        if (inactivePlayer && inactivePlayer.flagCaptured) {
            this.status = GameStatus.FLAG_CAPTURED;
        }

        if (inactivePlayer && inactivePlayer.hasMoveablePieces === false) {
            this.status = GameStatus.NO_PIECES;
        }

        // Toggle active player
        if (this.status === GameStatus.ONGOING && inactivePlayer) {
            this.activePlayer = inactivePlayer;
        }

        this.modifiedOn = Date.now();

        return true;
    }

    /**
     * Forfeit the game
     */
    forfeit(playerData: PlayerSession): boolean {
        // Find player in question
        const p = this.players.find(player => player.color === playerData.playerColor);
        if (!p) { return false; }

        // Check if player is actually joined
        if (!p.joined) { return false; }

        // Set player info
        p.forfeited = true;

        // Set game status
        this.status = GameStatus.FORFEIT;

        this.modifiedOn = Date.now();

        return true;
    }

    /**
     * Parse a move string and convert it to an object.
     * Returns the move object on success or null on failure.
     */
    parseMoveString(moveString: string): PlayerMove | null {
        // format: "<startSquare> <command> <endSquare>"
        const moveStrArr = moveString.split(" ");

        const startSquare = moveStrArr[0];
        const command = moveStrArr[1];
        const endSquare = moveStrArr[2];

        // Move to empty square
        if (command === '-') {
            return {
                type: 'move',
                startSquare: startSquare,
                endSquare: endSquare
            }
        }
        // Attack the piece
        // The result is unknown until the unit ranks are compared
        else if (command === 'x') {
            return {
                type: 'attack',
                startSquare: startSquare,
                endSquare: endSquare
            }
        }
        //swapping pieces (only allowed during setup)
        else if (command === 's') {
            return {
                type: 'swap',
                startSquare: startSquare,
                endSquare: endSquare
            }
        } else {
            return null;
        }
    };
}
