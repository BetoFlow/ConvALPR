# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH /app
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
# Context is project root
COPY web_portal/requirements.txt /app/requirements.txt

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the web_portal application code into the container
# Context is project root
COPY web_portal/. /app/

# Copy the common module from the project root
COPY common /app/common

# Expose the port the app runs on
EXPOSE 5000

# Command to run the application
CMD ["flask", "run"]
