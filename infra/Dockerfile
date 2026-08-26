FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY packages ./packages
COPY apps ./apps
COPY services ./services
COPY robotics ./robotics
COPY tools ./tools

RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" python-multipart httpx jinja2 numpy opencv-python-headless pyyaml

ENV SIGNOFF_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8600

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8600"]
