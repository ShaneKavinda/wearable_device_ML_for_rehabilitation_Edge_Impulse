# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm AS model-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

COPY resource/exported_model/ei_gesture_left_hand_imu_arduino.zip \
    resource/exported_model/ei_gesture_left_hand_imu_arduino.zip
COPY resource/inference_api/edge_runner/prepare_model.py \
    resource/inference_api/edge_runner/prepare_model.py
COPY resource/inference_api/edge_runner/CMakeLists.txt \
    resource/inference_api/edge_runner/CMakeLists.txt
COPY resource/inference_api/edge_runner/main.cpp \
    resource/inference_api/edge_runner/main.cpp
COPY resource/inference_api/edge_runner/windows_porting.cpp \
    resource/inference_api/edge_runner/windows_porting.cpp
COPY resource/inference_api/cloud_service \
    resource/inference_api/cloud_service

RUN python resource/inference_api/edge_runner/prepare_model.py \
        --archive resource/exported_model/ei_gesture_left_hand_imu_arduino.zip \
    && cmake -S resource/inference_api/edge_runner -B /build \
        -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --config Release --parallel \
    && strip /build/edge_inference_runner \
    && PYTHONPATH=/src/resource/inference_api \
        python resource/inference_api/cloud_service/smoke_test.py \
        /build/edge_inference_runner


FROM python:3.13-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="IMU Rehabilitation Cloud Inference" \
    org.opencontainers.image.description="Edge Impulse deployment 19 inference API" \
    org.opencontainers.image.version="19"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_RUNNER_PATH=/usr/local/bin/edge_inference_runner \
    MODEL_VERSION=ei-738400-deployment-19 \
    PORT=8080

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY resource/inference_api/cloud_service/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
        --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY --from=model-builder /build/edge_inference_runner \
    /usr/local/bin/edge_inference_runner
COPY --chown=10001:10001 resource/inference_api/cloud_service/__init__.py \
    resource/inference_api/cloud_service/__main__.py \
    resource/inference_api/cloud_service/app.py \
    resource/inference_api/cloud_service/runner.py \
    /app/cloud_service/

USER 10001:10001

EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/readyz', timeout=2)"]

CMD ["python", "-m", "cloud_service"]
