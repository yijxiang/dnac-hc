FROM python:3.8-alpine
WORKDIR /app
COPY src /app
RUN pip install --user -r requirements.txt

CMD ["python", "main.py"]
