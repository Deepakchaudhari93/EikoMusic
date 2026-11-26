#ALONE-CODER
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .

RUN apt-get update -y && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ffmpeg curl unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install -U pip && pip3 install -U -r requirements.txt

COPY . .

RUN rm -rf /app/cache /app/downloads \
    && ln -s /tmp/cache /app/cache \
    && ln -s /tmp/downloads /app/downloads

CMD ["bash", "start"]
