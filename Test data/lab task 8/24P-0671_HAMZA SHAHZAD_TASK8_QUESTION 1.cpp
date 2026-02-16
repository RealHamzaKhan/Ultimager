#include<iostream>
using namespace std;

class Matrix{
private:
    int set[2][2];

public:
    Matrix(int a11,int a12,int a21,int a22){
    set[0][0] =a11; 
	set[0][1] =a12;
    set[1][0] =a21; 
	set[1][1] =a22;
    }

    void display(){
    cout<<set[0][0]<<" "<<set[0][1]<<endl;
    cout<<set[1][0]<<" "<<set[1][1]<<endl;
    }

    friend Matrix multiply(Matrix A,Matrix B);
};

Matrix multiply(Matrix A,Matrix B){
    Matrix result(
    A.set[0][0]*B.set[0][0] + A.set[0][1]*B.set[1][0],
	A.set[0][0]*B.set[0][1] + A.set[0][1]*B.set[1][1],
    A.set[1][0]*B.set[0][0] + A.set[1][1]*B.set[1][0],
    A.set[1][0]*B.set[0][1] + A.set[1][1]*B.set[1][1]
    );
    return result;
}

int main(){
    Matrix m1(10,12,15,19);
    Matrix m2(12,11,9,7);

    Matrix result =multiply(m1,m2);

    cout<<"Result :"<<endl;
    result.display();

    return 0;
}

