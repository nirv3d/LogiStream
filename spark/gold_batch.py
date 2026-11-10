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

# origin/destination hub lookups
oh = hubs.select(
    F.col("hub_id").alias("origin_hub_id"),
    F.col("hub_name").alias("origin_hub_name"),
    F.col("hub_city").alias("origin_city"),
)
dh = hubs.select(
    F.col("hub_id").alias("destination_hub_id"),
    F.col("hub_name").alias("destination_hub_name"),
    F.col("hub_city").alias("destination_city"),
)

#"now" for the whole lake = latest event time we have (NOT the wall clock), so gold gives the same answers whenever you run it.
as_of_df = (
    events.select(F.col("event_time").alias("t"))
    .unionByName(orders.select(F.col("updated_at").alias("t")))
    .unionByName(orders.select(F.col("created_at").alias("t")))
    .agg(F.max("t").alias("as_of"))
)

print("Sanity info:")
print("  shipment status values in silver:")
shipments.groupBy("shipment_status").count().show(truncate=False)
multi = shipments.groupBy("order_id").count().filter("count > 1").count()
print(f"  orders with more than one shipment: {multi}")


#fact_orders this tells us the current state of each order, including its shipment (if any) and its status (cancelled, delivered, in_transit)
one_ship = (
    shipments.withColumn(
        "rn", F.row_number().over(Window.partitionBy("order_id").orderBy(F.col("shipment_id").desc()))
    )
    .filter("rn = 1")
    .select("order_id", "shipment_id")
)

fact_orders = (
    orders.join(customers, "customer_id", "left")
    .join(oh, "origin_hub_id", "left")
    .join(dh, "destination_hub_id", "left")
    .join(one_ship, "order_id", "left")
    .withColumn("created_date", day("created_at"))
    .withColumn("is_cancelled", F.col("order_status") == "cancelled")
    # simulator does not touch an order after cancelling it, so updated_at = cancel time
    .withColumn("cancelled_at", F.when(F.col("is_cancelled"), F.col("updated_at")))
    .withColumn("cancelled_date", day("cancelled_at"))
    .withColumn("is_delivered", F.col("order_status") == "delivered")
    .withColumn("is_in_transit", F.col("order_status") == "in_transit")
    .select(
        "order_id", "customer_id", "customer_city",
        "origin_hub_id", "origin_hub_name", "origin_city",
        "destination_hub_id", "destination_hub_name", "destination_city",
        "weight_kg", "order_status", "created_at", "created_date", "updated_at",
        "is_cancelled", "cancelled_at", "cancelled_date",
        "is_delivered", "is_in_transit", "shipment_id",
    )
    .cache()
)



#fact_shipments
ev_ship = events.groupBy("shipment_id").agg(
    F.min(F.when(F.col("event_type") == "departed", F.col("event_time"))).alias("departed_at"),
    F.sum((F.col("event_type") == "delayed").cast("int")).alias("delayed_event_count"),
)
 
fact_shipments = (
    shipments.join(ev_ship, "shipment_id", "left")
    .join(
        orders.select(
            "order_id", "customer_id", "origin_hub_id", "destination_hub_id",
            F.col("created_at").alias("order_created_at"),
        ),
        "order_id", "left",
    )
    .join(customers, "customer_id", "left")
    .join(vehicles, "vehicle_id", "left")
    .join(oh, "origin_hub_id", "left")
    .join(dh, "destination_hub_id", "left")
    .crossJoin(F.broadcast(as_of_df))
    # delivered = has an actual arrival time. Undelivered is never "on time".
    .withColumn("is_delivered", F.col("actual_arrival").isNotNull())
    .withColumn("is_late", F.col("is_delivered") & (F.col("actual_arrival") > F.col("planned_arrival")))
    .withColumn("is_on_time", F.col("is_delivered") & (F.col("actual_arrival") <= F.col("planned_arrival")))
    .withColumn(
        "delay_minutes",
        F.when(F.col("is_late"), F.round(minutes("actual_arrival", "planned_arrival"), 2))
        .when(F.col("is_on_time"), F.lit(0.0)),
    )
    # end-to-end: order created -> actual arrival
    .withColumn(
        "delivery_minutes",
        F.when(F.col("is_delivered"), F.round(minutes("actual_arrival", "order_created_at"), 2)),
    )
    # on the road: departed event -> actual arrival
    .withColumn(
        "transit_minutes",
        F.when(
            F.col("is_delivered") & F.col("departed_at").isNotNull(),
            F.round(minutes("actual_arrival", "departed_at"), 2),
        ),
    )
    # warehouse side: order created -> departed event
    .withColumn(
        "order_to_dispatch_minutes",
        F.when(F.col("departed_at").isNotNull(), F.round(minutes("departed_at", "order_created_at"), 2)),
    )
    # not delivered and already past its planned arrival, measured at as_of
    .withColumn(
        "is_currently_delayed",
        (~F.col("is_delivered")) & (F.col("planned_arrival") < F.col("as_of")),
    )
    .withColumn(
        "current_delay_minutes",
        F.when(F.col("is_currently_delayed"), F.round(minutes("as_of", "planned_arrival"), 2)),
    )
    .withColumn("planned_date", day("planned_arrival"))
    .withColumn("departed_date", day("departed_at"))
    .withColumn("arrival_date", day("actual_arrival"))
    .select(
        "shipment_id", "order_id", "vehicle_id", "plate_no",
        "origin_hub_id", "origin_hub_name", "origin_city",
        "destination_hub_id", "destination_hub_name", "destination_city",
        "customer_city", "shipment_status",
        "order_created_at", "departed_at", "planned_arrival", "actual_arrival",
        "planned_date", "departed_date", "arrival_date",
        "is_delivered", "is_late", "is_on_time", "is_currently_delayed",
        "delay_minutes", "current_delay_minutes",
        "delivery_minutes", "transit_minutes", "order_to_dispatch_minutes",
        "delay_minutes_source", "delayed_event_count",
    )
    .cache()
)