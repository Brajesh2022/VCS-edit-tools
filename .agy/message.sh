#!/bin/sh
DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="$DIR" python3 -c "
import os, json, sys
try:
    with open(os.path.join(os.environ['DIR'], 'instructions.md'), 'r', encoding='utf-8') as f:
        text = f.read()
    print(json.dumps({'injectSteps': [{'ephemeralMessage': text}]}))
except Exception as e:
    print(e, file=sys.stderr)
    sys.exit(1)
"
