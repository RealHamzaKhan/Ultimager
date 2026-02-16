"""Test cases for BST implementation."""
import unittest

class TestBST(unittest.TestCase):
    def test_insert_and_search(self):
        from q1_bst import BinarySearchTree
        bst = BinarySearchTree()
        bst.insert(10)
        bst.insert(5)
        bst.insert(15)
        self.assertEqual(bst.search(10), None)  # value is None
        self.assertIsNone(bst.search(99))

    def test_delete(self):
        from q1_bst import BinarySearchTree
        bst = BinarySearchTree()
        for v in [50, 30, 70]:
            bst.insert(v)
        bst.delete(30)
        self.assertEqual(bst.inorder(), [50, 70])

    def test_inorder(self):
        from q1_bst import BinarySearchTree
        bst = BinarySearchTree()
        for v in [5, 3, 7, 1, 4]:
            bst.insert(v)
        self.assertEqual(bst.inorder(), [1, 3, 4, 5, 7])

    def test_empty_tree(self):
        from q1_bst import BinarySearchTree
        bst = BinarySearchTree()
        self.assertIsNone(bst.search(1))
        self.assertEqual(bst.inorder(), [])

if __name__ == "__main__":
    unittest.main()
