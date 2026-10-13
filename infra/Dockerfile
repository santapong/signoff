ARG PYTHON_IMAGE=python:3.12.9-slim
FROM ${PYTHON_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY packages ./packages
COPY apps ./apps
COPY services ./services
COPY robotics ./robotics
COPY tools ./tools

ARG SIGNOFF_GIT_SHA=unknown
ENV SIGNOFF_GIT_SHA=${SIGNOFF_GIT_SHA}

ENV SIGNOFF_DATA_DIR=/data
ENV SIGNOFF_REQUIRE_AUTH=1
VOLUME ["/data"]
EXPOSE 8600

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8600"]
