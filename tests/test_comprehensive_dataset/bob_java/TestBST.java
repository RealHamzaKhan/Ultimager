// TestBST.java
public class TestBST {
    public static void main(String[] args) {
        BinarySearchTree bst = new BinarySearchTree();
        bst.insert(10);
        bst.insert(5);
        bst.insert(15);
        assert bst.search(10) : "Search for 10 failed";
        assert !bst.search(99) : "Search for 99 should fail";
        bst.delete(10);
        assert !bst.search(10) : "10 should be deleted";
        System.out.println("All tests passed!");
    }
}
