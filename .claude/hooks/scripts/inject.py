#!/usr/bin/env python3
import json
import os
import sys

def main():
    # Consume stdin if provided by Claude hook system
    if not sys.stdin.isatty():
        try:
            _ = sys.stdin.read()
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
        
        # Write the response for the UserPromptSubmit hook
        response = {
            "continue": True,
            "suppressOutput": True,
            "systemMessage": msg
        }
    except Exception as e:
        # Fallback response in case of error
        response = {
            "continue": True,
            "suppressOutput": False,
            "systemMessage": f"Error loading vcs-edit payload: {str(e)}"
        }
        
    sys.stdout.write(json.dumps(response))
    
if __name__ == '__main__':
    main()
