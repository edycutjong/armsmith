FROM --platform=linux/amd64 python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["gunicorn", "-w", "4", "app:app"]
