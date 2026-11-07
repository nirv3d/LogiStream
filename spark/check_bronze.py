"""Bronze health check: per-topic counts, offset gaps, latest events."""
from delta import configure_spark_with_delta_pip
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

BRONZE_PATH = "/lake/bronze/cdc_events"
 
builder = (
    SparkSession.builder.appName("check_bronze")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.shuffle.partitions", "4")
    .config("spark.jars.ivy", "/root/.ivy2")
)
spark = configure_spark_with_delta_pip(builder).getOrCreate()
spark.sparkContext.setLogLevel("ERROR")
 
df = spark.read.format("delta").load(BRONZE_PATH)
print(f"\nTotal rows: {df.count()}")

# per topic+partition: rows vs expected offsets (max-min+1); missing = gaps,
# duplicates = same offset stored twice (should be 0 unless a restart replayed)
per_part = df.groupBy("topic", "kafka_partition").agg(
    F.count("*").alias("rows"),
    F.countDistinct("kafka_offset").alias("distinct_offsets"),
    F.min("kafka_offset").alias("min_off"),
    F.max("kafka_offset").alias("max_off"),
)
summary = (
    per_part.withColumn("missing", F.col("max_off") - F.col("min_off") + 1 - F.col("distinct_offsets"))
    .withColumn("duplicates", F.col("rows") - F.col("distinct_offsets"))
    .groupBy("topic")
    .agg(F.sum("rows").alias("rows"), F.sum("missing").alias("missing"),
         F.sum("duplicates").alias("duplicates"))
    .orderBy("topic")
)
print("\nPer-topic counts (missing and duplicates should be 0):")
summary.show(truncate=False)
 
print("Latest events:")
(df.orderBy(F.col("kafka_timestamp").desc())
   .select("topic", "kafka_partition", "kafka_offset", "kafka_timestamp", "key")
   .show(10, truncate=False))