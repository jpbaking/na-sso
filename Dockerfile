FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY oneauth ./oneauth
RUN pip install --no-cache-dir .

FROM python:3.12-slim
RUN useradd -r -m -d /app oneauth && mkdir /data && chown oneauth /data
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
USER oneauth
WORKDIR /app
ENV ONEAUTH_DATABASE_PATH=/data/oneauth.db
EXPOSE 8000 9000
CMD ["uvicorn", "oneauth.main:app", "--host", "0.0.0.0", "--port", "8000"]
