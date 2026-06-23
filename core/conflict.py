"""3-way conflict resolution using Python's stdlib difflib.

Given:
  base    = the file content the agent saw (the blob at the agent's hash)
  ours    = the agent's intended new content for [line_range]
  theirs  = the file as it currently exists on disk

Two cases:
  Case 1: Conflicting change is OUTSIDE the agent's line range
          → Auto-merge: apply both changes cleanly.
  Case 2: Conflicting change OVERLAPS the agent's line range
          → Cannot auto-merge.  Return a structured conflict report so the
            agent can decide what to do next.

Line ranges are 1-indexed inclusive (matching the CLI syntax).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_lines(filepath: str) -> List[str]:
    """Read a file as a list of lines, preserving line endings."""
    with open(filepath, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read().splitlines(keepends=True)


def _parse_range(line_range: str, total: int) -> tuple[int, int]:
    """Parse 'START-END' (1-indexed inclusive) and clamp to file bounds."""
    if "-" not in line_range:
        n = int(line_range)
        return n, n
    a, b = line_range.split("-", 1)
    a = int(a) if a.strip() else 1
    b = int(b) if b.strip() else total
    if a < 1:
        a = 1
    if b > total:
        b = total
    if a > b:
        a, b = b, a
    return a, b


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

@dataclass
class ChangeRegion:
    """A maximal run of lines that differ between two versions of a file."""
    start: int  # 1-indexed, inclusive (base)
    end: int    # 1-indexed, inclusive (base)
    other_start: int # 1-indexed, inclusive (other)
    other_end: int   # 1-indexed, inclusive (other)


def _diff_regions(base_lines: List[str], other_lines: List[str]) -> List[ChangeRegion]:
    """Find all maximal regions where `other` differs from `base`.

    Uses difflib.SequenceMatcher.get_opcodes() and reports regions for
    any opcode other than 'equal'.
    """
    regions: List[ChangeRegion] = []
    matcher = difflib.SequenceMatcher(a=base_lines, b=other_lines, autojunk=False)
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # Map onto 1-indexed base line numbers
        start = a1 + 1
        end = a2 if a2 > a1 else a1 + 1  # insertions: a1==a2, treat as 1-line region
        other_start = b1 + 1
        other_end = b2 if b2 > b1 else b1 + 1
        
        # If pure insertion, the region is a 1-line region at the insertion point.
        if a1 == a2:
            start = a1 + 1
            end = a1 + 1
        if b1 == b2:
            other_start = b1 + 1
            other_end = b1 + 1
            
        regions.append(ChangeRegion(start=start, end=end, other_start=other_start, other_end=other_end))
    return regions


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Inclusive overlap test."""
    return not (a_end < b_start or b_end < a_start)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve(base_file: str, ours_file: str, theirs_file: str, line_range: str) -> dict:
    """Attempt to merge an agent's edit against a file that has changed.

    Args:
        base_file:   path to the file at the blob hash the agent holds
                     (i.e. what the agent saw when it called `read`)
        ours_file:   path to a tmp file containing the agent's intended new
                     content FOR THE GIVEN LINE RANGE (not the whole file)
        theirs_file: path to the file as it currently exists on disk
        line_range:  'START-END' (1-indexed inclusive) of the lines the agent
                     is trying to replace

    Returns one of:
      {"status": "auto_merged", "new_content": "<merged file as string>",
       "merged_regions": [{"start":..,"end":..}, ...]}

      {"status": "conflict",
       "conflicting_lines": "12-20",
       "base_content": "...",      # the lines from base at that range
       "their_change": "...",      # the lines from theirs at that range
       "your_change": "...",       # the lines from ours (the agent's new content)
       "diff": "..."}              # unified diff base↔theirs at that range
    """
    base_lines = _read_lines(base_file)
    ours_lines = _read_lines(ours_file)
    theirs_lines = _read_lines(theirs_file)

    total = len(base_lines)
    start, end = _parse_range(line_range, total)

    # Find what changed in `theirs` relative to `base`
    their_changes = _diff_regions(base_lines, theirs_lines)

    if not their_changes:
        # Should not normally happen — if their blob != base blob, something
        # changed.  Defensive: treat as no-op conflict.
        # Just apply ours.
        merged = base_lines[: start - 1] + ours_lines + base_lines[end:]
        return {
            "status": "auto_merged",
            "new_content": "".join(merged),
            "merged_regions": [],
        }

    # Find overlapping and non-overlapping changes
    overlapping = []
    non_overlapping = []
    for region in their_changes:
        if _ranges_overlap(start, end, region.start, region.end):
            overlapping.append(region)
        else:
            non_overlapping.append(region)

    if not overlapping:
        # Case 1: all of `their` changes are outside our edit range.
        # Strategy: take `theirs` as the new base (it's the freshest), then
        # splice our new content into the same logical line range of `theirs`.
        # But line numbers in `theirs` may have shifted due to their edits
        # ABOVE our range.  We need to recompute the range.

        offset = 0  # how many lines added (positive) or removed (negative) above our range
        for region in non_overlapping:
            if region.end < start:
                # Above our range — affects line numbering
                # Net change = (theirs_lines in region) - (base_lines in region)
                # But region.start/end are in base coordinates; we need the
                # theirs side too.  Use difflib again to count.
                pass  # handled below with a unified merge

        # Simpler: build the merged file by walking opcodes of base↔theirs,
        # and when we hit our edit range in base coordinates, substitute ours.
        merged = _merge_with_ours(base_lines, theirs_lines, ours_lines, start, end)
        return {
            "status": "auto_merged",
            "new_content": "".join(merged),
            "merged_regions": [
                {"start": r.start, "end": r.end} for r in non_overlapping
            ],
        }

    # Case 2: at least one change region overlaps our edit range.
    # Report the conflict to the agent.
    overlap_start = min(r.start for r in overlapping)
    overlap_end = max(r.end for r in overlapping)
    their_start = min(r.other_start for r in overlapping)
    their_end = max(r.other_end for r in overlapping)

    # Show the overlap region from each side
    base_slice = base_lines[max(0, overlap_start - 1) : overlap_end]
    theirs_slice = theirs_lines[max(0, their_start - 1) : their_end]
    ours_slice = ours_lines  # the agent already gave us the new content for the range

    base_slice_numbered = [f"{max(1, overlap_start) + i}: {line}" for i, line in enumerate(base_slice)]
    theirs_slice_numbered = [f"{max(1, their_start) + i}: {line}" for i, line in enumerate(theirs_slice)]

    diff_text = "".join(
        difflib.unified_diff(
            base_slice_numbered,
            theirs_slice_numbered,
            fromfile=f"base:{overlap_start}-{overlap_end}",
            tofile=f"theirs:{overlap_start}-{overlap_end}",
            lineterm="",
        )
    )

    return {
        "status": "conflict",
        "conflicting_lines": f"{overlap_start}-{overlap_end}",
        "base_content": "".join(base_slice),
        "their_change": "".join(theirs_slice),
        "your_change": "".join(ours_slice),
        "diff": diff_text,
    }


