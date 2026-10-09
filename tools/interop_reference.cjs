// Test reference only. ECMAScript/V8 supplies number formatting and UTF-16 ordering.
// Authored glue, not an independently audited JCS implementation. No eval or network.
'use strict';
const fs = require('fs');
const crypto = require('crypto');
const data = JSON.parse(fs.readFileSync(0,'utf8'));
function canonical(v) {
  if (v === null || typeof v !== 'object') {
    if (typeof v === 'number' && !Number.isFinite(v)) throw Error('nonfinite');
    return JSON.stringify(v);
  }
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
}
const numbers = data.numbers.map(hex => {
  const n = Buffer.from(hex,'hex').readDoubleBE(0);
  return Number.isFinite(n) ? JSON.stringify(n) : null;
});
const records = data.records.map(text => {
  const value = canonical(JSON.parse(text));
  return {canonical_hex:Buffer.from(value,'utf8').toString('hex'),sha256:crypto.createHash('sha256').update(value,'utf8').digest('hex')};
});
process.stdout.write(JSON.stringify({node:process.version,v8:process.versions.v8,numbers,records}));
