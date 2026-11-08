FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Print logs right away instead of buffering them
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
RUN useradd --create-home --uid 1000 spr

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# data/ holds the Telegram session, docker-compose.yml mounts a volume there.
# Heroku runs containers as a random user, so the folder is writable by all.
RUN mkdir -p /app/data && chown spr:spr /app/data && chmod 1777 /app/data
USER spr

CMD ["python3", "-m", "spr"]
