#include <iostream>
#include <string>
using namespace std;

//Forward declaration
class Printer;

class Document
{
private:
    string content;

public:
    //Constructor
    Document(string text) : content(text)
        {

        }

    //Method that Refuses to Show Content
    void viewContent() const
        {
        cout<<"Content is restricted."<<endl;
        }

    //Declaring Printer as a Friend Class
    friend class Printer;
};

class Printer
{
public:
    //Can Access Private Content because Printer is a Friend
    void printDocument(const Document& doc) const
        {
        cout<<"Printing Document: "<<doc.content<<endl;
        }
};

int main()
{
Document doc("This is a confidential report.");
Printer printer;

cout<<"Trying to view content directly:"<<endl;
doc.viewContent();                  //This should NOT Show Actual Content

cout<<"\nUsing printer to access content:"<<endl;
printer.printDocument(doc);         //This should Print Actual Private Content

return 0;
}
