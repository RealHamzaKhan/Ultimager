# -*- coding: utf-8 -*-
"""BST implementation with unicode comments — Ağaç Yapısı (Tree Structure)."""

class Düğüm:
    """Ağaç düğümü — Tree node."""
    def __init__(self, anahtar):
        self.anahtar = anahtar  # key
        self.sol = None         # left — 左
        self.sağ = None         # right — 右

class İkiliAramaAğacı:
    """İkili Arama Ağacı — Binary Search Tree — 二叉搜索树"""
    def __init__(self):
        self.kök = None  # root

    def ekle(self, anahtar):
        """Ekleme — Insert — 插入"""
        if self.kök is None:
            self.kök = Düğüm(anahtar)
        else:
            self._ekle(self.kök, anahtar)

    def _ekle(self, düğüm, anahtar):
        if anahtar < düğüm.anahtar:
            if düğüm.sol is None:
                düğüm.sol = Düğüm(anahtar)
            else:
                self._ekle(düğüm.sol, anahtar)
        else:
            if düğüm.sağ is None:
                düğüm.sağ = Düğüm(anahtar)
            else:
                self._ekle(düğüm.sağ, anahtar)

    def ara(self, anahtar):
        """Arama — Search — 搜索"""
        return self._ara(self.kök, anahtar)

    def _ara(self, düğüm, anahtar):
        if düğüm is None:
            return False
        if anahtar == düğüm.anahtar:
            return True
        elif anahtar < düğüm.anahtar:
            return self._ara(düğüm.sol, anahtar)
        else:
            return self._ara(düğüm.sağ, anahtar)

# Test: Çalıştırma — Execution — 実行
if __name__ == "__main__":
    ağaç = İkiliAramaAğacı()
    for değer in [50, 30, 70, 20, 40]:
        ağaç.ekle(değer)
    print(f"Arama 40: {ağaç.ara(40)}")  # True
    print(f"Arama 99: {ağaç.ara(99)}")  # False
