# Build from the repo root: docker build -f dashboard/Dockerfile .
FROM node:22-slim
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --omit=dev
COPY dashboard/server.js ./
COPY dashboard/public/ public/
COPY config.yaml config.demo.yaml /app/
ENV DATA_DIR=/data HOST=0.0.0.0 PORT=3852
USER node
EXPOSE 3852
CMD ["node", "server.js"]
