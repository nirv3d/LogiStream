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