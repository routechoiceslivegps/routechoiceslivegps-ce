FROM debian:13-slim

RUN apt-get update && apt-get install -y wget
RUN wget https://github.com/ononoki1/nginx-http3/releases/latest/download/nginx.deb
RUN apt-get install -f -y ./nginx.deb

RUN nginx -g daemon off;
