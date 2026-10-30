"""Live courier simulator
 
Every tick it:
  1. creates new orders (more at peak hours),
  2. moves due orders through created -> packed -> in_transit -> delivered,
     assigning vehicles, creating shipments and writing scan events,
  3. injects rare problems: incidents, cancellations, duplicate updates, late scans,
  4. commits everything as one batch, so Debezium sees it together.
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

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two points on Earth, in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def poisson(rng, lam):
    """Random whole number with average `lam` (Knuth's method, fine for small lam)."""
    if lam <= 0:
        return 0
    limit = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1

def demand_multiplier(sim_now):
    """Busier in the morning and evening, quiet at night (India time)."""
    hour = sim_now.astimezone(IST).hour
    if 9 <= hour < 12 or 17 <= hour < 20:
        return 1.6
    if hour < 6:
        return 0.2
    return 1.0

@dataclass 
class ActiveOrder:
    order_id: int
    status: str
    origin: int
    destination: int
    due: datetime                       # simulated time when it moves on
    shipment_id: int | None = None
    vehicle_id: int | None = None
    planned_arrival: datetime | None = None

class Simulator:
    def __init__(self, conn, cfg):
        self.conn = conn
        self.cur = conn.cursor()
        self.cfg = cfg
        self.rng = random.Random(cfg["seed"])
        self.sim_now = datetime.now(timezone.utc)
        self.active: dict[int, ActiveOrder] = {}
        self.counts = {"orders": 0, "delivered": 0, "cancelled": 0, "incidents": 0}
        self.load_reference()
        self.resume()

    # ---------- setup ----------
 
    def load_reference(self):
        cur = self.cur
        cur.execute("SELECT hub_id, name, latitude, longitude FROM hubs ORDER BY hub_id")
        self.hubs = {
            r[0]: {"name": r[1], "lat": r[2], "lon": r[3]} for r in cur.fetchall()
        }
        cur.execute("SELECT customer_id FROM customers ORDER BY customer_id")
        self.customer_ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT vehicle_id, plate_no FROM vehicles ORDER BY vehicle_id")
        self.plates = dict(cur.fetchall())
        if len(self.hubs) < 2 or not self.customer_ids or not self.plates:
            raise SystemExit("Reference data missing. Run seed_reference.py first.")

    def resume(self):
        """Pick up orders left in progress by an earlier run."""
        self.cur.execute(
            """
            SELECT o.order_id, o.status, o.origin_hub_id, o.destination_hub_id,
                   s.shipment_id, s.vehicle_id, s.planned_arrival
            FROM orders o
            LEFT JOIN shipments s
                   ON s.order_id = o.order_id AND s.status = 'in_transit'
            WHERE o.status IN ('created', 'packed', 'in_transit')
            """
        )
        for oid, status, origin, dest, ship_id, veh_id, planned in self.cur.fetchall():
            if status == "in_transit":
                if ship_id is None:
                    continue
                due = max(planned, self.sim_now) + timedelta(
                    minutes=self.rng.randint(1, 30)
                )
            else:
                due = self.sim_now + self.handling_time(status)
            self.active[oid] = ActiveOrder(
                oid, status, origin, dest, due, ship_id, veh_id, planned
            )
        if self.active:
            print(f"Resumed {len(self.active)} orders already in progress.")

   # helpers 
 
    def log(self, kind, message):
        print(f"  {kind:<9} {message}")
 
    def handling_time(self, stage):
        return timedelta(minutes=self.rng.randint(*HANDLING_MINUTES[stage]))
 
    def add_event(self, shipment_id, hub_id, event_type, notes=None, event_time=None):
        self.cur.execute(
            "INSERT INTO shipment_events (shipment_id, hub_id, event_type, event_time, notes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (shipment_id, hub_id, event_type, event_time or self.sim_now, notes),
        )
 
    def set_status(self, order, new_status):
        old = order.status
        self.cur.execute(
            "UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s",
            (new_status, self.sim_now, order.order_id),
        )
        order.status = new_status
        self.log("MOVE", f"order {order.order_id}: {old} -> {new_status}")
 
    def pick_vehicle(self, origin_hub):
        """Prefer a free vehicle based at the origin hub, otherwise any free one."""
        self.cur.execute(
            "SELECT vehicle_id, home_hub_id FROM vehicles "
            "WHERE status = 'available' ORDER BY vehicle_id"
        )
        rows = self.cur.fetchall()
        if not rows:
            return None
        local = [v for v, hub in rows if hub == origin_hub]
        return self.rng.choice(local or [v for v, _ in rows])

   # ---------- the four things that happen each tick ----------
 
    def new_orders(self):
        rate = self.cfg["orders_per_tick"] * demand_multiplier(self.sim_now)
        for _ in range(poisson(self.rng, rate)):
            origin, destination = self.rng.sample(list(self.hubs), 2)
            customer = self.rng.choice(self.customer_ids)
            weight = round(self.rng.uniform(0.5, 30.0), 2)
            self.cur.execute(
                "INSERT INTO orders (customer_id, origin_hub_id, destination_hub_id, "
                "weight_kg, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, 'created', %s, %s) RETURNING order_id",
                (customer, origin, destination, weight, self.sim_now, self.sim_now),
            )
            order_id = self.cur.fetchone()[0]
            self.active[order_id] = ActiveOrder(
                order_id, "created", origin, destination,
                self.sim_now + self.handling_time("created"),
            )
            self.counts["orders"] += 1
            self.log("NEW", f"order {order_id}")
 
    def process_due(self):
        for order in list(self.active.values()):
            if order.due > self.sim_now:
                continue
            can_cancel = order.status in ("created", "packed")
            if can_cancel and self.rng.random() < self.cfg["cancel_probability"]:
                self.cancel(order)
            elif order.status == "created":
                self.set_status(order, "packed")
                order.due = self.sim_now + self.handling_time("packed")
            elif order.status == "packed":
                self.dispatch(order)
            elif order.status == "in_transit":
                self.deliver(order)

    def maybe_incident(self):
        if self.rng.random() >= self.cfg["incident_probability"]:
            return
        moving = [o for o in self.active.values() if o.status == "in_transit"]
        if not moving:
            return
        order = self.rng.choice(moving)
        lost_minutes = self.rng.randint(30, 120)
        hub_id = self.rng.choice([order.origin, order.destination])
        description = self.rng.choice(INCIDENT_TEMPLATES).format(
            plate=self.plates[order.vehicle_id], hub=self.hubs[hub_id]["name"]
        )
        self.cur.execute(
            "INSERT INTO incident_reports (vehicle_id, hub_id, reported_at, description) "
            "VALUES (%s, %s, %s, %s)",
            (order.vehicle_id, hub_id, self.sim_now, description),
        )
        self.add_event(order.shipment_id, hub_id, "delayed", f"about {lost_minutes} min lost")
        order.due += timedelta(minutes=lost_minutes)   # the delay carries into delivery
        self.counts["incidents"] += 1
        self.log("INCIDENT", description)

    def maybe_mess(self):
        # Deliberately messy data, so the silver layer has real problems to fix
        # Duplicate update: same values written again, which Debezium reports as a new event
        if self.active and self.rng.random() < self.cfg["duplicate_probability"]:
            order = self.rng.choice(list(self.active.values()))
            self.cur.execute(
                "UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s",
                (order.status, self.sim_now, order.order_id),
            )
            self.log("DUPLICATE", f"order {order.order_id} rewritten with the same status")

         # Late scan: an event stamped hours in the past
        moving = [o for o in self.active.values() if o.status == "in_transit"]
        if moving and self.rng.random() < self.cfg["late_event_probability"]:
            order = self.rng.choice(moving)
            late_time = self.sim_now - timedelta(hours=self.rng.randint(1, 6))
            self.add_event(order.shipment_id, order.origin, "scanned", "late scan", late_time)
            self.log("LATE", f"scan for order {order.order_id} stamped {late_time:%H:%M}")

#order life cycle steps
    def dispatch(self, order):
        vehicle_id = self.pick_vehicle(order.origin)
        if vehicle_id is None:
            order.due = self.sim_now + timedelta(minutes=self.rng.randint(5, 15))
            self.log("WAIT", f"order {order.order_id}: no vehicle free, retrying later")
            return
 
        o, d = self.hubs[order.origin], self.hubs[order.destination]
        km = haversine_km(o["lat"], o["lon"], d["lat"], d["lon"]) * ROAD_FACTOR
        planned_minutes = max(20.0, km / AVG_SPEED_KMH * 60)
        planned = self.sim_now + timedelta(minutes=planned_minutes)
        # Real trips vary around the plan
        actual_minutes = planned_minutes * self.rng.lognormvariate(0, 0.2)
 
        self.cur.execute(
            "UPDATE vehicles SET status = 'on_route' WHERE vehicle_id = %s", (vehicle_id,)
        )
        self.cur.execute(
            "INSERT INTO shipments (order_id, vehicle_id, planned_arrival, status) "
            "VALUES (%s, %s, %s, 'in_transit') RETURNING shipment_id",
            (order.order_id, vehicle_id, planned),
        )
        order.shipment_id = self.cur.fetchone()[0]
        order.vehicle_id = vehicle_id
        order.planned_arrival = planned
        order.due = self.sim_now + timedelta(minutes=actual_minutes)
 
        self.add_event(order.shipment_id, order.origin, "departed")
        self.set_status(order, "in_transit")
 
    def deliver(self, order):
        delay = round((self.sim_now - order.planned_arrival).total_seconds() / 60)
        self.cur.execute(
            "UPDATE shipments SET actual_arrival = %s, delay_minutes = %s, "
            "status = 'delivered' WHERE shipment_id = %s",
            (self.sim_now, delay, order.shipment_id),
        )
        self.cur.execute(
            "UPDATE vehicles SET status = 'available' WHERE vehicle_id = %s",
            (order.vehicle_id,),
        )
        self.add_event(order.shipment_id, order.destination, "delivered")
        self.set_status(order, "delivered")
        del self.active[order.order_id]
        self.counts["delivered"] += 1
 
    def cancel(self, order):
        self.set_status(order, "cancelled")
        del self.active[order.order_id]
        self.counts["cancelled"] += 1