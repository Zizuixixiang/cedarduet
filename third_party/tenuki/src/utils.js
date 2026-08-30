// Adapted from Tenuki src/utils.js: only helpers used by the rules/scoring
// core are retained. Upstream DOM/SVG helpers are intentionally not vendored.
export default {
  flatMap: function(ary, lambda) {
    return Array.prototype.concat.apply([], ary.map(lambda));
  },

  unique: function(ary) {
    let unique = [];
    ary.forEach(el => {
      if (unique.indexOf(el) < 0) {
        unique.push(el);
      }
    });
    return unique;
  }
};
