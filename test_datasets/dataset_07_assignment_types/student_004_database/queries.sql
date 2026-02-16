
-- Create table
CREATE TABLE students (
    id INT PRIMARY KEY,
    name VARCHAR(100),
    grade FLOAT
);

-- Insert data
INSERT INTO students VALUES (1, 'Alice', 95.5);

-- Query
SELECT * FROM students WHERE grade > 90;