def _merge_with_ours(
    base_lines: List[str],
    theirs_lines: List[str],
    ours_lines: List[str],
    start: int,
    end: int,
) -> List[str]:
    """Merge theirs (outside our range) + ours (inside our range) → final file.

    Walks base↔theirs opcodes.  For each region of base:
      - equal / replace / delete outside [start-1, end) → take theirs side
      - inside [start-1, end) → take ours_lines (once) and skip base's portion
    """
    matcher = difflib.SequenceMatcher(a=base_lines, b=theirs_lines, autojunk=False)
    out: List[str] = []
    ours_inserted = False

    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        # a1, a2 are 0-indexed half-open in base
        # convert our 1-indexed inclusive range to 0-indexed half-open
        r_start = start - 1
        r_end = end  # half-open

        if a2 <= r_start or a1 >= r_end:
            # This opcode is entirely outside our range → take theirs
            out.extend(theirs_lines[b1:b2])
        elif a1 >= r_start and a2 <= r_end:
            # This opcode is entirely inside our range → take ours (once)
            if not ours_inserted:
                out.extend(ours_lines)
                ours_inserted = True
            # Skip theirs for this region too (we're replacing the whole range)
        else:
            # Opcode straddles our range boundary
            if tag == "equal":
                if a1 < r_start:
                    out.extend(theirs_lines[b1 : b1 + (r_start - a1)])
                if not ours_inserted:
                    out.extend(ours_lines)
                    ours_inserted = True
                if a2 > r_end:
                    out.extend(theirs_lines[b1 + (r_end - a1) : b2])
            else:
                out.extend(theirs_lines[b1:b2])

    # If our range was never inside any opcode (e.g. ours is at the very end
    # of the file with no changes after it), insert ours at the right place.
    if not ours_inserted:
        # Find the right spot: after (start-1) base lines worth of theirs.
        # Simpler approach: rebuild from scratch.
        out = _merge_with_ours_naive(base_lines, theirs_lines, ours_lines, start, end)

    return out


def _merge_with_ours_naive(
    base_lines: List[str],
    theirs_lines: List[str],
    ours_lines: List[str],
    start: int,
    end: int,
) -> List[str]:
    """Fallback merger: assume no offset shift below our range, splice ours in."""
    # Count net line offset introduced by theirs' edits strictly ABOVE our range
    matcher = difflib.SequenceMatcher(a=base_lines, b=theirs_lines, autojunk=False)
    offset = 0
    r_start = start - 1
    r_end = end

    # We can't simply walk opcodes because line numbers shift. Instead, build
    # the merge by:
    #   1. taking theirs verbatim
    #   2. finding where our [start..end] base range maps to in theirs
    #   3. replacing that mapped slice with ours

    # Map base line indices to theirs line indices using matching blocks
    matching_blocks = matcher.get_matching_blocks()
    # Find the matching block that contains base line `start-1` (the first line of our range)
    # If our range starts at a boundary between matching blocks, find the
    # theirs index right before it.

    # Find theirs_index corresponding to base_index = start-1 (0-indexed)
    def base_to_theirs(base_idx: int) -> int:
        """Return the theirs index corresponding to base_idx, or -1 if not in a matching block."""
        for bi, ti, size in matching_blocks:
            if bi <= base_idx < bi + size:
                return ti + (base_idx - bi)
        return -1

    # Find the closest matching block boundary at or before our range start
    # so we can compute the offset.
    pre_offset = 0
    for bi, ti, size in matching_blocks:
        if bi + size <= r_start:
            # This entire block is before our range
            pre_offset = (ti + size) - (bi + size)
        elif bi <= r_start:
            # Block covers our range start. Offset is exactly ti - bi.
            pre_offset = ti - bi
            break
        else:
            # Block starts after our range.
            break

    # Compute the start in theirs as: base_start + pre_offset (approx)
    # This works if all edits above our range are net insertions/deletions.
    theirs_start = r_start + pre_offset
    theirs_end = r_end + pre_offset  # approximate

    # Splice ours into theirs at [theirs_start, theirs_end)
    return theirs_lines[:theirs_start] + ours_lines + theirs_lines[theirs_end:]
