FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY web/ web/
COPY memory_bank/ memory_bank/

ENV PYTHONUNBUFFERED=1

CMD ["python", "backend/app.py"]
