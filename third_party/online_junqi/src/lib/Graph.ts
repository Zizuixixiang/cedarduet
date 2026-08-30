/*
* Defines the adjacency graph for nodes on the board.
*/
import { BUNKER_SQUARES, FRONT_ROW_SQUARES, HEADQUARTER_SQUARES } from './BoardConstants';

interface TransformOffset {
  x: number;
  y: number;
}

// Bunker squares connect in 8 directions
const bunkerTransforms: TransformOffset[] = [
  {x:+0, y:+1}, {x:+1, y:+1},
  {x:+1, y:+0}, {x:+1, y:-1},
  {x:+0, y:-1}, {x:-1, y:-1},
  {x:-1, y:+0}, {x:-1, y:+1}
];
// Most squares connect in 4 directions
const crossTransforms: TransformOffset[] = [
  {x:+0, y:+1}, {x:+1, y:+0},
  {x:+0, y:-1}, {x:-1, y:+0}
]

export class Graph {
  nodes: string[];
  neighborMap: { [key: string]: Set<string> };

  constructor() {
    const graphNodesEdges = this.initializeGraph();

    this.nodes = graphNodesEdges.nodes;
    this.neighborMap = graphNodesEdges.neighborMap;
  }

  getAdjacentNeighbors(currentSquare: string): Set<string> {
    return this.neighborMap[currentSquare];
  }

  initializeGraph() {
    const nodes = this.initializeNodes();
    const neighborMap = this.initializeEdges(nodes);

    return {
      nodes: nodes,
      neighborMap: neighborMap
    }
  }

  initializeNodes(): string[] {
    const allNodes: string[] = [];
    const columnList: string[] = ['a', 'b', 'c', 'd', 'e'];
    columnList.forEach(function(columnChar) {
      for (let row = 1; row <= 12; row++) {
        const nodeKey = columnChar + row;
        allNodes.push(nodeKey);
      }
    });

    return allNodes;
  }

  initializeEdges(nodes: string[]): { [key: string]: Set<string> } {
    const neighborMap: { [key: string]: Set<string> } = {};

    nodes.forEach(currentSquare => {
      // Exclude front row because the neighbor could end up in the opponent's side
      if (FRONT_ROW_SQUARES.includes(currentSquare)) {
        return;
      }
      const adjacentSquares: string[] = this.getMoves(currentSquare, crossTransforms);
      adjacentSquares.forEach(newSquare => {
        this.addEdge(neighborMap, currentSquare, newSquare);
      });
    });

    BUNKER_SQUARES.forEach(currentSquare => {
      const adjacentSquares: string[] = this.getMoves(currentSquare, bunkerTransforms);
      adjacentSquares.forEach(newSquare => {
        this.addEdge(neighborMap, currentSquare, newSquare);
      });
    });

    this.addEdgesInFrontRowSamePlayer(neighborMap)
    this.addEdgesBetweenDifferentPlayers(neighborMap);

    // Set headquarter squares to immobile
    HEADQUARTER_SQUARES.forEach(function(square: string) {
      neighborMap[square].clear();
    });

    return neighborMap;
  }

  getMoves(square: string, transforms: TransformOffset[]): string[] {
      return transforms
        .map((move) => this.transformSquare(square, move))
        .filter((newSquare): newSquare is string => !!newSquare);
  }

  addEdgesInFrontRowSamePlayer(neighborMap: { [key: string]: Set<string> }): void {
    // Player 1: a6 <-> b6 <-> c6 <-> d6 <-> e6
    this.addEdge(neighborMap, "b6", "a6");
    this.addEdge(neighborMap, "b6", "c6");
    this.addEdge(neighborMap, "d6", "c6");
    this.addEdge(neighborMap, "d6", "e6");
    // Player 2: a7 <-> b7 <-> c7 <-> d7 <-> e7
    this.addEdge(neighborMap, "b7", "a7");
    this.addEdge(neighborMap, "b7", "c7");
    this.addEdge(neighborMap, "d7", "c7");
    this.addEdge(neighborMap, "d7", "e7");
  }

  addEdgesBetweenDifferentPlayers(neighborMap: { [key: string]: Set<string> }): void {
    // Add edges between front row nodes on the opposite side
    this.addEdge(neighborMap, "a6", "a7");
    this.addEdge(neighborMap, "c6", "c7");
    this.addEdge(neighborMap, "e6", "e7");
  }

  /**
   * Apply an x and y offset to a starting square to get a destination square.
   * Returns the destination square on success, otherwise returns null
   */
  transformSquare(square: string, transform: TransformOffset): string | null {
    // Parse square
    const column = square[0];
    const row = parseInt(square.substring(1, square.length), 10);

    // Apply transform
    const destColumn = Graph.alpha2num(column) + transform.x;
    const destRow = row + transform.y;

    // Check boundaries
    if (destColumn < 1 || destColumn > 5) { return null; }
    if (destRow < 1 || destRow > 12) { return null; }

    // Return new square
    return Graph.num2alpha(destColumn) + destRow;
  };

  // Add an unconnected edge between node1 and node2
  addEdge(neighborMap: { [key: string]: Set<string> }, node1Key: string, node2Key: string): void {
    // Initialize an empty list for the new nodes
    if (!(node1Key in neighborMap)) {
      neighborMap[node1Key] = new Set<string>();
    }
    if (!(node2Key in neighborMap)) {
      neighborMap[node2Key] = new Set<string>();
    }

    // Add the nodes
    neighborMap[node1Key].add(node2Key);
    neighborMap[node2Key].add(node1Key);
  }

  private static alpha2num(a: string): number {
    switch (a) {
      case 'a': return 1;
      case 'b': return 2;
      case 'c': return 3;
      case 'd': return 4;
      case 'e': return 5;
      default: return 6; // out of bounds
    }
  }

  private static num2alpha(n: number): string {
    switch (n) {
      case 1: return 'a';
      case 2: return 'b';
      case 3: return 'c';
      case 4: return 'd';
      case 5: return 'e';
      default: return 'f'; // out of bounds
    }
  }
}
