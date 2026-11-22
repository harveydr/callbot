FROM python:3.11.9-slim-bookworm

WORKDIR /srv
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && mkdir -p /var/rec && chown nobody:nogroup /var/rec
COPY backend /srv
USER nobody
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
