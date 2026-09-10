ARG AGENT_IMAGE
FROM ${AGENT_IMAGE}
COPY workstation-relay/relay.py /code/workstation_relay.py
CMD ["/code/.venv/bin/uvicorn", "workstation_relay:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
