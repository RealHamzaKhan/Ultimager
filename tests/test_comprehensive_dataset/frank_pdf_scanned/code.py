"""Partial BST — insert only, no delete."""

class BST:
    def __init__(self):
        self.root = None

    def insert(self, val):
        if not self.root:
            self.root = {"val": val, "left": None, "right": None}
        else:
            self._ins(self.root, val)

    def _ins(self, n, val):
        if val < n["val"]:
            if n["left"] is None:
                n["left"] = {"val": val, "left": None, "right": None}
            else:
                self._ins(n["left"], val)
        else:
            if n["right"] is None:
                n["right"] = {"val": val, "left": None, "right": None}
            else:
                self._ins(n["right"], val)
