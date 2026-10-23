import os
import random

import psycopg
from faker import Faker
from psycopg import sql

SEED = 42
NUM_VEHICLES = 30
NUM_CUSTOMERS = 200

DSN = os.getenv("LOGISTREAM_DSN", "host=localhost port=5432 dbname=logistics user=logistics password=logistics")

HUBS = [
    ("Connaught Place Central", "New Delhi",      28.6315, 77.2167),
    ("Tagore Garden Depot",     "New Delhi",      28.6510, 77.1120),
    ("Dwarka Depot",            "Delhi",          28.5921, 77.0460),
    ("Rohini Depot",            "Delhi",          28.7495, 77.0565),
    ("Noida Sector 62 Depot",   "Noida",          28.6271, 77.3725),
    ("Greater Noida Depot",     "Greater Noida",  28.4744, 77.5040),
    ("Cyber City Depot",        "Gurugram",       28.4949, 77.0885),
    ("Mayur Vihar Depot",       "Delhi",          28.6040, 77.2940),
]

STATE_CODE = {
    "New Delhi": "DL",
    "Delhi": "DL",
    "Noida": "UP",
    "Greater Noida": "UP",
    "Gurugram": "HR",
}

LOCALITIES = {
    "New Delhi":     ["Connaught Place", "Lajpat Nagar", "Karol Bagh", "Hauz Khas"],
    "Delhi":         ["Rohini Sector 9", "Dwarka Sector 12", "Janakpuri", "Mayur Vihar Phase 1"],
    "Noida":         ["Sector 18", "Sector 62", "Sector 137", "Sector 76", "Sector 108"],
    "Greater Noida": ["Alpha 1", "Beta 2", "Gamma 1", "Pi Sector", "sector 1"],
    "Gurugram":      ["DLF Phase 2", "Sector 56", "Sohna Road", "Golf Course Road"],
}

rng = random.Random(SEED)
Faker.seed(SEED)
fake = Faker("en_IN")

def row_count(cur, table):
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))) #this line means that the code is executing a SQL query to count the number of rows in a specified table. The `cur.execute` method runs the SQL command, which is constructed using `sql.SQL` and `sql.Identifier` to safely insert the table name into the query. The result of this query will be the total number of rows present in the given table.
    return cur.fetchone()[0]

def make_plate(city):
    state = STATE_CODE[city]
    district = rng.randint(1, 15)
    letters = rng.choice("ABCDEFGHJKLMNPRSTUVXY") + rng.choice("ABCDEFGHJKLMNPRSTUVXY")
    return f"{state}-{district:02d}-{letters}-{rng.randint(1000, 9999)}" # Format: DL-01-AB-1234 (state, district, letters, number)

def make_address(city):
    house = rng.randint(1, 400)
    block = rng.choice(["A", "B", "C", "D", "E", "F"])
    locality = rng.choice(LOCALITIES[city])
    return f"{house}-{block}, {locality}, {city}" # Format: 123-A, Connaught Place, New Delhi

def seed_hubs(cur): #seed_hubs is a function that takes a database cursor (cur) as an argument and seeds the hubs table in the database with predefined hub data. It inserts each hub's name, city, latitude, and longitude into the hubs table, ensuring that if a hub with the same name already exists, it does not insert a duplicate. After inserting the hubs, it retrieves and returns a list of tuples containing the hub_id and city for all hubs in the database, ordered by hub_id.
    for name, city, lat, lon in HUBS:
        cur.execute(
            "INSERT INTO hubs (name, city, latitude, longitude) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
            (name, city, lat, lon),
        )
    cur.execute("SELECT hub_id, city FROM hubs ORDER BY hub_id")
    return cur.fetchall()  # list of (hub_id, city)

def seed_vehicles_and_drivers(cur, hubs):
    for _ in range(NUM_VEHICLES):
        hub_id, city = rng.choice(hubs)
        cur.execute(
            "INSERT INTO vehicles (plate_no, home_hub_id) VALUES (%s, %s) "
            "RETURNING vehicle_id",
            (make_plate(city), hub_id),
        )
        vehicle_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO drivers (name, phone, vehicle_id) VALUES (%s, %s, %s)",
            (fake.name(), fake.phone_number(), vehicle_id),
        )

def seed_customers(cur):
    cities = sorted({city for _, city, _, _ in HUBS})
    rows = []
    for _ in range(NUM_CUSTOMERS):
        city = rng.choice(cities)
        rows.append((fake.name(), fake.phone_number(), make_address(city), city))
    cur.executemany(
        "INSERT INTO customers (name, phone, address, city) VALUES (%s, %s, %s, %s)",
        rows,
    )

def main():
    with psycopg.connect(DSN) as conn:
        