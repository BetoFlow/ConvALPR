# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 5001 available to the world outside this container (if cashier service runs on 5001)
# This will be mapped in docker-compose.yml
EXPOSE 5001

# Define environment variables (can be overridden by docker-compose)
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0
ENV FLASK_RUN_PORT=5001
ENV MONGO_URI=mongodb://mongodb:27017/ 
ENV DB_NAME=anpr_db
# Add other ENV VARS like FLASK_DEBUG, FLASK_SECRET_KEY as needed

# Command to run the application
CMD ["flask", "run"]
