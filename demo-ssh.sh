#!/bin/sh
set -eu

case "${1:-}" in
  bootstrap)
    mkdir -p /demo
    test -f /demo/management_key || ssh-keygen -q -t ed25519 -N '' -f /demo/management_key
    for name in password combined; do
      test -f "/demo/${name}_host_key" || ssh-keygen -q -t ed25519 -N '' -f "/demo/${name}_host_key"
    done
    password_fp="$(ssh-keygen -lf /demo/password_host_key.pub -E sha256 | awk '{print $2}')"
    combined_fp="$(ssh-keygen -lf /demo/combined_host_key.pub -E sha256 | awk '{print $2}')"
    cat > /demo/na-sso.yaml <<EOF
version: 1
password_policy: {min_length: 14, history_size: 3, expires_after_days: 90, expiry_acknowledgement_mode: grace, expiry_acknowledgement_grace_days: 14, expiry_acknowledgement_limit: 1}
ssh_key_policy: {allowed_algorithms: [ed25519, rsa], rsa_min_bits: 3072, browser_generation: true, allow_server_fallback: true}
support_policy: {label: Contact the demo operator, url: null, guidance: Share the affected demo target name; never share passwords or private keys.}
targets:
  - {id: ssh_password, type: ssh, display_name: Debian SSH password, host: demo-ssh-password, port: 22, host_key_sha256: ${password_fp}, platform: debian, allow_relaxed_usernames: false, mode: password}
  - {id: ssh_combined, type: ssh, display_name: Debian SSH key and password, host: demo-ssh-combined, port: 22, host_key_sha256: ${combined_fp}, platform: debian, allow_relaxed_usernames: true, mode: password_and_key, default_groups: [developers]}
  - {id: firewall_a, type: opnsense, display_name: Firewall A, base_url: http://mock-targets:9000, verify_tls: false}
  - {id: firewall_b, type: opnsense, display_name: Firewall B, base_url: http://mock-targets:9000, verify_tls: false}
  - {id: nexus_demo, type: nexus, display_name: Nexus Repository, base_url: http://mock-targets:9000, default_roles: [nx-anonymous], verify_tls: false}
  - {id: cloud_demo, type: nextcloud, display_name: Nextcloud, base_url: http://mock-targets:9000, verify_tls: false, default_groups: [employees]}
  - {id: jenkins_demo, type: jenkins, display_name: Jenkins, base_url: http://mock-targets:9000, verify_tls: false}
  - {id: gitlab_demo, type: gitlab, display_name: GitLab, base_url: http://mock-targets:9000, verify_tls: false}
  - {id: gitea_demo, type: gitea, display_name: Gitea, base_url: http://mock-targets:9000, verify_tls: false}
  - {id: immich_demo, type: immich, display_name: Immich, base_url: http://mock-targets:9000, verify_tls: false}
EOF
    # The public demo UI uploads management_key from the host. It is a disposable
    # demo credential, so keep it host-readable; host private keys stay restricted.
    chmod 0644 /demo/management_key /demo/na-sso.yaml /demo/*.pub
    chmod 0600 /demo/*_host_key
    ;;
  serve)
    name="${2:?server name required}"
    test -f "/demo/${name}_host_key"
    id provisioner >/dev/null 2>&1 || useradd -m -s /bin/sh provisioner
    printf 'provisioner:demo-ssh-admin\n' | chpasswd
    getent group developers >/dev/null || groupadd developers
    mkdir -p /run/sshd /home/provisioner/.ssh
    cp /demo/management_key.pub /home/provisioner/.ssh/authorized_keys
    chown -R provisioner:provisioner /home/provisioner/.ssh
    chmod 0700 /home/provisioner/.ssh
    chmod 0600 /home/provisioner/.ssh/authorized_keys
    printf 'provisioner ALL=(ALL) NOPASSWD: ALL\n' >/etc/sudoers.d/provisioner
    chmod 0440 /etc/sudoers.d/provisioner
    exec /usr/sbin/sshd -D -e -h "/demo/${name}_host_key" -o PasswordAuthentication=yes -o PubkeyAuthentication=yes -o PermitRootLogin=no
    ;;
  *) exit 64 ;;
esac
