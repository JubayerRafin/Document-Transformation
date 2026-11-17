"""Broad-phase filtering; exact PDF overlap semantics remain in the caller."""
import heapq
import math


def candidate_pairs(rectangles):
    """Yield each intersecting candidate in original (i, j) order.

    Sweep on y so separate text lines expire quickly. Degenerate/non-finite
    geometry falls back to exhaustive candidates to preserve caller behavior.
    """
    if any(not r.is_valid or not all(math.isfinite(v) for v in r) for r in rectangles):
        return ((i,j) for i in range(len(rectangles)) for j in range(i+1,len(rectangles)))
    active={};ends=[];pairs=[]
    for i in sorted(range(len(rectangles)),key=lambda i:(rectangles[i].y0,i)):
        rect=rectangles[i]
        while ends and ends[0][0]<=rect.y0:
            _,previous=heapq.heappop(ends);active.pop(previous,None)
        if rect.is_empty:continue
        for j,other in active.items():
            if rect.x0<other.x1 and other.x0<rect.x1:
                pairs.append((min(i,j),max(i,j)))
        active[i]=rect;heapq.heappush(ends,(rect.y1,i))
    return sorted(pairs)
