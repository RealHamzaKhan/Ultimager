# Complexity Analysis — Binary Search Tree

## Time Complexity

| Operation | Average Case | Worst Case |
|-----------|-------------|------------|
| Insert    | O(log n)    | O(n)       |
| Search    | O(log n)    | O(n)       |
| Delete    | O(log n)    | O(n)       |
| Traversal | O(n)        | O(n)       |

### Discussion
The average case assumes a balanced tree where height h = O(log n).
The worst case occurs with a degenerate (skewed) tree where all nodes
form a single chain, effectively becoming a linked list with h = O(n).

## Space Complexity
- **Tree storage:** O(n) for n nodes
- **Recursive operations:** O(h) stack space where h is the tree height
- **In-order traversal (iterative):** O(h) with explicit stack

## Balancing Considerations
Self-balancing variants like AVL trees or Red-Black trees guarantee
O(log n) worst-case for all operations by maintaining balance invariants.
