FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY na_sso ./na_sso
RUN pip install --no-cache-dir .

FROM python:3.12-slim
RUN useradd -r -m -d /app na_sso && mkdir /data && chown na_sso /data
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
USER na_sso
WORKDIR /app
ENV NA_SSO_DATABASE_PATH=/data/na-sso.db
EXPOSE 8000 9000
CMD ["uvicorn", "na_sso.main:app", "--host", "0.0.0.0", "--port", "8000"]
