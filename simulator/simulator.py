"""Live courier simulator
 
Every tick it:
  1. creates new orders (more at peak hours),
  2. moves due orders through created -> packed -> in_transit -> delivered,
     assigning vehicles, creating shipments and writing scan events,
  3. injects rare problems: incidents, cancellations, duplicate updates, late scans,
  4. commits everything as one batch, so Debezium sees it together.
 
Safe to stop with Ctrl+C and start again: it rebuilds its in-progress orders
from Postgres.
"""

import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
import yaml

DSN = os.getenv(
    "LOGISTREAM_DSN",
    "host=localhost port=5432 dbname=logistics user=logistics password=logistics",
)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

IST = timezone(timedelta(hours=5, minutes=30))

# Simulated minutes an order spends in each stage before it moves on (low, high)
HANDLING_MINUTES = {"created": (5, 20), "packed": (10, 40)}

# Average road speed in km/h, and how much longer roads are than a straight line
AVG_SPEED_KMH = 30
ROAD_FACTOR = 1.3

INCIDENT_TEMPLATES = [
    "Vehicle {plate} broke down near {hub}, waiting for a tow.",
    "Engine failure on {plate} close to {hub}; replacement driver called.",
    "{plate} has a flat tyre near {hub}, delivery delayed.",
    "Waterlogging near {hub} stopped {plate}; rerouting.",
    "Road closed near {hub}, {plate} is taking a longer route.",
]
