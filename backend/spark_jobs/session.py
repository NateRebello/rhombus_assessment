"""SparkSession factory. Memory limits read from env vars to avoid OOM in the Celery worker JVM."""
import os

from pyspark.sql import SparkSession


def get_session(app_name: str = "rhombus") -> SparkSession:
    driver_memory = os.environ.get("SPARK_DRIVER_MEMORY", "2g")
    executor_memory = os.environ.get("SPARK_EXECUTOR_MEMORY", "1g")

    session = (
        SparkSession.builder
        .appName(app_name)
        .master(os.environ.get("SPARK_MASTER_URL", "local[*]"))
        .config("spark.driver.memory", driver_memory)
        .config("spark.executor.memory", executor_memory)
        .config("spark.sql.parquet.compression.codec", "snappy")
        .config("spark.ui.enabled", "false")
        .config("spark.log.level", "WARN")
        .getOrCreate()
    )

    session.sparkContext.setLogLevel("WARN")
    return session
