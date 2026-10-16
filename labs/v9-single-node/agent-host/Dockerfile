# A bare Linux server on the lab network, where participants install Elastic Agent
# by hand (the Linux commands shown in Kibana > Fleet > Add agent).
FROM debian:trixie-20260918-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl procps sudo \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash student \
 && echo "student ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/student \
 && chmod 440 /etc/sudoers.d/student

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
