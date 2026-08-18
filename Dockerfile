FROM irisxdr/neo-wzml:latest

WORKDIR /usr/src/app

RUN chmod 777 /usr/src/app

COPY requirements.txt .
RUN uv pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "start.sh"]
