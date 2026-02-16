// bst.h — BST header
#ifndef BST_H
#define BST_H

class BST {
    struct Node { int key; Node* left; Node* right; };
    Node* root;
    int sz;
    Node* insert(Node* node, int key);
    Node* remove(Node* node, int key);
    Node* findMin(Node* node);
    void inorder(Node* node) const;
public:
    BST();
    void insert(int key);
    bool search(int key) const;
    void remove(int key);
    void printInorder() const;
    int size() const;
};

#endif
