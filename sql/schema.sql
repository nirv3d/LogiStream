CREATE TABLE hubs (
  hub_id     SERIAL PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  city       TEXT NOT NULL,
  latitude   DOUBLE PRECISION,
  longitude  DOUBLE PRECISION
);

CREATE TABLE vehicles (
  vehicle_id   SERIAL PRIMARY KEY,
  plate_no     TEXT NOT NULL UNIQUE,
  home_hub_id  INT REFERENCES hubs(hub_id),
  status       TEXT NOT NULL DEFAULT 'available'
);

CREATE TABLE drivers (
  driver_id   SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  phone       TEXT,
  vehicle_id  INT REFERENCES vehicles(vehicle_id)
);

CREATE TABLE customers (
  customer_id  SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  phone        TEXT,
  address      TEXT,
  city         TEXT
);

CREATE TABLE orders (
  order_id            SERIAL PRIMARY KEY,
  customer_id         INT REFERENCES customers(customer_id),
  origin_hub_id       INT REFERENCES hubs(hub_id),
  destination_hub_id  INT REFERENCES hubs(hub_id),
  weight_kg           NUMERIC(6,2),
  status              TEXT NOT NULL DEFAULT 'created',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE shipments (
  shipment_id      SERIAL PRIMARY KEY,
  order_id         INT REFERENCES orders(order_id),
  vehicle_id       INT REFERENCES vehicles(vehicle_id),
  planned_arrival  TIMESTAMPTZ,
  actual_arrival   TIMESTAMPTZ,
  delay_minutes    INT,
  status           TEXT NOT NULL DEFAULT 'planned'
);

CREATE TABLE shipment_events (
  event_id     BIGSERIAL PRIMARY KEY,
  shipment_id  INT REFERENCES shipments(shipment_id),
  hub_id       INT REFERENCES hubs(hub_id),
  event_type   TEXT NOT NULL,
  event_time   TIMESTAMPTZ NOT NULL,
  notes        TEXT
);

CREATE TABLE incident_reports (
  incident_id  SERIAL PRIMARY KEY,
  vehicle_id   INT REFERENCES vehicles(vehicle_id),
  hub_id       INT REFERENCES hubs(hub_id),
  reported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  description  TEXT NOT NULL
);

ALTER TABLE hubs             REPLICA IDENTITY FULL;
ALTER TABLE vehicles         REPLICA IDENTITY FULL;
ALTER TABLE drivers          REPLICA IDENTITY FULL;
ALTER TABLE customers        REPLICA IDENTITY FULL;
ALTER TABLE orders           REPLICA IDENTITY FULL;
ALTER TABLE shipments        REPLICA IDENTITY FULL;
ALTER TABLE shipment_events  REPLICA IDENTITY FULL;
ALTER TABLE incident_reports REPLICA IDENTITY FULL;