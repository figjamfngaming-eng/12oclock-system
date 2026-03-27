CREATE TABLE riders (
id SERIAL PRIMARY KEY,
mxb_name TEXT,
guid TEXT,
class_name TEXT
);

CREATE TABLE events (
id SERIAL PRIMARY KEY,
name TEXT,
class_name TEXT,
race_stage TEXT DEFAULT 'qualifying'
);

CREATE TABLE registrations (
id SERIAL PRIMARY KEY,
event_id INT,
rider_id INT
);

CREATE TABLE results (
id SERIAL PRIMARY KEY,
event_id INT,
rider_id INT,
position INT,
points INT
);

CREATE TABLE gate_orders (
id SERIAL PRIMARY KEY,
event_id INT,
rider_id INT,
gate_order INT
);
