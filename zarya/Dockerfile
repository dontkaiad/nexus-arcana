# ⭐ Zarya — heylark Booking concierge bot (@heylark_booking_bot). #23 / ADR-0026.
#
# Narrow build context on purpose: Zarya's image only re-layers when core/,
# zarya/ or the handful of SQLAlchemy table defs it needs change — so a change
# to nexus/handlers or arcana/handlers does NOT trigger a Zarya redeploy,
# even though it lives in the same repo + compose.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY zarya/ ./zarya/
COPY nexus/__init__.py ./nexus/__init__.py
COPY nexus/repos/__init__.py nexus/repos/tasks_tables.py ./nexus/repos/
COPY arcana/__init__.py ./arcana/__init__.py
COPY arcana/repos/__init__.py arcana/repos/rituals_tables.py arcana/repos/works_tables.py ./arcana/repos/

# NO build-time version stamp here on purpose: `ARG GIT_SHA` + a `RUN` consuming
# it would bust this layer on EVERY deploy (deploy.sh passes a fresh SHA),
# defeating the narrow-context caching above — Zarya would rebuild + restart +
# ping "поднялся" on unrelated commits (favicon, nexus/handlers, …). Instead the
# stamp is written at container start (zarya/bot.py) — runtime, no layer.
#
# GIT_SHA is the exception that is cheap: ARG+ENV as the LAST instructions, no RUN, so every
# content layer above stays cached. It changes the image config though, so deploy.sh must pass
# the last commit that touched the paths listed in zarya/build-inputs.txt (not HEAD), otherwise the image ID —
# and the container — would change on every unrelated deploy (see deploy.sh).
ARG GIT_SHA=
ENV GIT_SHA=${GIT_SHA}

CMD ["python", "-m", "zarya"]
