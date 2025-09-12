FROM python:3.11-slim
RUN adduser --disabled-password --gecos "" app
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
USER app
CMD ["python","main.py"]
