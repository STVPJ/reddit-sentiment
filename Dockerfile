FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sentiment/ sentiment/
COPY config.yaml config.demo.yaml ./
ENV DATA_DIR=/data
RUN useradd --create-home app && mkdir /data && chown app /data
USER app
CMD ["python", "-m", "sentiment", "run"]
