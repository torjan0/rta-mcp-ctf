FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY agentlab/requirements.txt /app/agentlab/requirements.txt
RUN pip install --no-cache-dir -r /app/agentlab/requirements.txt
COPY agentlab/ /app/agentlab/
COPY deploy/aws/ctfd_seed.py /app/deploy/aws/ctfd_seed.py
COPY selfhost/ /app/selfhost/

RUN useradd --uid 10001 --create-home agent \
    && mkdir -p /var/lib/agentlab \
    && chown agent:agent /var/lib/agentlab
USER agent
WORKDIR /app/agentlab
CMD ["python", "/app/selfhost/run_lab.py"]
