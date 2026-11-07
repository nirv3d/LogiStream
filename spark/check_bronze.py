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
)
spark = configure_spark_with_delta_pip(builder).getOrCreate()
spark.sparkContext.setLogLevel("ERROR")
 
df = spark.read.format("delta").load(BRONZE_PATH)
print(f"\nTotal rows: {df.count()}")

# per topic+partition: rows vs expected offsets (max-min+1); missing = gaps,
# duplicates = same offset stored twice (should be 0 unless a restart replayed)
