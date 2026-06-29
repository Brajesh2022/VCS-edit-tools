import asyncio
import os
import json
import subprocess
from contextlib import AsyncExitStack

def run_vcs(args):
    return subprocess.run(["python3", "cli.py"] + args, capture_output=True, text=True)

def get_blob(filepath):
    res = run_vcs(["read", filepath])
    for line in res.stdout.split("\n"):
        if "blob:" in line:
            return line.split(" ")[1]
    return None

async def main():
    try:
        from mcp.client.stdio import stdio_client, StdioServerParameters
        from mcp.client.session import ClientSession
    except ImportError:
        print("mcp client not installed or imports failed")
        return

    # Setup tests
    with open("test1.txt", "w") as f:
        f.write("Line 1\nLine 2\nLine 3\n")
    with open("test2.txt", "w") as f:
        f.write("A\nB\nC\n")

    blob1 = get_blob("test1.txt")
    blob2 = get_blob("test2.txt")
    
    server_params = StdioServerParameters(
        command="python3",
        args=["mcp_server.py"]
    )
    
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        
        print("\n--- Testing Batch Edit ---")
        result = await session.call_tool(
            "vcs_edit",
            arguments={
                "edits": [
                    {
                        "action": "replace",
                        "filepath": "test1.txt",
                        "blob": blob1,
                        "start_line": 2,
                        "end_line": 2,
                        "content": "Line 2 MCP Replaced"
                    },
                    {
                        "action": "insert",
                        "filepath": "test1.txt",
                        "blob": blob1,
                        "line": 4,
                        "content": "Line 4 MCP Inserted"
                    },
                    {
                        "action": "delete",
                        "filepath": "test2.txt",
                        "blob": blob2,
                        "start_line": 2,
                        "end_line": 2
                    },
                    {
                        "action": "create",
                        "filepath": "test3.txt",
                        "content": "Hello World from MCP Create"
                    }
                ]
            }
        )
        print("Result:")
        print(result.content[0].text if result.content else "No output")
        
        print("\n--- Verifying Files ---")
        print("test1.txt:")
        with open("test1.txt") as f: print(f.read().strip())
        print("\ntest2.txt:")
        with open("test2.txt") as f: print(f.read().strip())
        print("\ntest3.txt:")
        with open("test3.txt") as f: print(f.read().strip())

if __name__ == "__main__":
    asyncio.run(main())
