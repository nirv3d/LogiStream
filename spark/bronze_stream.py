"""Bronze job: all logistics.public.* Redpanda topics -> one Delta table.
 
Raw and append-only: no parsing, no de-duplication. Silver does that.
Stop with Ctrl+C; restart resumes from the checkpoint.
"""
import os

from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "redpanda:9092")
TOPIC_PATTERN = r"logistics\.public\..*"
BRONZE_PATH = "/lake/bronze/cdc_events"
CHECKPOINT = "/lake/_checkpoints/bronze_cdc_events"
KAFKA_PKG = "org.apache.spark:spark-sql-kafka-0-10_2.13:4.2.0"

builder = (
    SparkSession.builder.appName("bronze_cdc_events")
    .config("spark.driver.memory", "2g")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.shuffle.partitions", "4")
    .config("spark.jars.ivy", "/root/.ivy2")
)
spark = configure_spark_with_delta_pip(builder, extra_packages=[KAFKA_PKG]).getOrCreate()
spark.sparkContext.setLogLevel("WARN")

raw = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", BOOTSTRAP)
    .option("subscribePattern", TOPIC_PATTERN)
    .option("startingOffsets", "earliest")
    .option("maxOffsetsPerTrigger", 20000)
    .load()
)

bronze = raw.select(
    F.col("topic"),
    F.col("partition").alias("kafka_partition"),
    F.col("offset").alias("kafka_offset"),
    F.col("timestamp").alias("kafka_timestamp"),
    F.col("key").cast("string").alias("key"),
    F.col("value").cast("string").alias("value"),
    F.current_timestamp().alias("ingested_at"),
)

query = (
    bronze.writeStream.format("delta")
    .outputMode("append")
    .option("checkpointLocation", CHECKPOINT)
    .partitionBy("topic")
    .trigger(processingTime="5 seconds")
    .start(BRONZE_PATH)
)
query.awaitTermination()