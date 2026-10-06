FROM certbot/certbot:v3.1.0
COPY letsencrypt.sh /usr/local/bin/letsencrypt.sh
RUN chmod +x /usr/local/bin/letsencrypt.sh
ENTRYPOINT ["/usr/local/bin/letsencrypt.sh"]
