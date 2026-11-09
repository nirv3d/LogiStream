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


def process_table(spark, bronze, name, key, cols):
    row_schema = StructType([StructField(c, read_type(t)) for c, t in cols.items()])
    envelope = StructType([StructField("payload", StructType([
        StructField("before", row_schema),
        StructField("after", row_schema),
        StructField("source", StructType([
            StructField("lsn", LongType()),
            StructField("ts_ms", LongType()),
        ])),
        StructField("op", StringType()),
    ]))])

    # Rows with a null value are Debezium "tombstones" (sent after a delete).
    # They carry no data, so we skip them rather than quarantine them.
    raw = bronze.filter(
        (F.col("topic") == f"logistics.public.{name}") & F.col("value").isNotNull()
    )

    p = (
        raw.withColumn("j", F.from_json("value", envelope))
        .withColumn("op", F.col("j.payload.op"))
        .withColumn("lsn", F.col("j.payload.source.lsn"))
        .withColumn("src_ms", F.col("j.payload.source.ts_ms"))
        # on a delete, 'after' is empty, so the row comes from 'before'
        .withColumn("row", F.when(F.col("op") == "d", F.col("j.payload.before"))
                            .otherwise(F.col("j.payload.after")))
    )
    p = p.withColumn("pk", F.col(f"row.{key}"))

    reason = (
        F.when(F.col("j.payload").isNull(), "unparseable JSON or no payload")
        .when(F.col("op").isNull() | ~F.col("op").isin("c", "u", "d", "r"), "unknown op")
        .when(F.col("lsn").isNull(), "missing lsn")
        .when(F.col("row").isNull() | F.col("pk").isNull(), "missing row or key")
    )
    p = p.withColumn("reason", reason)

    bad = p.filter(F.col("reason").isNotNull()).select(
        "topic", "kafka_partition", "kafka_offset", "value", "reason")
    good = p.filter(F.col("reason").isNull())

    # newest event per key: highest lsn, Kafka offset breaks ties
    w = Window.partitionBy("pk").orderBy(F.col("lsn").desc(), F.col("kafka_offset").desc())
    latest = good.withColumn("rn", F.row_number().over(w)).filter("rn = 1")

    silver = latest.select(
        *[F.col(f"row.{c}").cast(t).alias(c) for c, t in cols.items()],
        F.col("lsn").alias("_lsn"),
        F.col("kafka_offset").alias("_kafka_offset"),
        F.col("op").alias("_op"),
        (F.col("op") == "d").alias("is_deleted"),
        F.when(F.col("op") == "d", (F.col("src_ms") / 1000).cast("timestamp"))
         .alias("deleted_at"),
        F.current_timestamp().alias("_silver_at"),
    )

    path = f"{SILVER}/{name}"
    if not DeltaTable.isDeltaTable(spark, path):
        silver.write.format("delta").mode("overwrite").save(path)
    else:
        (DeltaTable.forPath(spark, path).alias("t")
            .merge(silver.alias("s"), f"t.{key} = s.{key}")
            .whenMatchedUpdateAll(condition="s._lsn > t._lsn")
            .whenNotMatchedInsertAll()
            .execute())
    return bad


def main():
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")
    bronze = spark.read.format("delta").load(BRONZE)   # a consistent snapshot

    bad_frames = []
    for name, (key, cols) in TABLES.items():
        print(f"silver: {name} ...")
        bad_frames.append(process_table(spark, bronze, name, key, cols))

    # Quarantine is rebuilt from bronze each run, so overwrite keeps it duplicate-free.
    quarantine = reduce(lambda a, b: a.unionByName(b), bad_frames)
    (quarantine.write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true").save(f"{SILVER}/_quarantine"))

    print("\n=== silver summary ===")
    for name in TABLES:
        df = spark.read.format("delta").load(f"{SILVER}/{name}")
        total = df.count()
        deleted = df.filter("is_deleted").count()
        print(f"{name:18s} rows={total:6d}  active={total - deleted:6d}  deleted={deleted}")
    print(f"quarantined rows: {spark.read.format('delta').load(f'{SILVER}/_quarantine').count()}")
    spark.stop()


if __name__ == "__main__":
    main()