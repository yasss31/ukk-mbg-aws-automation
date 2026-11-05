import sys, time
sys.stdout.reconfigure(encoding='utf-8')
from aws_clients import AWSClients
c = AWSClients()

be_id = 'i-0d9e033545447e902'

sh_script = """#!/bin/bash
set -ex
export HOME=/root
export COMPOSER_HOME=/root/.composer
cd /var/www/html

# 1. Install AWS SDK
composer require aws/aws-sdk-php --no-interaction

# 2. Tulis config.php
cat << 'EOF' > /var/www/html/config.php
<?php
return [
    'db' => [
        'host' => 'mbg-rds-mysql.cjswqlp0e9y0.us-east-1.rds.amazonaws.com',
        'port' => '3306',
        'user' => 'admin',
        'pass' => '12345678',
        'name' => 'mbg_db',
    ],
    's3' => [
        'bucket' => 'mbg-uploads-937385515130-us-east-1-an',
        'region' => 'us-east-1',
    ],
    'sns' => [
        'topic_arn' => 'arn:aws:sns:us-east-1:937385515130:mbg-sns-notifikasi',
    ],
    'api_key' => 'mbg-secret-key-2024',
];
EOF

chown -R apache:apache /var/www/html
chmod -R 755 /var/www/html

# 3. Jalankan Database Init & Seed
php -r '
require_once "/var/www/html/core/Database.php";
$pdo = Database::get();
echo "Database and tables successfully created!\n";
'

php /var/www/html/seed_users.php

systemctl restart httpd
curl -s http://localhost/health
"""

cmd = c.ssm.send_command(
    InstanceIds=[be_id],
    DocumentName='AWS-RunShellScript',
    Parameters={'commands': [sh_script]}
)
cmd_id = cmd['Command']['CommandId']
print("Command sent to BE:", cmd_id)
time.sleep(10)
res = c.ssm.get_command_invocation(CommandId=cmd_id, InstanceId=be_id)
print("=== STDOUT ===")
print(res.get('StandardOutputContent'))
print("=== STDERR ===")
print(res.get('StandardErrorContent'))

