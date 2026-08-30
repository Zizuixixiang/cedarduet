import {readFile} from "node:fs/promises";

const CORE_URL = new URL("../runtime/core.js", import.meta.url).href;
const SHIM_EXPORTS = {
  Board: ["Board"],
  BoardConstants: [
    "BUNKER_SQUARES", "HEADQUARTER_SQUARES", "HEADQUARTER_SQUARE_GROUP",
  ],
  BoardValidator: ["BoardValidator"],
  Graph: ["Graph"],
  Piece: ["Piece", "PieceRank", "GameResult"],
  RailroadNetwork: ["RailroadNetwork"],
};

export async function resolve(specifier, context, nextResolve) {
  const match = specifier.match(/^\.\.\/src\/lib\/([A-Za-z]+)$/);
  if (match && SHIM_EXPORTS[match[1]]) {
    return {url: `junqi-runtime:${match[1]}`, shortCircuit: true};
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.startsWith("junqi-runtime:")) {
    const moduleName = url.slice("junqi-runtime:".length);
    const names = SHIM_EXPORTS[moduleName];
    const exports = names.map(
      (name) => `export const ${name} = core.${name};`
    ).join("\n");
    return {
      format: "module",
      shortCircuit: true,
      source: `import core from ${JSON.stringify(CORE_URL)};\n${exports}\n`,
    };
  }
  if (url.endsWith(".test.ts")) {
    const original = await readFile(new URL(url), "utf8");
    // The relevant upstream tests are plain JavaScript except for TypeScript's
    // non-null assertion operator. Strip only that token at load time so the
    // checked-in native test sources remain unchanged.
    const source = original
      .replace(/([\]\)])!([.);,])/g, "$1$2")
      .replace(/([A-Za-z0-9_'"\]])!\./g, "$1.");
    return {format: "module", shortCircuit: true, source};
  }
  return nextLoad(url, context);
}
