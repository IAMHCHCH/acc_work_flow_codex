#!/bin/sh
set -eu
BMC_HOST=${BMC_HOST:-192.168.90.209}; CONSOLE_PORT=${CONSOLE_PORT:-10008}; TARGET=${TARGET:-192.168.90.141}; TIMEOUT=${TIMEOUT:-900}
command -v expect >/dev/null 2>&1 || { echo 'expect is required' >&2; exit 2; }
expect <<EOF
set timeout 30
spawn telnet $BMC_HOST $CONSOLE_PORT
expect "login:" { send "root\r" }
expect "Password:" { send "root\r" }
expect "#" { send "sh /home/reset_chip.sh 0\r" }
expect "#" { send "exit\r" }
EOF
end=$((`date +%s` + TIMEOUT))
while [ `date +%s` -lt "$end" ]; do
  if nc -z -w 3 "$TARGET" 22 >/dev/null 2>&1 && ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$TARGET" true >/dev/null 2>&1; then echo "target $TARGET is reachable"; exit 0; fi
  sleep 15
done
echo "target did not become reachable within $TIMEOUT seconds" >&2; exit 1
