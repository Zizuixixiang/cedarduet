"use strict";

const fs = require("node:fs");
const {dispatch} = require("./runtime/core.js");

try {
  const request = JSON.parse(fs.readFileSync(0, "utf8"));
  process.stdout.write(JSON.stringify({ok: true, result: dispatch(request)}));
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: error instanceof Error ? error.message : String(error),
  }));
}
