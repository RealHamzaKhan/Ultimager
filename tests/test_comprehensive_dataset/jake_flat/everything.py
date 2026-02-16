"""Single-file complete submission — all answers in one file."""

# ============ Problem 1: BST Implementation ============
class BST:
    def __init__(self): self.root = None
    def insert(self, v):
        if not self.root: self.root = {"v": v, "l": None, "r": None}
        else: self._ins(self.root, v)
    def _ins(self, n, v):
        if v < n["v"]:
            if not n["l"]: n["l"] = {"v": v, "l": None, "r": None}
            else: self._ins(n["l"], v)
        else:
            if not n["r"]: n["r"] = {"v": v, "l": None, "r": None}
            else: self._ins(n["r"], v)
    def search(self, v):
        return self._srch(self.root, v)
    def _srch(self, n, v):
        if not n: return False
        if v == n["v"]: return True
        return self._srch(n["l"], v) if v < n["v"] else self._srch(n["r"], v)

# ============ Problem 2: Analysis ============
# Insert: O(log n) avg, O(n) worst
# Search: O(log n) avg, O(n) worst
# Delete: not implemented
# Space: O(n)

# ============ Problem 3: Tests ============
if __name__ == "__main__":
    t = BST()
    for x in [5, 3, 7, 1, 4, 6, 8]:
        t.insert(x)
    assert t.search(4), "FAIL: 4 should exist"
    assert not t.search(99), "FAIL: 99 should not exist"
    print("All tests passed!")
