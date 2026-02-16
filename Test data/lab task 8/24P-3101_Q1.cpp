#include <iostream>
using namespace std;

class Matrix
{
private:
    int data[2][2];

public:
    //Constructor to initialize the matrix
    Matrix(int a_11 = 0, int a_12 = 0, int a_21 = 0, int a_22 = 0)
        {
        data[0][0] = a_11; 
        data[0][1] = a_12;
        data[1][0] = a_21; 
        data[1][1] = a_22;
        }

    //Function to display the matrix
    void displayMatrix() const
        {
        cout<<data[0][0]<<" "<<data[0][1]<<endl;
        cout<<data[1][0]<<" "<<data[1][1]<<endl;
        }

    //Friend function declaration
    friend Matrix multiply(const Matrix&, const Matrix&);
};

// Friend function definition
Matrix multiply(const Matrix& m1, const Matrix& m2)
{
Matrix result;

result.data[0][0] = m1.data[0][0] * m2.data[0][0] + m1.data[0][1] * m2.data[1][0];
result.data[0][1] = m1.data[0][0] * m2.data[0][1] + m1.data[0][1] * m2.data[1][1];
result.data[1][0] = m1.data[1][0] * m2.data[0][0] + m1.data[1][1] * m2.data[1][0];
result.data[1][1] = m1.data[1][0] * m2.data[0][1] + m1.data[1][1] * m2.data[1][1];

return result;
}

int main()
{
Matrix M1(1, 2, 3, 4);
Matrix M2(5, 6, 7, 8);

cout<<"Matrix 1:"<<endl;
M1.displayMatrix();

cout<<"\nMatrix 2:"<<endl;
M2.displayMatrix();

Matrix M3 = multiply(M1, M2);

cout<<"\nProduct Matrix:"<<endl;
M3.displayMatrix();

return 0;
}
