#include <iostream>
using namespace std;

class Printer;

class Document{
private:
    string content;

public:
    Document(string t){
    content = t;
    }

    void view(){
    cout<<"Cannot see content."<<endl;
    }
	friend class Printer;  
};

class Printer{
public:
    void printdoc(Document& doc) {
        cout << "Printing: "<<doc.content<<endl;
    }
};

int main(){
    Document d1("Top Secret Message");

    d1.view();  

    Printer p1;
    p1.printdoc(d1);  

    return 0;
}

