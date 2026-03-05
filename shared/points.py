# AMA-style points table (1st -> 22nd). Edit if your series changes.
AMA_POINTS = {
    1: 25, 2: 22, 3: 20, 4: 18, 5: 16, 6: 15, 7: 14, 8: 13, 9: 12, 10: 11,
    11: 10, 12: 9, 13: 8, 14: 7, 15: 6, 16: 5, 17: 4, 18: 3, 19: 2, 20: 1,
    21: 0, 22: 0,
}

def points_for_position(pos: int) -> int:
    if pos <= 0:
        return 0
    return AMA_POINTS.get(pos, 0)
