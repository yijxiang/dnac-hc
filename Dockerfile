FROM python:3.8.9-alpine
WORKDIR /app
COPY app /app
RUN pip install --user -r requirements.txt

CMD ["python", "main.py"]
