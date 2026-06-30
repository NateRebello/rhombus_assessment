"""
SparkSession factory.

This module is the only place in the codebase that creates a SparkSession.
Centralising creation here means:
  - Memory limits are applied consistently for every job (no task forgets them).
  - Tests can monkeypatch get_session() to return a local[1] session.
  - Configuration is read from env vars set in docker-compose / .env, so
    tuning memory does not require a code change.

Why memory limits matter here:
  The Celery worker process forks a JVM subprocess when PySpark is initialised.
  Without explicit memory caps the JVM will use all available heap, potentially
  OOMing the host alongside other Celery workers.  The compose mem_limit on the
  worker service is the outer bound; these JVM flags are the inner bound.
"""
import os

from pyspark.sql import SparkSession


def get_session(app_name: str = "rhombus") -> SparkSession:
    """
    Return (or create) a SparkSession configured for the current environment.

    Memory values are read from environment variables so they can be tuned
    without rebuilding the image.  Defaults are conservative to avoid OOM on
    development machines.
    """
    driver_memory = os.environ.get("SPARK_DRIVER_MEMORY", "2g")
    executor_memory = os.environ.get("SPARK_EXECUTOR_MEMORY", "1g")

    session = (
        SparkSession.builder
        .appName(app_name)
        # Local mode: all work runs in the driver JVM.  For a distributed
        # cluster replace "local[*]" with the master URL from env.
        .master(os.environ.get("SPARK_MASTER_URL", "local[*]"))
        .config("spark.driver.memory", driver_memory)
        .config("spark.executor.memory", executor_memory)
        # Write Parquet files with snappy compression by default — good balance
        # of speed and size for intermediate results.
        .config("spark.sql.parquet.compression.codec", "snappy")
        # Disable Spark UI in the worker to avoid port conflicts when multiple
        # workers run on the same host during development.
        .config("spark.ui.enabled", "false")
        # Reduce log verbosity from the default INFO to WARN so Celery logs
        # are not drowned out by Spark's verbose output.
        .config("spark.log.level", "WARN")
        .getOrCreate()
    )

    session.sparkContext.setLogLevel("WARN")
    return session
