# A bare Linux server on the lab network, where participants install Elastic Agent
# by hand (the Linux commands shown in Kibana > Fleet > Add agent).
# Ubuntu, not Debian 12/13: on those, the System integration reads logs from journald
# only, and a container has no journal. On Ubuntu it reads /var/log/auth.log and syslog.
# After changing this file, change the image tag in compose.yml and lab.sh.
FROM ubuntu:noble-20260911

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl procps sudo \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash student \
 && echo "student ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/student \
 && chmod 440 /etc/sudoers.d/student

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
