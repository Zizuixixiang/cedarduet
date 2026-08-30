import { Piece } from './Piece';

export const PLAYER1_LEFT_RAIL: string[] = ['a2', 'a3', 'a4', 'a5', 'a6'];
export const PLAYER1_RIGHT_RAIL: string[] = ['e2', 'e3', 'e4', 'e5', 'e6'];
export const PLAYER1_BACK_ROW_RAIL: string[] = ['a2', 'b2', 'c2', 'd2', 'e2'];
export const PLAYER1_FRONT_ROW_RAIL: string[] = ['a6', 'b6', 'c6', 'd6', 'e6'];

export const PLAYER2_LEFT_RAIL: string[] = ['e7', 'e8', 'e9', 'e10', 'e11'];
export const PLAYER2_RIGHT_RAIL: string[] = ['a7', 'a8', 'a9', 'a10', 'a11'];
export const PLAYER2_FRONT_ROW_RAIL: string[] = ['a7', 'b7', 'c7', 'd7', 'e7'];
export const PLAYER2_BACK_ROW_RAIL: string[] = ['a11', 'b11', 'c11', 'd11', 'e11'];

// left and right is inverted depending on the player's perspective
export const LEFT_RAIL: string[] = PLAYER1_LEFT_RAIL.concat(PLAYER2_RIGHT_RAIL);
export const RIGHT_RAIL: string[] = PLAYER1_RIGHT_RAIL.concat(PLAYER2_LEFT_RAIL);

export const RAIL_LINES: string[][] = [
  LEFT_RAIL,
  RIGHT_RAIL,
  PLAYER1_BACK_ROW_RAIL,
  PLAYER1_FRONT_ROW_RAIL,
  PLAYER2_FRONT_ROW_RAIL,
  PLAYER2_BACK_ROW_RAIL
];

export const FRONT_ROW_SQUARES: string[] = PLAYER1_FRONT_ROW_RAIL.concat(PLAYER2_FRONT_ROW_RAIL);

export const BUNKER_SQUARES: string[] = ['b3', 'd3', 'c4', 'b5', 'd5', 'b8', 'd8', 'c9', 'b10', 'd10'];
export const HEADQUARTER_SQUARE_GROUP: string[][] = [['b1', 'd1'], ['b12', 'd12']];
export const HEADQUARTER_SQUARES: string[] = HEADQUARTER_SQUARE_GROUP.flat();

export type BoardSquarePieceMap = {
  [key: string]: Piece | null;
}
