FROM python:3.14-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.14-slim AS runtime

# Не-root пользователь для запускаемого процесса.
RUN useradd --create-home --uid 10001 bridge

WORKDIR /app

# Копируем установленные пакеты из стадии сборки.
COPY --from=builder --chown=bridge:bridge /root/.local /home/bridge/.local
COPY --chown=bridge:bridge src ./src

ENV PYTHONUNBUFFERED=1
ENV PATH=/home/bridge/.local/bin:$PATH
ENV DATABASE_PATH=/data/bridge.db
ENV PYTHONDONTWRITEBYTECODE=1

USER bridge

# /data ожидается смонтированным томом.
VOLUME ["/data"]

CMD ["python", "-m", "src.main"]
