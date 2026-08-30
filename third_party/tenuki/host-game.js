import BoardState from "./src/board-state.js";
import Ruleset from "./src/ruleset.js";
import Scorer from "./src/scorer.js";

export const BOARD_SIZE = 19;
export const KOMI = 7.5;
export const KO_RULE = "positional-superko";
export const SCORING = "area";

function checkedCoordinate(value, name) {
  if (!Number.isInteger(value) || value < 0 || value >= BOARD_SIZE) {
    throw new Error(`${name} 必须是 0–${BOARD_SIZE - 1} 的整数`);
  }
  return value;
}

function checkedHistory(history) {
  if (!Array.isArray(history) || history.length > 2000) {
    throw new Error("history 必须是至多 2000 手的数组");
  }
  return history.map((entry, index) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(`第 ${index + 1} 手不是对象`);
    }
    if (entry.action === "pass") return {action: "pass"};
    if (entry.action !== "play") {
      throw new Error(`第 ${index + 1} 手 action 无效`);
    }
    return {
      action: "play",
      row: checkedCoordinate(entry.row, "row"),
      col: checkedCoordinate(entry.col, "col"),
    };
  });
}

function pointKey(point) {
  return `${point.y},${point.x}`;
}

export function sortedPoints(points) {
  return points.map((point) => ({row: point.y, col: point.x})).sort(
    (left, right) => left.row - right.row || left.col - right.col
  );
}

export class HostGame {
  constructor(history = [], deadStones = []) {
    this.boardSize = BOARD_SIZE;
    this._initialState = BoardState._initialFor(BOARD_SIZE, 0);
    this._moves = [];
    this._deadPoints = [];
    this._ruleset = new Ruleset({koRule: KO_RULE});
    this._scorer = new Scorer({scoreBy: SCORING, komi: KOMI});

    checkedHistory(history).forEach((move, index) => {
      const accepted = move.action === "pass"
        ? this.pass()
        : this.playAt(move.row, move.col);
      if (!accepted) throw new Error(`无法重放第 ${index + 1} 手`);
    });
    this.setDeadStones(deadStones);
  }

  currentState() {
    return this._moves[this._moves.length - 1] || this._initialState;
  }

  currentPlayer() {
    return this.currentState().nextColor();
  }

  intersections() {
    return this.currentState().intersections;
  }

  intersectionAt(y, x) {
    return this.currentState().intersectionAt(y, x);
  }

  isIllegalAt(y, x) {
    return this._ruleset.isIllegal(y, x, this);
  }

  playAt(y, x) {
    checkedCoordinate(y, "row");
    checkedCoordinate(x, "col");
    if (this.isOver() || this.isIllegalAt(y, x)) return false;

    let newState = this.currentState().playAt(y, x, this.currentPlayer());
    const {koPoint} = newState;
    if (
      koPoint
      && !this._ruleset._isKoViolation(
        koPoint.y,
        koPoint.x,
        newState,
        this._moves.concat(newState)
      )
    ) {
      newState = newState.copyWithAttributes({koPoint: null});
    }
    this._moves.push(newState);
    this._deadPoints = [];
    return true;
  }

  pass() {
    if (this.isOver()) return false;
    this._moves.push(this.currentState().playPass(this.currentPlayer()));
    this._deadPoints = [];
    return true;
  }

  isOver() {
    if (this._moves.length < 2) return false;
    const finalMove = this._moves[this._moves.length - 1];
    const previousMove = this._moves[this._moves.length - 2];
    return finalMove.pass && previousMove.pass;
  }

  deadStones() {
    return this._deadPoints;
  }

  _isDeadAt(y, x) {
    return this._deadPoints.some((point) => point.y === y && point.x === x);
  }

  toggleDeadAt(y, x) {
    if (!this.isOver()) return false;
    return this._setDeadStatus(y, x, !this._isDeadAt(y, x));
  }

  markDeadAt(y, x) {
    if (this._isDeadAt(y, x)) return true;
    return this._setDeadStatus(y, x, true);
  }

  _setDeadStatus(y, x, markingDead) {
    checkedCoordinate(y, "row");
    checkedCoordinate(x, "col");
    const selectedIntersection = this.intersectionAt(y, x);
    if (selectedIntersection.isEmpty()) return false;

    const chosenDead = [];
    const [candidates] = this.currentState().partitionTraverse(
      selectedIntersection,
      (intersection) => (
        intersection.isEmpty()
        || intersection.sameColorAs(selectedIntersection)
      )
    );
    candidates.forEach((sameColorOrEmpty) => {
      if (!sameColorOrEmpty.isEmpty()) chosenDead.push(sameColorOrEmpty);
    });
    chosenDead.forEach((intersection) => {
      if (markingDead) {
        if (!this._isDeadAt(intersection.y, intersection.x)) {
          this._deadPoints.push({y: intersection.y, x: intersection.x});
        }
      } else {
        this._deadPoints = this._deadPoints.filter(
          (dead) => !(dead.y === intersection.y && dead.x === intersection.x)
        );
      }
    });
    return true;
  }

  setDeadStones(deadStones) {
    if (!Array.isArray(deadStones) || deadStones.length > BOARD_SIZE * BOARD_SIZE) {
      throw new Error("dead_stones 必须是棋盘坐标数组");
    }
    if (deadStones.length && !this.isOver()) {
      throw new Error("只有连续两次 pass 后才能标记死子");
    }
    const requested = deadStones.map((point) => {
      if (!point || typeof point !== "object" || Array.isArray(point)) {
        throw new Error("dead_stones 含无效坐标");
      }
      return {
        y: checkedCoordinate(point.row, "row"),
        x: checkedCoordinate(point.col, "col"),
      };
    });
    if (new Set(requested.map(pointKey)).size !== requested.length) {
      throw new Error("dead_stones 不能含重复坐标");
    }
    requested.forEach((point) => {
      if (!this.markDeadAt(point.y, point.x)) {
        throw new Error("dead_stones 只能标记已有棋子");
      }
    });
    const canonical = new Set(this._deadPoints.map(pointKey));
    if (
      canonical.size !== requested.length
      || requested.some((point) => !canonical.has(pointKey(point)))
    ) {
      throw new Error("dead_stones 必须包含 Tenuki 选中的完整棋串集合");
    }
  }

  territory() {
    if (!this.isOver()) return {black: [], white: []};
    return this._scorer.territory(this);
  }

  score() {
    if (!this.isOver()) throw new Error("尚未进入计分阶段");
    return this._scorer.score(this);
  }

  legalActions() {
    if (this.isOver()) return [];
    const actions = [];
    for (let row = 0; row < BOARD_SIZE; row += 1) {
      for (let col = 0; col < BOARD_SIZE; col += 1) {
        if (!this.isIllegalAt(row, col)) {
          actions.push({action: "play", row, col});
        }
      }
    }
    actions.push({action: "pass"});
    return actions;
  }

  positionIdentity(state = this.currentState()) {
    return state.intersections.map((point) => (
      point.isBlack() ? "B" : point.isWhite() ? "W" : "."
    )).join("");
  }
}

export function checkedEngineHistory(history) {
  return checkedHistory(history);
}
