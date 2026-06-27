#!/bin/sh
DIR="$(cd "$(dirname "$0")" && pwd)"
node -e "
const fs = require('fs');
const path = require('path');
try {
  const text = fs.readFileSync(path.join('$DIR', 'instructions.md'), 'utf8');
  console.log(JSON.stringify({
    injectSteps: [{ ephemeralMessage: text }]
  }));
} catch (e) {
  console.error(e);
  process.exit(1);
}
"
