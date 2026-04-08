FROM python:3.10-slim

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

# Install cron
RUN apt-get update && apt-get install -y cron

# Add crontab
RUN echo "0 8,13,18 * * * cd /app && python main.py >> /var/log/cron.log 2>&1" > /etc/cron.d/job-tracker
RUN chmod 0644 /etc/cron.d/job-tracker
RUN crontab /etc/cron.d/job-tracker

CMD ["cron", "-f"]