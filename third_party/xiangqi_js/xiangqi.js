/* @license
 * Copyright (c) 2019-2024, lengyanyu258 <lengyanyu258@outlook.com>
 * Released under the BSD-2-Clause license
 * https://github.com/lengyanyu258/xiangqi.js/blob/master/LICENSE
 */

'use strict';

const Xiangqi = function (fen) {
  const BLACK = 'b';
  const RED = 'r';

  const EMPTY = -1;

  const PAWN = 'p';
  const CANNON = 'c';
  const ROOK = 'r';
  const KNIGHT = 'n';
  const BISHOP = 'b';
  const ADVISER = 'a';
  const KING = 'k';

  const SYMBOLS = 'pcrnbakPCRNBAK';
  const DEFAULT_POSITION = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR r - - 0 1';

  const POSSIBLE_RESULTS = Object.freeze(['1-0', '0-1', '1/2-1/2', '*']);

  // prettier-ignore
  const PAWN_OFFSETS = Object.freeze({
    b: [ 0x10, -0x01, 0x01],
    r: [-0x10, -0x01, 0x01]
  });
  // prettier-ignore
  const PIECE_OFFSETS = Object.freeze({
    c: [-0x10, 0x10, -0x01, 0x01],
    r: [-0x10, 0x10, -0x01, 0x01],
    n: [-0x20 - 0x01, -0x20 + 0x01,  0x20 - 0x01,  0x20 + 0x01,
        -0x10 - 0x02,  0x10 - 0x02, -0x10 + 0x02,  0x10 + 0x02],
    b: [-0x20 - 0x02,  0x20 + 0x02,  0x20 - 0x02, -0x20 + 0x02],
    a: [-0x10 - 0x01,  0x10 + 0x01,  0x10 - 0x01, -0x10 + 0x01],
    k: [-0x10, 0x10, -0x01, 0x01],
  });

  const FLAGS = Object.freeze({
    NORMAL: 'n',
    CAPTURE: 'c',
  });
  const BITS = Object.freeze({
    NORMAL: 1,
    CAPTURE: 2,
  });
  // prettier-ignore
  const SQUARES = Object.freeze({
    a9: 0x00, b9: 0x01, c9: 0x02, d9: 0x03, e9: 0x04, f9: 0x05, g9: 0x06, h9: 0x07, i9: 0x08,
    a8: 0x10, b8: 0x11, c8: 0x12, d8: 0x13, e8: 0x14, f8: 0x15, g8: 0x16, h8: 0x17, i8: 0x18,
    a7: 0x20, b7: 0x21, c7: 0x22, d7: 0x23, e7: 0x24, f7: 0x25, g7: 0x26, h7: 0x27, i7: 0x28,
    a6: 0x30, b6: 0x31, c6: 0x32, d6: 0x33, e6: 0x34, f6: 0x35, g6: 0x36, h6: 0x37, i6: 0x38,
    a5: 0x40, b5: 0x41, c5: 0x42, d5: 0x43, e5: 0x44, f5: 0x45, g5: 0x46, h5: 0x47, i5: 0x48,
    a4: 0x50, b4: 0x51, c4: 0x52, d4: 0x53, e4: 0x54, f4: 0x55, g4: 0x56, h4: 0x57, i4: 0x58,
    a3: 0x60, b3: 0x61, c3: 0x62, d3: 0x63, e3: 0x64, f3: 0x65, g3: 0x66, h3: 0x67, i3: 0x68,
    a2: 0x70, b2: 0x71, c2: 0x72, d2: 0x73, e2: 0x74, f2: 0x75, g2: 0x76, h2: 0x77, i2: 0x78,
    a1: 0x80, b1: 0x81, c1: 0x82, d1: 0x83, e1: 0x84, f1: 0x85, g1: 0x86, h1: 0x87, i1: 0x88,
    a0: 0x90, b0: 0x91, c0: 0x92, d0: 0x93, e0: 0x94, f0: 0x95, g0: 0x96, h0: 0x97, i0: 0x98,
  });
  let board = new Array(256);
  let kings = { r: EMPTY, b: EMPTY };
  let turn = RED;
  let half_moves = 0;
  let move_number = 1;
  let history = [];
  let futures = [];
  let header = {};

  /* if the user passes in a fen string, load it, else default to starting position */
  if (typeof fen === 'undefined') {
    load(DEFAULT_POSITION);
  } else {
    load(fen);
  }

  function clear(keep_headers) {
    if (typeof keep_headers === 'undefined') {
      keep_headers = false;
    }
    board = new Array(256);
    kings = { r: EMPTY, b: EMPTY };
    turn = RED;
    half_moves = 0;
    move_number = 1;
    history = [];
    futures = [];
    if (!keep_headers) header = {};
    update_setup(generate_fen());
  }

  function reset() {
    load(DEFAULT_POSITION);
  }

  function load(fen, keep_headers) {
    if (typeof keep_headers === 'undefined') {
      keep_headers = false;
    }

    if (!validate_fen(fen).valid) {
      return false;
    }
    let tokens = fen.split(/\s+/);
    let position = tokens[0];
    let square = 0,
      piece,
      color;

    clear(keep_headers);

    for (let i = 0; i < position.length; ++i) {
      piece = position.charAt(i);
      if (piece === '/') {
        square += 0x07;
      } else if (is_digit(piece)) {
        square += parseInt(piece, 10);
      } else {
        color = piece < 'a' ? RED : BLACK;
        put({ type: piece.toLowerCase(), color: color }, algebraic(square));
        square++;
      }
    }

    turn = tokens[1] === BLACK ? BLACK : RED;

    half_moves = parseInt(tokens[4], 10);
    move_number = parseInt(tokens[5], 10);

    update_setup(generate_fen());

    return true;
  }
  function validate_fen(fen) {
    const errors = {
      0: 'No errors.',
      1: 'FEN string must contain six space-delimited fields.',
      2: '6th field (move number) must be a positive integer.',
      3: '5th field (half move counter) must be a non-negative integer.',
      4: "4th field (en-passant square) should be '-'.",
      5: "3rd field (castling availability) should be '-'.",
      6: '2nd field (side to move) is invalid.',
      7: "1st field (piece positions) does not contain 10 '/'-delimited rows.",
      8: '1st field (piece positions) is invalid [consecutive numbers].',
      9: '1st field (piece positions) is invalid [invalid piece].',
      10: '1st field (piece positions) is invalid [row too large].',
      11: '1st field (piece positions) is invalid [each side has one king].',
      12: '1st field (piece positions) is invalid [each side has no more than 2 advisers].',
      13: '1st field (piece positions) is invalid [each side has no more than 2 bishops].',
      14: '1st field (piece positions) is invalid [each side has no more than 2 knights].',
      15: '1st field (piece positions) is invalid [each side has no more than 2 rooks].',
      16: '1st field (piece positions) is invalid [each side has no more than 2 cannons].',
      17: '1st field (piece positions) is invalid [each side has no more than 5 pawns].',
      18: '1st field (piece positions) is invalid [black king should at right position].',
      19: '1st field (piece positions) is invalid [red king should at right position].',
      20: '1st field (piece positions) is invalid [black adviser should at right position].',
      21: '1st field (piece positions) is invalid [red adviser should at right position].',
      22: '1st field (piece positions) is invalid [black bishop should at right position].',
      23: '1st field (piece positions) is invalid [red bishop should at right position].',
      24: '1st field (piece positions) is invalid [black pawn should at right position].',
      25: '1st field (piece positions) is invalid [red pawn should at right position].',
    };
    function result(err_num) {
      return { valid: err_num === 0, error_number: err_num, error: errors[err_num] };
    }

    /* 1st criterion: 6 space-separated fields? */
    const tokens = fen.split(/\s+/);
    if (tokens.length !== 6) {
      return result(1);
    }

    /* 2nd criterion: move number field is a integer value > 0? */
    if (tokens[5] === '' || isNaN(tokens[5]) || parseInt(tokens[5], 10) <= 0) {
      return result(2);
    }
    /* 3rd criterion: half move counter is an integer >= 0? */
    if (tokens[4] === '' || isNaN(tokens[4]) || parseInt(tokens[4], 10) < 0) {
      return result(3);
    }

    /* 4th criterion: 4th field is a valid e.p.-string? */
    if (!/^-$/.test(tokens[3])) {
      return result(4);
    }

    /* 5th criterion: 3th field is a valid castle-string? */
    if (!/^-$/.test(tokens[2])) {
      return result(5);
    }
    /* 6th criterion: 2nd field is "r/w" (red) or "b" (black)? */
    if (!/^([rwb])$/.test(tokens[1])) {
      return result(6);
    }

    /* 7th criterion: 1st field contains 10 rows? */
    const rows = tokens[0].split('/');
    if (rows.length !== 10) {
      return result(7);
    }
    /* 8th criterion: every row is valid? */
    // prettier-ignore
    const pieces = {
      p: { number: 0, squares: [] }, P: { number: 0, squares: [] },
      c: { number: 0, squares: [] }, C: { number: 0, squares: [] },
      r: { number: 0, squares: [] }, R: { number: 0, squares: [] },
      n: { number: 0, squares: [] }, N: { number: 0, squares: [] },
      b: { number: 0, squares: [] }, B: { number: 0, squares: [] },
      a: { number: 0, squares: [] }, A: { number: 0, squares: [] },
      k: { number: 0, squares: [] }, K: { number: 0, squares: [] },
      };
    let i, j, sum_fields, previous_was_number;
    for (i = 0; i < rows.length; i++) {
      /* check for right sum of fields AND not two numbers in succession */
      sum_fields = 0;
      previous_was_number = false;
      for (j = 0; j < rows[i].length; j++) {
        if (!isNaN(rows[i][j])) {
          if (previous_was_number) {
            return result(8);
          }
          sum_fields += parseInt(rows[i][j], 10);
          previous_was_number = true;
        } else {
          try {
            ++pieces[rows[i][j]].number;
          } catch (e) {
            return result(9);
          }
          pieces[rows[i][j]].squares.push((i << 4) | sum_fields);
          sum_fields += 1;
          previous_was_number = false;
        }
      }
      if (sum_fields !== 9) {
        return result(10);
      }
    }
    /* 9th criterion: every piece's number is valid? */
    if (pieces.k.number !== 1 || pieces.K.number !== 1) {
      return result(11);
    }
    if (pieces.a.number > 2 || pieces.A.number > 2) {
      return result(12);
    }
    if (pieces.b.number > 2 || pieces.B.number > 2) {
      return result(13);
    }
    if (pieces.n.number > 2 || pieces.N.number > 2) {
      return result(14);
    }
    if (pieces.r.number > 2 || pieces.R.number > 2) {
      return result(15);
    }
    if (pieces.c.number > 2 || pieces.C.number > 2) {
      return result(16);
    }
    if (pieces.p.number > 5 || pieces.P.number > 5) {
      return result(17);
    }
    /* 10th criterion: every piece's place is valid? */
    if (out_of_place(KING, pieces.k.squares[0], BLACK)) {
      return result(18);
    }
    if (out_of_place(KING, pieces.K.squares[0], RED)) {
      return result(19);
    }
    for (i = 0; i < pieces.a.squares.length; ++i) {
      if (out_of_place(ADVISER, pieces.a.squares[i], BLACK)) {
        return result(20);
      }
    }
    for (i = 0; i < pieces.A.squares.length; ++i) {
      if (out_of_place(ADVISER, pieces.A.squares[i], RED)) {
        return result(21);
      }
    }
    for (i = 0; i < pieces.b.squares.length; ++i) {
      if (out_of_place(BISHOP, pieces.b.squares[i], BLACK)) {
        return result(22);
      }
    }
    for (i = 0; i < pieces.B.squares.length; ++i) {
      if (out_of_place(BISHOP, pieces.B.squares[i], RED)) {
        return result(23);
      }
    }
    for (i = 0; i < pieces.p.squares.length; ++i) {
      if (out_of_place(PAWN, pieces.p.squares[i], BLACK)) {
        return result(24);
      }
    }
    for (i = 0; i < pieces.P.squares.length; ++i) {
      if (out_of_place(PAWN, pieces.P.squares[i], RED)) {
        return result(25);
      }
    }
    /* everything's okay! */
    return result(0);
  }

  function generate_fen() {
    let empty = 0,
      fen = '',
      color,
      piece;

    for (let i = SQUARES.a9; i <= SQUARES.i0; ++i) {
      if (board[i] == null) {
        empty++;
      } else {
        if (empty > 0) {
          fen += empty;
          empty = 0;
        }
        color = board[i].color;
        piece = board[i].type;

        fen += color === RED ? piece.toUpperCase() : piece.toLowerCase();
      }
      if (file(i) >= 8) {
        if (empty > 0) {
          fen += empty;
        }

        if (i !== SQUARES.i0) {
          fen += '/';
        }

        empty = 0;
        i += 0x07;
      }
    }

    return [fen, turn, '-', '-', half_moves, move_number].join(' ');
  }

  function set_header(args) {
    for (let i = 0; i < args.length; i += 2) {
      if (typeof args[i] === 'string' && typeof args[i + 1] === 'string') {
        header[args[i]] = args[i + 1];
      }
    }
    return header;
  }
  /* called when the initial board setup is changed with put() or remove().
   * modifies the FEN properties of the header object.  if the FEN is equal to
   * the default position, the FEN are deleted the setup is only updated if history.
   * length is zero, ie moves haven't been made.
   */
  function update_setup(fen) {
    if (history.length > 0) return;

    if (fen !== DEFAULT_POSITION) {
      header.FEN = fen;
    } else {
      delete header.FEN;
    }
  }
  function get(square) {
    const piece = board[SQUARES[square]];
    return piece ? { type: piece.type, color: piece.color } : null;
  }

  function put(piece, square) {
    /* check for valid piece object */
    if (!('type' in piece && 'color' in piece)) {
      return false;
    }

    /* check for piece */
    if (SYMBOLS.indexOf(piece.type.toLowerCase()) === -1) {
      return false;
    }

    /* check for valid square */
    if (!(square in SQUARES)) {
      return false;
    }
    const sq = SQUARES[square];

    /* don't let the user place more than one king */
    if (piece.type === KING && !(kings[piece.color] === EMPTY || kings[piece.color] === sq)) {
      return false;
    }

    if (out_of_place(piece.type, sq, piece.color)) {
      return false;
    }

    board[sq] = { type: piece.type, color: piece.color };
    if (piece.type === KING) {
      kings[piece.color] = sq;
    }

    update_setup(generate_fen());

    return true;
  }
  function remove(square) {
    const piece = get(square);
    board[SQUARES[square]] = null;
    if (piece && piece.type === KING) {
      kings[piece.color] = EMPTY;
    }

    update_setup(generate_fen());

    return piece;
  }

  function build_move(board, from, to, flags) {
    let move = {
      color: turn,
      from: from,
      to: to,
      flags: flags,
      piece: board[from].type,
    };

    if (board[to]) {
      move.captured = board[to].type;
    }
    return move;
  }
  function generate_moves(options) {
    function add_move(board, moves, from, to, flags) {
      moves.push(build_move(board, from, to, flags));
    }

    let moves = [];
    let us = turn;
    let them = swap_color(us);

    let first_sq = SQUARES.a9;
    let last_sq = SQUARES.i0;
    /* do we want legal moves? */
    const legal = typeof options !== 'undefined' && 'legal' in options ? options.legal : true;
    // do we need opponent moves?
    const opponent = typeof options !== 'undefined' && 'opponent' in options ? options.opponent : false;
    /* are we generating moves for a single square? */
    if (typeof options !== 'undefined' && 'square' in options) {
      if (options.square in SQUARES) {
        first_sq = last_sq = SQUARES[options.square];
      } else {
        /* invalid square */
        return [];
      }
    }

    if (opponent) {
      turn = swap_color(turn);
      us = turn;
      them = swap_color(us);
    }
    let i, j, len, piece, OFFSETS, offset, square, crossed;
    for (i = first_sq; i <= last_sq; ++i) {
      piece = board[i];
      if (piece == null || piece.color !== us) continue;

      OFFSETS = piece.type === PAWN ? PAWN_OFFSETS[us] : PIECE_OFFSETS[piece.type];

      for (j = 0, len = OFFSETS.length; j < len; ++j) {
        if (piece.type === PAWN && j > 0 && !crossed_river(i, us)) break;

        offset = OFFSETS[j];
        square = i;
        crossed = false;
        while (true) {
          square += offset;

          if (out_of_board(square)) break;
          else if (piece.type === KNIGHT && hobbling_horse_leg(i, j)) break;
          else if (piece.type === BISHOP && (blocking_elephant_eye(i, j) || crossed_river(square, us))) break;
          else if ((piece.type === ADVISER || piece.type === KING) && out_of_place(piece.type, square, us)) break;
          if (board[square] == null) {
            if (piece.type === CANNON && crossed) continue;
            add_move(board, moves, i, square, BITS.NORMAL);
          } else {
            if (piece.type === CANNON) {
              if (crossed) {
                if (board[square].color === them) add_move(board, moves, i, square, BITS.CAPTURE);
                break;
              }
              crossed = true;
            } else {
              if (board[square].color === them) add_move(board, moves, i, square, BITS.CAPTURE);
              break;
            }
          }
          if (piece.type !== CANNON && piece.type !== ROOK) break;
        }
      }
      if (file(i) >= 8) {
        i = i + 0x07;
      }
    }

    /* return all pseudo-legal moves (this includes moves that allow the king
     * to be captured)
     */
    if (!legal) {
      return moves;
    }

    /* filter out illegal moves */
    let legal_moves = [];
    for (i = 0, len = moves.length; i < len; i++) {
      make_move(moves[i]);
      if (!king_attacked(us)) {
        legal_moves.push(moves[i]);
      }
      undo_move();
    }
    // DID we need opponent moves?
    if (opponent) {
      turn = swap_color(turn);
    }

    return legal_moves;
  }
  /* convert a move from 0x9a coordinates to Internet Chinese Chess Server (ICCS)
   *
   * @param {boolean} sloppy Use the sloppy ICCS generator to work around over
   * disambiguation bugs in Fritz and Chessbase.  See below:
   *
   * r1bqkbnr/ppp2ppp/2n5/1B1pP3/4P3/8/PPPP2PP/RNBQK1NR b KQkq - 2 4
   * 4. ... Nge7 is overly disambiguated because the knight on c6 is pinned
   * 4. ... Ne7 is technically the valid SAN
   */
  function move_to_iccs(move, sloppy) {
    let output = '';
    // let disambiguator = get_disambiguator(move, sloppy);

    // if (move.piece !== PAWN) {
    //   output += move.piece.toUpperCase() + disambiguator;
    // }

    // output += algebraic(move.to);
    output = algebraic(move.from) + algebraic(move.to);

    return output;
  }

  // parses all of the decorators out of a SAN string
  function stripped_iccs(move) {
    return move.replace(/=/, '').replace(/[+#]?[?!]*$/, '');
  }
  function king_attacked(us) {
    let square = kings[us];
    let them = swap_color(us);
    let i, len, sq;
    // knight
    for (i = 0, len = PIECE_OFFSETS[KNIGHT].length; i < len; ++i) {
      sq = square + PIECE_OFFSETS[KNIGHT][i];
      if (
        board[sq] != null &&
        !out_of_board(sq) &&
        board[sq].color === them &&
        board[sq].type === KNIGHT &&
        !hobbling_horse_leg(sq, i < 4 ? 3 - i : 11 - i)
      )
        return true;
    }
    // king, rook, cannon
    for (i = 0, len = PIECE_OFFSETS[ROOK].length; i < len; ++i) {
      let offset = PIECE_OFFSETS[ROOK][i];
      let crossed = false;
      for (sq = square + offset; !out_of_board(sq); sq += offset) {
        let piece = board[sq];
        if (piece != null) {
          if (piece.color === them) {
            if (crossed) {
              if (piece.type === CANNON) return true;
            } else {
              if (piece.type === ROOK || piece.type === KING) return true;
            }
          }
          if (crossed) break;
          else crossed = true;
        }
      }
    }
    // pawn
    for (i = 0, len = PAWN_OFFSETS[them].length; i < len; ++i) {
      sq = square - PAWN_OFFSETS[them][i];
      if (board[sq] != null && !out_of_board(sq) && board[sq].color === them && board[sq].type === PAWN) return true;
    }
    return false;
  }

  function in_check() {
    return king_attacked(turn);
  }

  function in_checkmate() {
    return in_check() && generate_moves().length === 0;
  }

  function in_stalemate() {
    return !in_check() && generate_moves().length === 0;
  }

  function insufficient_material() {
    // TODO: more cases
    let pieces = {},
      piece;
    let num_pieces = 0;
    for (let sq in SQUARES) {
      if (SQUARES.hasOwnProperty(sq)) {
        piece = board[SQUARES[sq]];
        if (piece) {
          pieces[piece.type] = piece.type in pieces ? pieces[piece.type] + 1 : 1;
          num_pieces++;
        }
      }
    }
    /* k vs. k */
    if (num_pieces === 2) return true;
    else if (
      typeof pieces[KNIGHT] === 'undefined' &&
      typeof pieces[ROOK] === 'undefined' &&
      typeof pieces[CANNON] === 'undefined' &&
      typeof pieces[PAWN] === 'undefined'
    )
      return true;

    return false;
  }
  function in_threefold_repetition() {
    /* TODO: while this function is fine for casual use, a better
     * implementation would use a Zobrist key (instead of FEN). the
     * Zobrist key would be maintained in the make_move/undo_move functions,
     * avoiding the costly that we do below.
     */
    let moves = [],
      move;
    let positions = {};
    let repetition = false;

    while (true) {
      move = undo_move();
      if (!move) break;
      moves.push(move);
    }
    while (true) {
      /* remove the last four fields in the FEN string, they're not needed
       * when checking for draw by rep */
      let fen = generate_fen().split(' ').slice(0, 2).join(' ');

      /* has the position occurred three or move times */
      positions[fen] = fen in positions ? positions[fen] + 1 : 1;
      if (positions[fen] >= 3) {
        repetition = true;
      }

      if (!moves.length) {
        break;
      }
      make_move(moves.pop());
    }

    return repetition;
  }
  function push(list, move) {
    list.push({
      move: move,
      kings: { b: kings.b, r: kings.r },
      turn: turn,
      half_moves: half_moves,
      move_number: move_number,
    });
  }

  function make_move(move) {
    push(history, move);

    // if king was captured
    if (board[move.to] != null && board[move.to].type === KING) kings[board[move.to].color] = EMPTY;

    board[move.to] = board[move.from];
    board[move.from] = null;
    /* if we moved the king */
    if (board[move.to].type === KING) {
      kings[board[move.to].color] = move.to;
    }

    /* reset the 60 move counter if a piece is captured */
    if (move.flags & BITS.CAPTURE) {
      half_moves = 0;
    } else {
      half_moves++;
    }

    if (turn === BLACK) {
      move_number++;
    }
    turn = swap_color(turn);
  }

  function set_move(old, undo = true) {
    if (old == null) {
      return null;
    }
    const move = old.move;
    kings = old.kings;
    turn = old.turn;
    half_moves = old.half_moves;
    move_number = old.move_number;

    board[move.from] = board[move.to];
    board[move.from].type = move.piece;
    board[move.to] = null;

    if ((move.flags & BITS.CAPTURE) > 0 && undo) {
      board[move.to] = { type: move.captured, color: swap_color(turn) };
    }

    return move;
  }

  function undo_move() {
    return set_move(history.pop());
  }
  function redo_move() {
    return set_move(futures.pop(), false);
  }

  /* this function is used to uniquely identify ambiguous moves */
  function get_disambiguator(move, sloppy) {
    const moves = generate_moves({ legal: !sloppy });

    const from = move.from;
    const to = move.to;
    const piece = move.piece;

    let ambiguities = 0;
    let same_rank = 0;
    let same_file = 0;
    for (let i = 0, len = moves.length; i < len; i++) {
      let ambig_from = moves[i].from;
      let ambig_to = moves[i].to;
      let ambig_piece = moves[i].piece;

      /* if a move of the same piece type ends on the same to square, we'll
       * need to add a disambiguator to the algebraic notation
       */
      if (piece === ambig_piece && from !== ambig_from && to === ambig_to) {
        ambiguities++;

        if (rank(from) === rank(ambig_from)) {
          same_rank++;
        }
        if (file(from) === file(ambig_from)) {
          same_file++;
        }
      }
    }
    if (ambiguities > 0) {
      /* if there exists a similar moving piece on the same rank and file as
       * the move in question, use the square as the disambiguator
       */
      if (same_rank > 0 && same_file > 0) {
        return algebraic(from);
      } else if (same_file > 0) {
        /* if the moving piece rests on the same file, use the rank symbol as the
         * disambiguator
         */
        return algebraic(from).charAt(1);
      } else {
        /* else use the file symbol */
        return algebraic(from).charAt(0);
      }
    }
    return '';
  }

  function ascii() {
    let s = '   +---------------------------+\n';
    for (let i = SQUARES.a9; i <= SQUARES.i0; i++) {
      /* display the rank */
      if (file(i) === 0) {
        s += ' ' + '9876543210'[rank(i)] + ' |';
      }
      /* empty piece */
      if (board[i] == null) {
        s += ' . ';
      } else {
        let piece = board[i].type;
        let color = board[i].color;
        let symbol = color === RED ? piece.toUpperCase() : piece.toLowerCase();
        s += ' ' + symbol + ' ';
      }

      if (i & 0x08) {
        s += '|\n';
        i += 7;
      }
    }
    s += '   +---------------------------+\n';
    s += '     a  b  c  d  e  f  g  h  i\n';

    return s;
  }
  // convert a move from Internet Chinese Chess Server (ICCS) to 0x9a coordinates
  function move_from_iccs(move, sloppy) {
    // strip off any move decorations: e.g Nf3+?!
    let clean_move = stripped_iccs(move);
    // if we're using the sloppy parser run a regex to grab piece, to, and from
    // this should parse invalid ICCS like: h2e2, H7-E7
    let matches = clean_move.match(/([a-iA-I][0-9])-?([a-iA-I][0-9])/);
    let piece, from, to;
    // TODO: support sloppy (must integrate with WXF)
    if (sloppy) {
      if (matches) {
        piece = matches[1];
        from = matches[2];
        to = matches[3];
      }
    }
    let moves = generate_moves();
    for (let i = 0, len = moves.length; i < len; i++) {
      // try the strict parser first, then the sloppy parser if requested by the user
      if (
        clean_move === stripped_iccs(move_to_iccs(moves[i])) ||
        (sloppy && clean_move === stripped_iccs(move_to_iccs(moves[i], true)))
      ) {
        return moves[i];
      } else {
        if (
          matches &&
          (!piece || piece.toLowerCase() === moves[i].piece) &&
          SQUARES[from] === moves[i].from &&
          SQUARES[to] === moves[i].to
        ) {
          return moves[i];
        }
      }
    }
    return null;
  }

  /*****************************************************************************
   * UTILITY FUNCTIONS
   ****************************************************************************/
  function rank(i) {
    return i >> 4;
  }

  function file(i) {
    return i & 0x0f;
  }

  function algebraic(i) {
    const f = file(i),
      r = rank(i);
    return 'abcdefghi'.substring(f, f + 1) + '9876543210'.substring(r, r + 1);
  }
  function swap_color(c) {
    return c === RED ? BLACK : RED;
  }

  function is_digit(c) {
    return '0123456789'.indexOf(c) !== -1;
  }

  function crossed_river(p, c) {
    return c === RED ? rank(p) < 5 : rank(p) > 4;
  }

  function out_of_board(square) {
    return square < 0 || rank(square) > 9 || file(square) > 8;
  }
  function out_of_place(piece, square, color) {
    // K, A, B, P
    let side = {};
    if (piece === PAWN) {
      side = [0, 2, 4, 6, 8];
      if (color === RED) {
        return rank(square) > 6 || (rank(square) > 4 && side.indexOf(file(square)) === -1);
      } else {
        return rank(square) < 3 || (rank(square) < 5 && side.indexOf(file(square)) === -1);
      }
    } else if (piece === BISHOP) {
      // prettier-ignore
      side[RED]   = [0x92, 0x96, 0x70, 0x74, 0x78, 0x52, 0x56];
      side[BLACK] = [0x02, 0x06, 0x20, 0x24, 0x28, 0x42, 0x46];
    } else if (piece === ADVISER) {
      // prettier-ignore
      side[RED]   = [0x93, 0x95, 0x84, 0x73, 0x75];
      side[BLACK] = [0x03, 0x05, 0x14, 0x23, 0x25];
    } else if (piece === KING) {
      // prettier-ignore
      side[RED]   = [0x93, 0x94, 0x95, 0x83, 0x84, 0x85, 0x73, 0x74, 0x75];
      side[BLACK] = [0x03, 0x04, 0x05, 0x13, 0x14, 0x15, 0x23, 0x24, 0x25];
    } else {
      // C, R, N
      return out_of_board(square);
    }
    return side[color].indexOf(square) === -1;
  }

  function hobbling_horse_leg(square, index) {
    const orientation = [-0x10, 0x10, -0x01, 0x01];
    return board[square + orientation[Math.floor(index / 2)]] != null;
  }

  function blocking_elephant_eye(square, index) {
    const orientation = [-0x10 - 0x01, 0x10 + 0x01, 0x10 - 0x01, -0x10 + 0x01];
    return board[square + orientation[index]] != null;
  }
  /* pretty = external move object */
  function make_pretty(ugly_move) {
    let move = clone(ugly_move);
    move.iccs = move_to_iccs(move, false);
    move.to = algebraic(move.to);
    move.from = algebraic(move.from);
    move.piece = turn === RED ? move.piece.toUpperCase() : move.piece;

    let flags = '';

    for (let flag in BITS) {
      if ((BITS[flag] & move.flags) > 0) {
        flags += FLAGS[flag];
      }
    }
    move.flags = flags;

    return move;
  }
  function clone(obj) {
    let dupe = obj instanceof Array ? [] : {};

    for (let property in obj) {
      if (typeof property === 'object') {
        dupe[property] = clone(obj[property]);
      } else {
        dupe[property] = obj[property];
      }
    }

    return dupe;
  }

  function trim(str) {
    return str.replace(/^\s+|\s+$/g, '');
  }
  /*****************************************************************************
   * DEBUGGING UTILITIES
   ****************************************************************************/
  function perft(depth) {
    const moves = generate_moves({ legal: false });
    let nodes = 0;
    for (let i = 0, len = moves.length; i < len; i++) {
      make_move(moves[i]);
      if (!king_attacked(turn)) {
        if (depth - 1 > 0) {
          let child_nodes = perft(depth - 1);
          nodes += child_nodes;
        } else {
          nodes++;
        }
      }
      undo_move();
    }

    return nodes;
  }
  return {
    /***************************************************************************
     * PUBLIC CONSTANTS (is there a better way to do this?)
     **************************************************************************/
    RED: RED,
    BLACK: BLACK,
    PAWN: PAWN,
    CANNON: CANNON,
    ROOK: ROOK,
    KNIGHT: KNIGHT,
    BISHOP: BISHOP,
    ADVISER: ADVISER,
    KING: KING,
    SQUARES: (function () {
      /* from the ECMA-262 spec (section 12.6.4):
       * "The mechanics of enumerating the properties ... is
       * implementation dependent"
       * so: for (let sq in SQUARES) { keys.push(sq); } might not be
       * ordered correctly
       */
      let keys = [];
      for (let i = SQUARES.a9; i <= SQUARES.i0; i++) {
        if (file(i) === 0x09) {
          i += 6;
          continue;
        }
        keys.push(algebraic(i));
      }
      return keys;
    })(),
    FLAGS: FLAGS,
    /***************************************************************************
     * PUBLIC API
     **************************************************************************/
    load: function (fen) {
      return load(fen);
    },

    reset: function () {
      return reset();
    },
    moves: function (options) {
      /* The internal representation of a xiangqi move is in 0x9a format, and
       * not meant to be human-readable.  The code below converts the 0x9a
       * square coordinates to algebraic coordinates.  It also prunes an
       * unnecessary move keys resulting from a verbose call.
       */

      const ugly_moves = generate_moves(options);
      let moves = [];
      for (let i = 0, len = ugly_moves.length; i < len; i++) {
        // does the user want a full move object (most likely not), or just ICCS
        if (typeof options !== 'undefined' && 'verbose' in options && options.verbose) {
          moves.push(make_pretty(ugly_moves[i]));
        } else {
          moves.push(move_to_iccs(ugly_moves[i], false));
        }
      }

      return moves;
    },

    in_check: function () {
      return in_check();
    },
    in_checkmate: function () {
      return in_checkmate();
    },

    in_stalemate: function () {
      return in_stalemate();
    },

    in_draw: function () {
      return (
        half_moves >= 120 ||
        // Just a temporary workaround, should be refined in the future.
        in_threefold_repetition() ||
        insufficient_material()
      );
    },

    insufficient_material: function () {
      return insufficient_material();
    },
    in_threefold_repetition: function () {
      return in_threefold_repetition();
    },

    game_over: function () {
      return (
        half_moves >= 120 ||
        in_checkmate() ||
        in_stalemate() ||
        insufficient_material() ||
        in_threefold_repetition() ||
        kings[swap_color(turn)] === EMPTY
      );
    },

    validate_fen: function (fen) {
      return validate_fen(fen);
    },

    fen: function () {
      return generate_fen();
    },
    board: function () {
      let output = [],
        row = [];

      for (let i = SQUARES.a9; i <= SQUARES.i0; i++) {
        if (board[i] == null) {
          row.push(null);
        } else {
          row.push({ type: board[i].type, color: board[i].color });
        }
        if (i & 0x08) {
          output.push(row);
          row = [];
          i += 7;
        }
      }

      return output;
    },
    pgn: function (options) {
      /* using the specification from http://www.xqbase.com/protocol/cchess_pgn.htm
       * example for html usage: .pgn({ max_width: 72, newline_char: "<br />" })
       */
      let newline =
        typeof options === 'object' && typeof options.newline_char === 'string' ? options.newline_char : '\n';
      let max_width = typeof options === 'object' && typeof options.max_width === 'number' ? options.max_width : 0;
      let result = [];
      let header_exists = false;
      let i;
      /* add the PGN header headerrmation */
      for (i in header) {
        /* TODO: order of enumerated properties in header object is not guaranteed,
             see ECMA-262 spec (section 12.6.4)
         */
        result.push('[' + i + ' "' + header[i] + '"]' + newline);
        header_exists = true;
      }

      if (header_exists && history.length) {
        result.push(newline);
      }
      /* pop all of history onto reversed_history */
      let reversed_history = [];
      while (history.length > 0) {
        reversed_history.push(undo_move());
      }

      let moves = [];
      let move_string = '';

      /* build the list of moves.  a move_string looks like: "3. b2b6 b9c7" */
      while (reversed_history.length > 0) {
        let move = reversed_history.pop();
        /* if the position started with black to move, start PGN with 1. ... */
        if (!history.length && move.color === 'b') {
          move_string = move_number + '. ...';
        } else if (move.color !== 'b') {
          /* store the previous generated move_string if we have one */
          if (move_string.length) {
            moves.push(move_string);
          }
          move_string = move_number + '.';
        }
        move_string = move_string + ' ' + move_to_iccs(move, false);
        make_move(move);
      }

      /* are there any other leftover moves? */
      if (move_string.length) {
        moves.push(move_string);
      }

      /* is there a result? */
      if (typeof header.Result !== 'undefined') {
        moves.push(header.Result);
      }
      /* history should be back to what is was before we started generating PGN,
       * so join together moves
       */
      if (max_width === 0) {
        return result.join('') + moves.join(' ');
      }
      /* wrap the PGN output at max_width */
      let current_width = 0;
      for (i = 0; i < moves.length; i++) {
        /* if the current move will push past max_width */
        if (current_width + moves[i].length > max_width && i !== 0) {
          /* don't end the line with whitespace */
          if (result[result.length - 1] === ' ') {
            result.pop();
          }
          result.push(newline);
          current_width = 0;
        } else if (i !== 0) {
          result.push(' ');
          current_width++;
        }
        result.push(moves[i]);
        current_width += moves[i].length;
      }

      return result.join('');
    },
    load_pgn: function (pgn, options) {
      // allow the user to specify the sloppy move parser to work around over
      // disambiguation bugs in Fritz and Chessbase
      let sloppy = typeof options !== 'undefined' && 'sloppy' in options ? options.sloppy : false;

      function mask(str) {
        return str.replace(/\\/g, '\\');
      }

      function has_keys(object) {
        for (let key in object) {
          if (object.hasOwnProperty(key)) return true;
        }
        return false;
      }
      function parse_pgn_header(header, options) {
        let newline_char =
          typeof options === 'object' && typeof options.newline_char === 'string' ? options.newline_char : '\r?\n';
        let header_obj = {};
        let headers = header.split(new RegExp(mask(newline_char)));
        let key = '';
        let value = '';
        for (let i = 0; i < headers.length; i++) {
          key = headers[i].replace(/^\[([A-Z][A-Za-z]*)\s.*]$/, '$1');
          value = headers[i].replace(/^\[[A-Za-z]+\s"(.*)"]$/, '$1');
          if (trim(key).length > 0) {
            header_obj[key] = value;
          }
        }

        return header_obj;
      }

      const newline_char =
        typeof options === 'object' && typeof options.newline_char === 'string' ? options.newline_char : '\r?\n';
      // RegExp to split header.
      // With default newline_char, will equal: /^(?:\s)*(((?:\r?\n)*\[[^\]]+\])+)/
      const header_regex = new RegExp('^(?:\\s)*(((?:' + mask(newline_char) + ')*\\[[^\\]]+\\])+)');

      // If no header given, begin with moves.
      const header_string = header_regex.test(pgn) ? header_regex.exec(pgn)[1] : '';
      // Put the board in the starting position
      reset();
      /* parse PGN header */
      const headers = parse_pgn_header(header_string, options);
      for (let key in headers) {
        if (headers.hasOwnProperty(key)) set_header([key, headers[key]]);
      }

      /* load the starting position indicated by [FEN position] */
      if ('FEN' in headers) {
        if (!load(headers.FEN, true)) {
          // second argument to load: don't clear the headers
          console.warn('load header FEN failed!');
          return false;
        }
      }
      /* delete header to get the moves */
      let ms = pgn.replace(header_string, '').replace(new RegExp(mask(newline_char), 'g'), ' ');

      /* delete comments */
      ms = ms.replace(/({[^}]+})+?/g, '');
      /* delete recursive annotation variations */
      const rav_regex = /(\([^()]+\))+?/g;
      while (rav_regex.test(ms)) {
        ms = ms.replace(rav_regex, '');
      }

      /* delete move numbers */
      ms = ms.replace(/\d+\.(\.\.)?/g, '');
      /* delete ... indicating black to move */
      ms = ms.replace(/\.\.\./g, '');

      /* delete numeric annotation glyphs */
      ms = ms.replace(/\$\d+/g, '');

      /* trim and get array of moves */
      let moves = trim(ms).split(new RegExp(/\s+/));

      /* delete empty entries */
      moves = moves.join(',').replace(/,,+/g, ',').split(',');
      let move = '';
      for (let half_move = 0; half_move < moves.length - 1; half_move++) {
        move = move_from_iccs(moves[half_move], sloppy);

        /* move not possible! (don't clear the board to examine to show the
         * latest valid position)
         */
        if (move == null) {
          console.warn(`impossible move: ${moves[half_move]}!\n${ascii()}`);
          return false;
        } else {
          make_move(move);
        }
      }
      /* examine last move */
      move = moves[moves.length - 1];
      if (POSSIBLE_RESULTS.indexOf(move) > -1) {
        if (has_keys(header) && typeof header.Result === 'undefined') {
          set_header(['Result', move]);
        }
      } else {
        move = move_from_iccs(move, sloppy);
        if (move == null) {
          console.warn(`impossible last move: ${moves[moves.length - 1]}!\n${ascii()}`);
          return false;
        } else {
          make_move(move);
        }
      }
      return true;
    },
    header: function () {
      return set_header(arguments);
    },

    ascii: function () {
      return ascii();
    },

    turn: function () {
      return turn;
    },
    move: function (move, options) {
      /* The move function can be called with in the following parameters:
       *
       * .move('Nxb7')      <- where 'move' is a case-sensitive SAN string
       *
       * .move({ from: 'h7', <- where the 'move' is a move object (additional
       *         to :'h8',      fields are ignored)
       *      })
       */
      // allow the user to specify the sloppy move parser to work around over
      // disambiguation bugs in Fritz and Chessbase
      const sloppy = typeof options !== 'undefined' && 'sloppy' in options ? options.sloppy : false;

      let move_obj = null;

      if (typeof move === 'string') {
        move_obj = move_from_iccs(move, sloppy);
      } else if (typeof move === 'object') {
        let moves = generate_moves();
        /* convert the pretty move object to an ugly move object */
        for (let i = 0, len = moves.length; i < len; i++) {
          if (move.from === algebraic(moves[i].from) && move.to === algebraic(moves[i].to) && !('' in moves[i])) {
            move_obj = moves[i];
            break;
          }
        }
      }

      /* failed to find move */
      if (!move_obj) {
        return null;
      }
      /* need to make a copy of move because we can't generate SAN after the
       * move is made
       */
      const pretty_move = make_pretty(move_obj);

      make_move(move_obj);
      futures = [];

      return pretty_move;
    },
    undo: function () {
      push(futures, null);
      const move = undo_move();
      if (move) {
        const pretty_move = make_pretty(move);
        [move.from, move.to] = [move.to, move.from];
        futures[futures.length - 1].move = move;
        return pretty_move;
      } else {
        futures.pop();
        return null;
      }
    },
    redo: function () {
      push(history, null);
      const move = redo_move();
      if (move) {
        [move.from, move.to] = [move.to, move.from];
        history[history.length - 1].move = move;
        return make_pretty(move);
      } else {
        history.pop();
        return null;
      }
    },

    clear: function () {
      return clear();
    },

    put: function (piece, square) {
      return put(piece, square);
    },

    get: function (square) {
      return get(square);
    },
    remove: function (square) {
      return remove(square);
    },

    perft: function (depth) {
      return perft(depth);
    },

    history: function (options) {
      let reversed_history = [];
      let move_history = [];
      let verbose = typeof options !== 'undefined' && 'verbose' in options && options.verbose;

      while (history.length > 0) {
        reversed_history.push(undo_move());
      }
      while (reversed_history.length > 0) {
        let move = reversed_history.pop();
        if (verbose) {
          move_history.push(make_pretty(move));
        } else {
          move_history.push(move_to_iccs(move));
        }
        make_move(move);
      }

      return move_history;
    },
  };
};
/* export Xiangqi object if using node or any other CommonJS compatible
 * environment */
if (typeof exports !== 'undefined') exports.Xiangqi = Xiangqi;
/* export Xiangqi object for any RequireJS compatible environment */
if (typeof define !== 'undefined')
  define(function () {
    return Xiangqi;
  });
