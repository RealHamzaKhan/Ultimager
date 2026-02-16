// bst.cpp — C++ BST implementation
#include "bst.h"
#include <iostream>

BST::BST() : root(nullptr), sz(0) {}

BST::Node* BST::insert(Node* node, int key) {
    if (!node) { sz++; return new Node{key, nullptr, nullptr}; }
    if (key < node->key) node->left = insert(node->left, key);
    else if (key > node->key) node->right = insert(node->right, key);
    return node;
}

void BST::insert(int key) { root = insert(root, key); }

bool BST::search(int key) const {
    Node* cur = root;
    while (cur) {
        if (key == cur->key) return true;
        cur = key < cur->key ? cur->left : cur->right;
    }
    return false;
}

BST::Node* BST::findMin(Node* node) {
    while (node->left) node = node->left;
    return node;
}

BST::Node* BST::remove(Node* node, int key) {
    if (!node) return nullptr;
    if (key < node->key) node->left = remove(node->left, key);
    else if (key > node->key) node->right = remove(node->right, key);
    else {
        if (!node->left) { Node* r = node->right; delete node; sz--; return r; }
        if (!node->right) { Node* l = node->left; delete node; sz--; return l; }
        Node* succ = findMin(node->right);
        node->key = succ->key;
        node->right = remove(node->right, succ->key);
    }
    return node;
}

void BST::remove(int key) { root = remove(root, key); }

void BST::inorder(Node* node) const {
    if (node) { inorder(node->left); std::cout << node->key << " "; inorder(node->right); }
}

void BST::printInorder() const { inorder(root); std::cout << std::endl; }
int BST::size() const { return sz; }
