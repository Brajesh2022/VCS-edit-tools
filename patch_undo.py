def _do_undo(filepath, target_blob=None):
    from core.store import load_store, find_repo_root, load_snapshot
    from core.blob import get_blob_hash
    import os
    repo_root = find_repo_root()
    data = load_store(repo_root)
    order = data.get("_order", [])
    
    # Normalize filepath to relative path from repo root
    abs_path = os.path.abspath(filepath)
    try:
        rel_path = os.path.relpath(abs_path, repo_root)
    except ValueError:
        rel_path = abs_path
        
    file_history = [h for h, p in order if p == rel_path or p == abs_path]
    if not file_history:
        return {"status": "error", "message": f"no history for {filepath}"}
        
    current_blob = get_blob_hash(filepath)
    
    # Find the blob to rollback to
    if target_blob:
        target_lower = target_blob.lower()
        # Find it in history
        matches = [h for h in file_history if h.startswith(target_lower)]
        if not matches:
            return {"status": "error", "message": f"blob {target_blob} not in history for {filepath}"}
        rollback_blob = matches[0]
    else:
        # If no target blob, rollback to the previous one in history that differs from current
        # file_history is chronological (oldest first). We want the most recent one that is != current_blob
        rollback_blob = None
        for h in reversed(file_history):
            if not current_blob.startswith(h):
                rollback_blob = h
                break
        if not rollback_blob:
            return {"status": "error", "message": "no previous distinct state to undo to"}
            
    # Load snapshot and write it
    snapshot = load_snapshot(rollback_blob, repo_root)
    if snapshot is None:
        return {"status": "error", "message": f"snapshot missing for {rollback_blob}"}
        
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        f.write(snapshot)
        
    return {"status": "ok", "message": f"reverted to {rollback_blob[:8]}"}

def cmd_undo(args):
    """vcs undo <filepath> [blob]"""
    if not args:
        _error("usage: vcs undo <filepath> [blob]")
    filepath = args[0]
    blob = args[1] if len(args) > 1 else None
    
    if not os.path.exists(filepath):
        _error(f"file not found: {filepath}")
        
    result = _do_undo(filepath, blob)
    if result["status"] == "error":
        _error(result["message"])
    else:
        print(f"status: ok")
        print(f"message: {result['message']}")
        sys.exit(0)

def cmd_history(args):
    """vcs history <filepath>"""
    if not args:
        _error("usage: vcs history <filepath>")
    filepath = args[0]
    
    from core.store import load_store, find_repo_root
    from core.blob import get_blob_hash
    import os
    repo_root = find_repo_root()
    data = load_store(repo_root)
    order = data.get("_order", [])
    
    abs_path = os.path.abspath(filepath)
    try:
        rel_path = os.path.relpath(abs_path, repo_root)
    except ValueError:
        rel_path = abs_path
        
    file_history = [h for h, p in order if p == rel_path or p == abs_path]
    
    if not file_history:
        print("no history")
        return
        
    try:
        current_blob = get_blob_hash(filepath)
    except Exception:
        current_blob = None
        
    for h in file_history:
        marker = " (current)" if current_blob and current_blob.startswith(h) else ""
        print(f"{h[:8]}{marker}")
