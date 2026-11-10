"""
building the GOLD layer from SILVER.


Gold tables written to /lake/gold/:
    fact_orders        one row per order
    fact_shipments     one row per shipment
    fact_incidents     one row per incident (keeps the description text)
    agg_hub_daily      one row per hub per day (Indian time)
    agg_daily_overview one row per day for the whole network
    meta               one row: as_of (latest event time in the lake) and built_at

"""
from functools import reduce
 
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession, Window, functions as F

SILVER = "/lake/silver"
GOLD = "/lake/gold"
TZ = "Asia/Kolkata"


builder = (
    SparkSession.builder.appName("gold_batch")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.session.timeZone", "UTC")
    .config("spark.sql.shuffle.partitions", "8")
    .config("spark.driver.memory", "1g")
)
spark = configure_spark_with_delta_pip(builder).getOrCreate()
spark.sparkContext.setLogLevel("WARN")


#helper functions
def read_silver(name):
    """Current-state silver table without soft-deleted rows."""
    df = spark.read.format("delta").load(f"{SILVER}/{name}")
    return df.filter(~F.coalesce(F.col("is_deleted"), F.lit(False)))
 
def day(col):
    """Calendar date in Indian time."""
    return F.to_date(F.from_utc_timestamp(F.col(col), TZ))
 
def minutes(end, start):
    """Minutes between two timestamp columns."""
    return (F.unix_micros(F.col(end)) - F.unix_micros(F.col(start))) / 60_000_000.0
 
def as_int(col):
    return F.col(col).cast("int")
  
def combine(frames, keys):
    return reduce(lambda a, b: a.join(b, keys, "full_outer"), frames)
  
def write(df, name):
    path = f"{GOLD}/{name}"
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(path)
    n = spark.read.format("delta").load(path).count()
    print(f"  gold.{name}: {n} rows")


#loading silver
hubs = read_silver("hubs").select(
    "hub_id", F.col("name").alias("hub_name"), F.col("city").alias("hub_city")
)
customers = read_silver("customers").select("customer_id", F.col("city").alias("customer_city"))
vehicles = read_silver("vehicles").select("vehicle_id", "plate_no")
 
orders = read_silver("orders").select(
    "order_id", "customer_id", "origin_hub_id", "destination_hub_id",
    F.col("weight_kg").cast("double").alias("weight_kg"),
    F.col("status").alias("order_status"),
    F.to_timestamp("created_at").alias("created_at"),
    F.to_timestamp("updated_at").alias("updated_at"),
)
shipments = read_silver("shipments").select(
    "shipment_id", "order_id", "vehicle_id",
    F.to_timestamp("planned_arrival").alias("planned_arrival"),
    F.to_timestamp("actual_arrival").alias("actual_arrival"),
    F.col("delay_minutes").alias("delay_minutes_source"),
    F.col("status").alias("shipment_status"),
)
events = read_silver("shipment_events").select(
    "event_id", "shipment_id", "hub_id", "event_type",
    F.to_timestamp("event_time").alias("event_time"),
)
incidents = read_silver("incident_reports").select(
    "incident_id", "vehicle_id", "hub_id",
    F.to_timestamp("reported_at").alias("reported_at"), "description",
)