import { Graph } from './Graph';
import { Piece } from './Piece';

export class RailroadNetwork {
  graph: Graph;
  railLines: string[][];

  constructor(railLines: string[][]) {
    this.graph = new Graph();
    this.railLines = railLines;
  }

  // Get all the reachable squares from the current square
  // A square is not reachable if it is not accessible or it passes through another unit
  // Returns a set of strings
  getReachableSquares(currentSquare: string, isPieceEngineer: boolean, boardState: { [key: string]: Piece | null }): Set<string> {
    // If the piece is not on the railroad, it can only move one space
    if (!this.isOnRail(currentSquare)) {
      return this.graph.getAdjacentNeighbors(currentSquare);
    }

    const singleRail: Set<string> = this.getAllRailroadsFromSquare(currentSquare);

    // BFS search on the railroads
    const reachableSquares: string[] = [];
    const visited = new Set<string>();

    visited.add(currentSquare);
    reachableSquares.push(currentSquare);

    while (reachableSquares.length != 0) {
      const iterSquare: string = reachableSquares.shift()!;
      const allNeighbors: Set<string> = this.graph.getAdjacentNeighbors(iterSquare);
      const neighborsOnRailroad: string[] = Array.from(allNeighbors).filter((nextSquare) => this.isOnRail(nextSquare));

      neighborsOnRailroad.forEach(function(nextSquare) {
        if (visited.has(nextSquare)) {
          return;
        }

        // if the piece is not an engineer, check if it is on the same railroad
        if (isPieceEngineer === false && singleRail.has(nextSquare) === false) {
          // skip if not accessible without turning
          return;
        }

        // visited
        visited.add(nextSquare);

        // empty
        if (boardState[nextSquare] === null) {
          // explore this square and its neighbors
          reachableSquares.push(nextSquare);
        }
      });
    }

    // include squares that aren't on the railroad
    const neighbors: Set<string> = this.graph.getAdjacentNeighbors(currentSquare);
    neighbors.forEach(function(square: string) {
      visited.add(square);
    });

    return visited;
  }

  isOnRail(currentSquare: string): boolean {
    return this.railLines.some((railLine) => railLine.includes(currentSquare));
  }

  getAllRailroadsFromSquare(currentSquare: string): Set<string> {
    // get all positions that are accessible on the same railroad
    const singleRail: string[] = this.railLines
      .filter((railroad) => railroad.includes(currentSquare))
      .flat();
    return new Set(singleRail);
  }
}
