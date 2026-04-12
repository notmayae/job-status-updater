FROM python:3.10-slim

# Set timezone so cron runs at local time rather than UTC.
# CHANGE THIS: replace with your own timezone (e.g. Europe/London, America/New_York)
ENV TZ=Asia/Jerusalem
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# Install cron
RUN apt-get update && apt-get install -y cron

# Add crontab
RUN echo "0 8,13,18 * * * cd /app && python main.py >> /var/log/cron.log 2>&1" > /etc/cron.d/job-tracker
RUN chmod 0644 /etc/cron.d/job-tracker
RUN crontab /etc/cron.d/job-tracker

# Dump runtime environment variables to /etc/environment so cron jobs can access them.
# Without this, cron spawns jobs without the env vars passed via docker compose env_file.
CMD printenv > /etc/environment && cron -f