# Use slim Debian-based Python image for a smaller layer.
# We need Java for PySpark — install it here rather than in a separate stage
# so the worker and web images are identical (one build context, two compose
# services), simplifying deployment and image caching.
FROM python:3.11-slim

# Install OpenJDK (required by PySpark) and build dependencies for psycopg2.
# default-jdk-headless installs the distro's default JDK (17 on Debian Bookworm)
# and creates the architecture-neutral /usr/lib/jvm/default-java symlink, making
# this Dockerfile portable across amd64 and arm64 without hard-coding a path.
RUN apt-get update && apt-get install -y --no-install-recommends \
        default-jdk-headless \
        curl \
    && rm -rf /var/lib/apt/lists/*

# JAVA_HOME must be set so PySpark can find the JVM at runtime.
# /usr/lib/jvm/default-java is a distro-managed symlink that works on both
# amd64 and arm64 without architecture-specific path hard-coding.
ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH="${JAVA_HOME}/bin:${PATH}"

WORKDIR /app

# Install Python dependencies before copying source so Docker layer-caching
# doesn't bust the pip install on every source change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the backend source.
COPY . .

# Create upload/result directories in the image as fallbacks; the compose
# file mounts named volumes over these paths at runtime.
RUN mkdir -p /app/uploads /app/results

EXPOSE 8000
