FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY worker.py wsgi.py ./
COPY --from=frontend-builder /frontend/dist ./frontend/dist

RUN mkdir -p /app/data

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "wsgi:app"]
