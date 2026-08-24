FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=ui /ui/dist ./static
ENV FC_CONFIG=/app/config.yaml FC_DB=/data/fleet.db
EXPOSE 8090
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8090"]
