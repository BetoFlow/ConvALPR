# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies
# Dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    ffmpeg \
    # Add other system dependencies if required by TensorFlow or OpenCV beyond the basics
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
# Build context is project root, so path is alpr/requirements.txt
COPY alpr/requirements.txt /app/requirements.txt

# Install any needed packages specified in requirements.txt and pymongo
RUN pip install --no-cache-dir -r requirements.txt pymongo

# Copy the anpr application code into the container
# Build context is project root, copy contents of alpr/ directory to /app/
COPY alpr/. /app/
# This will copy alpr.py, service_main.py, models/, etc. into /app/
# If config.yaml is used by alpr.py, it needs to be handled.
# For now, assuming alpr.py is adapted to use environment variables for configuration.

# Ensure the script is executable (if needed, though typically not for python scripts)
# RUN chmod +x alpr.py

# Command to run the application
# service_main.py handles video stream processing and uses the ALPR class.
CMD ["python", "service_main.py"]
