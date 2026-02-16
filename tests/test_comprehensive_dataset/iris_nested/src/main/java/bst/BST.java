// BinarySearchTree.java — Java implementation
public class BinarySearchTree {
    private class Node {
        int key;
        Node left, right;
        Node(int k) { key = k; left = right = null; }
    }

    private Node root;

    public void insert(int key) {
        root = insertRec(root, key);
    }

    private Node insertRec(Node root, int key) {
        if (root == null) return new Node(key);
        if (key < root.key) root.left = insertRec(root.left, key);
        else if (key > root.key) root.right = insertRec(root.right, key);
        return root;
    }

    public boolean search(int key) {
        return searchRec(root, key);
    }

    private boolean searchRec(Node root, int key) {
        if (root == null) return false;
        if (key == root.key) return true;
        return key < root.key ? searchRec(root.left, key) : searchRec(root.right, key);
    }

    public void delete(int key) {
        root = deleteRec(root, key);
    }

    private Node deleteRec(Node root, int key) {
        if (root == null) return null;
        if (key < root.key) root.left = deleteRec(root.left, key);
        else if (key > root.key) root.right = deleteRec(root.right, key);
        else {
            if (root.left == null) return root.right;
            if (root.right == null) return root.left;
            Node succ = minValue(root.right);
            root.key = succ.key;
            root.right = deleteRec(root.right, succ.key);
        }
        return root;
    }

    private Node minValue(Node node) {
        while (node.left != null) node = node.left;
        return node;
    }

    public void inorder() { inorderRec(root); System.out.println(); }
    private void inorderRec(Node root) {
        if (root != null) {
            inorderRec(root.left);
            System.out.print(root.key + " ");
            inorderRec(root.right);
        }
    }

    public static void main(String[] args) {
        BinarySearchTree bst = new BinarySearchTree();
        int[] keys = {50, 30, 70, 20, 40};
        for (int k : keys) bst.insert(k);
        System.out.print("Inorder: "); bst.inorder();
        System.out.println("Search 40: " + bst.search(40));
        bst.delete(30);
        System.out.print("After delete: "); bst.inorder();
    }
}
