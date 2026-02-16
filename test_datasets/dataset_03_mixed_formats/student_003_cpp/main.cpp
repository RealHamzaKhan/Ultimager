
#include <iostream>
#include <cmath>

class Circle {
private:
    double radius;
public:
    Circle(double r) : radius(r) {}
    double getArea() {
        return M_PI * radius * radius;
    }
};

int main() {
    Circle c(5.0);
    std::cout << "Area: " << c.getArea() << std::endl;
    return 0;
}
