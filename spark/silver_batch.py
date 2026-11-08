from functools import reduce
 
from delta import configure_spark_with_delta_pip
from delta.tables import DeltaTable
from pyspark.sql import SparkSession, Window, functions as F
from pyspark.sql.types import (
    DoubleType, IntegerType, LongType, StringType, StructField, StructType,
)

BRONZE = "/lake/bronze/cdc_events"
SILVER = "/lake/silver"

# table -> (primary key, {column: silver type})
TABLES = {
    "hubs": ("hub_id", {
        "hub_id": "int", "name": "string", "city": "string",
        "latitude": "double", "longitude": "double"}),
    "customers": ("customer_id", {
        "customer_id": "int", "name": "string", "phone": "string",
        "address": "string", "city": "string"}),
    "vehicles": ("vehicle_id", {
        "vehicle_id": "int", "plate_no": "string", "home_hub_id": "int",
        "status": "string"}),
    "drivers": ("driver_id", {
        "driver_id": "int", "name": "string", "phone": "string",
        "vehicle_id": "int"}),
    "orders": ("order_id", {
        "order_id": "int", "customer_id": "int", "origin_hub_id": "int",
        "destination_hub_id": "int", "weight_kg": "decimal(6,2)",
        "status": "string", "created_at": "timestamp",
        "updated_at": "timestamp"}),
    "shipments": ("shipment_id", {
        "shipment_id": "int", "order_id": "int", "vehicle_id": "int",
        "planned_arrival": "timestamp", "actual_arrival": "timestamp",
        "delay_minutes": "int", "status": "string"}),
    "shipment_events": ("event_id", {
        "event_id": "long", "shipment_id": "int", "hub_id": "int",
        "event_type": "string", "event_time": "timestamp",
        "notes": "string"}),
    "incident_reports": ("incident_id", {
        "incident_id": "int", "vehicle_id": "int", "hub_id": "int",
        "reported_at": "timestamp", "description": "string"}),
}

def read_type(silver_type):
    """Type the value has inside the Debezium JSON (before we cast it)."""
    if silver_type.startswith("decimal"):
        return DoubleType()          # Debezium sends numeric as a double here
    return {
        "int": IntegerType(), "long": LongType(), "double": DoubleType(),
        "string": StringType(),
        "timestamp": StringType(),   # ISO-8601 text, cast to timestamp later
    }[silver_type]

def build_spark():
    builder = (
        SparkSession.builder.appName("silver_batch")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog",
                "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.driver.memory", "1g")        # small, so it can run beside bronze
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.jars.ivy", "/root/.ivy2")
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()