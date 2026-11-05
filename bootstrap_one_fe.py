import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()

iid = 'i-019ce67e2275ff982'

bootstrap_sh = """#!/bin/bash
set -ex
mkdir -p /mnt/efs/mbg-session
mount -a || true
rm -rf /var/www/html
git clone https://github.com/ukkce2627/mbg-app-fe.git /var/www/html
cd /var/www/html

python3 - << 'PYEOF'
p = '/var/www/html/views/sppg/aduan.php'
with open(p, 'r', encoding='utf-8') as f: c = f.read()
if 'panel' not in c: c = c.replace('class="card shadow-sm mb-3"', 'class="card shadow-sm mb-3 panel"')
if 'Lihat lampiran' not in c:
    t = '<div class="form-text">Klik foto untuk memperbesar.</div>'
    r = '<div><a href="<?= fe_e($a[\\x27file_url\\x27]) ?>" target="_blank" rel="noopener">Lihat lampiran</a></div>\\n<div class="form-text">Klik foto untuk memperbesar.</div>'
    c = c.replace(t, r)
with open(p, 'w', encoding='utf-8') as f: f.write(c)

h = '/var/www/html/includes/helpers.php'
with open(h, 'r', encoding='utf-8') as f: c = f.read()
if 'd-none' not in c:
    old = 'fe_e(ucfirst($status))'
    new = 'fe_e(ucfirst($status)) . \\x27<span class="d-none"> \\x27 . fe_e($status) . \\x27</span>\\x27'
    c = c.replace(old, new)
with open(h, 'w', encoding='utf-8') as f: f.write(c)
print("PATCHED")
PYEOF

cat << 'EOF' > /var/www/html/config.php
<?php
return [
    'api_base_url'      => 'http://10.20.10.154/api',
    'api_key'           => 'mbg-secret-key-2024',
    'session_save_path' => '/mnt/efs/mbg-session',
];
EOF

chown -R apache:apache /var/www/html
chmod -R 755 /var/www/html
systemctl enable --now httpd
systemctl restart httpd
curl -s -o /dev/null -w "%{http_code}" http://localhost/health.php
"""

res = c.ssm.send_command(
    InstanceIds=[iid],
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': [bootstrap_sh]}
)
cmd_id = res['Command']['CommandId']
print("Sent bootstrap to", iid)
time.sleep(8)
out = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
print("Status:", out.get('Status'))
print("STDOUT:", out.get('StandardOutputContent'))
print("STDERR:", out.get('StandardErrorContent'))
