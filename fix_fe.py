import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()

fix_script = """#!/bin/bash
set -x

python3 - << 'PYEOF'
import sys

p = '/var/www/html/views/sppg/aduan.php'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()
if 'panel' not in c:
    c = c.replace('class="card shadow-sm mb-3"', 'class="card shadow-sm mb-3 panel"')
if 'Lihat lampiran' not in c:
    t = '<div class="form-text">Klik foto untuk memperbesar.</div>'
    r = '''<div><a href="<?= fe_e($a['file_url']) ?>" target="_blank" rel="noopener">Lihat lampiran</a></div>\\n<div class="form-text">Klik foto untuk memperbesar.</div>'''
    c = c.replace(t, r)
with open(p, 'w', encoding='utf-8') as f:
    f.write(c)

h = '/var/www/html/includes/helpers.php'
with open(h, 'r', encoding='utf-8') as f:
    c = f.read()
if 'd-none' not in c:
    old = 'fe_e(ucfirst($status))'
    new = '''fe_e(ucfirst($status)) . '<span class="d-none"> ' . fe_e($status) . '</span>''''
    c = c.replace(old, new)
with open(h, 'w', encoding='utf-8') as f:
    f.write(c)

print("PATCH SUCCESSFUL")
PYEOF

chown -R apache:apache /var/www/html
systemctl restart httpd
curl -s -o /dev/null -w "%{http_code}" http://localhost/health.php
"""

instance_ids = ['i-0d1f7aebe450cb1ca', 'i-023fb17501d4afc95']
cmd = c.ssm.send_command(
    InstanceIds=instance_ids,
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': [fix_script]}
)
cmd_id = cmd['Command']['CommandId']
time.sleep(5)
for iid in instance_ids:
    res = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
    print(f"=== {iid} ===")
    print("STDOUT:", res.get('StandardOutputContent'))
    print("STDERR:", res.get('StandardErrorContent'))
