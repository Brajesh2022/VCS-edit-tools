#!/bin/bash

# Read the JSON payload sent by Antigravity from stdin
INPUT=$(cat)

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if echo "$INPUT" | grep -q '"invocationNum":[[:space:]]*0\b'; then
  # Trigger only on the first invocation
  INSTRUCTIONS=$(cat "$DIR/instructions.md")
  
  # Construct the ephemeral message using jq
  jq -n --arg inst "$INSTRUCTIONS" '
  {
    "injectSteps": [
      {
        "ephemeralMessage": ("\n\n" + $inst)
      }
    ]
  }
  '
else
  # For subsequent invocations, read from next_message.md
  NEXT_MSG=$(cat "$DIR/next_message.md" 2>/dev/null || echo "")
  
  if [ -n "$NEXT_MSG" ]; then
    jq -n --arg msg "$NEXT_MSG" '
    {
      "injectSteps": [
        {
          "ephemeralMessage": $msg
        }
      ]
    }
    '
  else
    echo '{"injectSteps": []}'
  fi
fi
