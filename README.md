# ✨ SpamProtectionRobot ✨
### Anti Spam/NSFW Telegram Bot Written In Python With Telethon.


[![Python](http://forthebadge.com/images/badges/made-with-python.svg)](https://python.org)&nbsp;
[![ForTheBadge built-with-love](http://ForTheBadge.com/images/badges/built-with-love.svg)](https://GitHub.com/TheHamkerCat/)


<img src="assets/start.jpg" width="300" align="right">


## Requirements

- Python >= 3.12
- PostgreSQL. Docker Compose runs one for you. On Heroku, the Deploy button adds the paid Heroku Postgres add-on, which sets `DATABASE_URL`.


## Settings

```sh
$ git clone https://github.com/TheHamkerCat/SpamProtectionRobot

$ cd SpamProtectionRobot

$ cp .env.example .env
```

Edit **.env** with your own values:

# Docker

```sh
$ docker compose up -d --build
```

# Run Directly

Needs [uv](https://docs.astral.sh/uv/).

```sh
$ uv sync

$ uv run python -m spr
```

After changing dependencies in **pyproject.toml**, update **uv.lock** (in Docker):

```sh
$ docker run --rm -v "$PWD:/app" -w /app python:3.12.16-slim-trixie sh -c "pip install -q uv==0.13.7 && uv lock"
```


# Heroku

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/thehamkercat/SpamProtectionRobot/)

Heroku has no .env file: fill in the same names as Config Vars.

The Deploy button also adds the Heroku Postgres add-on (a paid plan), which sets `DATABASE_URL`. If you create the app without the button, add the database yourself:

```sh
$ heroku addons:create heroku-postgresql:essential-0
```


# Database changes

The bot applies database migrations (Alembic) by itself at startup. After changing `spr/models.py`, create a migration and commit the new file from `spr/migrations/versions`:

```sh
$ docker compose -f compose.test.yml run --rm --build alembic revision --autogenerate -m "what changed"
```

This runs in Docker against a throwaway database, never the real one. Any other alembic command works the same way, e.g. `... run --rm alembic check`.

To see which migration the real database is on:

```sh
$ docker compose run --rm bot alembic current
```


# Tests

```sh
$ docker compose -f compose.test.yml run --rm --build tests

$ docker compose -f compose.test.yml --env-file .env run --rm --build live

$ docker compose -f compose.test.yml run --rm --build benchmark

$ docker compose -f compose.test.yml down
```


## Note

1. NOT RELATED TO INTELLIVOID IN ANY WAY.
2. Start image: [Andrey Matveev on Unsplash](https://unsplash.com/photos/a-paper-airplane-with-the-word-spam-written-on-it-XUDseoo1IHc).
