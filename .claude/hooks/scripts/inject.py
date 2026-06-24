#!/usr/bin/env python3
import json
import os
import sys

def main():
    plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT', '')
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
