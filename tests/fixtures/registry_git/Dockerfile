FROM alpine:3.23@sha256:469c9ea410b9215eaee7976ba6c0639039b444be8e5d638f7abbada189971eb2

RUN apk add --no-cache git openssh \
    && adduser -D -s /usr/bin/git-shell git \
    && passwd -d git \
    && mkdir -p /home/git/.ssh /fixtures \
    && chmod 0700 /home/git/.ssh \
    && chown git:git /home/git/.ssh

COPY repository/ /tmp/registry/
RUN git -C /tmp/registry init --quiet --initial-branch=main \
    && git -C /tmp/registry add . \
    && git -C /tmp/registry -c user.name=Fixture -c user.email=fixture@example.test \
        commit --quiet -m "Add registry fixture" \
    && git clone --quiet --bare /tmp/registry /fixtures/registry_fixture.git \
    && chown -R git:git /fixtures

COPY sshd_config /etc/ssh/sshd_config
CMD ["/bin/sh", "-ec", "install -o git -g git -m 0600 /run/registry-fixture-key.pub /home/git/.ssh/authorized_keys; ssh-keygen -A >/dev/null; exec /usr/sbin/sshd -D -e"]
