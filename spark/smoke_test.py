from pyspark.sql import SparkSession
from delta import configure_spark_with_delta_pip

builder = (
    SparkSession.builder.appName("smoke")
    .master("local[2]")
    .config("spark.driver.memory", "2g")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
)
spark = configure_spark_with_delta_pip(
    builder,
    extra_packages=["org.apache.spark:spark-sql-kafka-0-10_2.13:4.2.0"],
).getOrCreate()
spark.sparkContext.setLogLevel("WARN")

# Test 1: write and read a Delta table
df = spark.createDataFrame([(1, "created"), (2, "packed")], ["order_id", "status"])
df.write.format("delta").mode("overwrite").save("/lake/smoke_test")
spark.read.format("delta").load("/lake/smoke_test").show()

# Test 2: read from Redpanda
events = (
    spark.read.format("kafka")
    .option("kafka.bootstrap.servers", "redpanda:9092")
    .option("subscribe", "logistics.public.orders")
    .option("startingOffsets", "earliest")
    .load()
)
print("Events in orders topic:", events.count())