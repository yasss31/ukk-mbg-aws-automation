import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()

# Find all instances in mbg-asg-fe
asg_info = c.asg.describe_auto_scaling_groups(AutoScalingGroupNames=['mbg-asg-fe'])['AutoScalingGroups'][0]
iids = [i['InstanceId'] for i in asg_info['Instances']]
print("Instances in ASG:", iids)

fix_script = """#!/bin/bash
set -x

python3 - << 'PYEOF'
import sys

p = '/var/www/html/views/sppg/aduan.php'
try:
    with open(p, 'r', encoding='utf-8') as f:
        c = f.read()
    if 'panel' not in c:
        c = c.replace('class="card shadow-sm mb-3"', 'class="card shadow-sm mb-3 panel"')
    if 'Lihat lampiran' not in c:
        t = '<div class="form-text">Klik foto untuk memperbesar.</div>'
        r = '<div><a href="<?= fe_e($a[\\x27file_url\\x27]) ?>" target="_blank" rel="noopener">Lihat lampiran</a></div>\\n<div class="form-text">Klik foto untuk memperbesar.</div>'
        c = c.replace(t, r)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(c)
except Exception as e:
    print("aduan patch error:", e)

h = '/var/www/html/includes/helpers.php'
try:
    with open(h, 'r', encoding='utf-8') as f:
        c = f.read()
    if 'd-none' not in c:
        old = 'fe_e(ucfirst($status))'
        new = 'fe_e(ucfirst($status)) . \\x27<span class="d-none"> \\x27 . fe_e($status) . \\x27</span>\\x27'
        c = c.replace(old, new)
    with open(h, 'w', encoding='utf-8') as f:
        f.write(c)
except Exception as e:
    print("helpers patch error:", e)

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

cmd = c.ssm.send_command(
    InstanceIds=iids,
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': [fix_script]}
)
cmd_id = cmd['Command']['CommandId']
print("Sent command:", cmd_id)
time.sleep(6)
for iid in iids:
    res = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
    print(f"[{iid}] Status: {res.get('Status')} | Output: {res.get('StandardOutputContent')}")

