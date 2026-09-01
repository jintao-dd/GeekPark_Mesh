FROM python:3.12-slim
WORKDIR /srv/mesh
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY deploy/docker-entry.sh /docker-entry.sh
RUN sed -i 's/\r$//' /docker-entry.sh && chmod +x /docker-entry.sh && mkdir -p data/raw
EXPOSE 8080
CMD ["/docker-entry.sh"]
