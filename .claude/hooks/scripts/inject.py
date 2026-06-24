#!/usr/bin/env python3
import os
import json
import sys

def main():
    original_prompt = ""
    stdin_data = {}
    if not sys.stdin.isatty():
        try:
            raw_stdin = sys.stdin.read()
            if raw_stdin:
                stdin_data = json.loads(raw_stdin)
                original_prompt = stdin_data.get('prompt', stdin_data.get('message', stdin_data.get('text', stdin_data.get('input', ''))))
        except:
            pass

    # Resolve payload.json path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT', os.path.dirname(os.path.dirname(script_dir)))
    payload_path = os.path.join(plugin_root, 'payload.json')
    
    try:
        with open(payload_path, 'r') as f:
            payload = json.load(f)
            
        msg = payload.get('injectSteps', [{}])[0].get('ephemeralMessage', '')
        
        # Strip EPHEMERAL_MESSAGE tags which confuse the LLM
        msg = msg.replace('<EPHEMERAL_MESSAGE>', '').replace('</EPHEMERAL_MESSAGE>', '').strip()
        
        # Add a strong system directive prefix
        system_msg = f"*** CRITICAL SYSTEM INSTRUCTION ***\n{msg}\n*************************************"
        
        if original_prompt:
            new_prompt = f"{system_msg}\n\nUser Prompt: {original_prompt}"
        else:
            new_prompt = system_msg

        # Write the response for the UserPromptSubmit hook
        response = {
            "continue": True,
            "decision": "allow",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": system_msg
            }
        }
        sys.stdout.write(json.dumps(response))
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(2)

if __name__ == '__main__':
    main()
