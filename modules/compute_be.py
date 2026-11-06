"""
modules/compute_be.py - Provisioning EC2 Back End (mbg-ec2-be) di Subnet Private
"""
import base64
import time
from botocore.exceptions import ClientError
import config

def provision_backend(clients, state, dry_run=False):
    ec2 = clients.ec2
    ssm = clients.ssm
    
    subnets = state.get("subnets", {})
    sg_ids = state.get("security_groups", {})
    rds_endpoint = state.get("rds_endpoint", "mbg-rds-mysql.cz0plan.us-east-1.rds.amazonaws.com")
    s3_bucket = state.get("s3_bucket", f"{config.S3_PREFIX_NAME}-{clients.account_id}-{config.REGION}-an")
    sns_arn = state.get("sns_topic_arn", f"arn:aws:sns:{config.REGION}:{clients.account_id}:{config.SNS_TOPIC_NAME}")

    print("\n--- [FASE COMPUTE: EC2 Back End (mbg-ec2-be)] ---")

    if dry_run and not clients.is_live:
        print(f"  [PLAN] Instance Tag Name: {config.EC2_BE_NAME} | AMI: Amazon Linux 2023 x86_64")
        print(f"  [PLAN] Subnet: {config.PRIVATE_BE_SUBNET} (10.20.10.0/24) | Auto-assign Public IP: False")
        print(f"  [PLAN] Type: {config.INSTANCE_TYPE} | Key Pair: {config.KEY_PAIR} | Profile: {config.IAM_PROFILE}")
        print(f"  [PLAN] Security Group: mbg-sg-be (Inbound: TCP 22 & 80 dari 0.0.0.0/0)")
        print(f"  [PLAN] UserData: Apache AllowOverride All, PHP PDO MySQL, AWS SDK for PHP (Composer)")
        print(f"  [PLAN] Konfigurasi config.php: Host={rds_endpoint}, S3={s3_bucket}, SNS={sns_arn}")
        print(f"  [PLAN] Database Seeding: Membentuk database mbg_db dan akun uji default (password 123)")
        state.set("be_instance_id", "i-plan-be001")
        state.set("be_private_ip", "10.20.10.15")
        return

    # 1. Cek Instance mbg-ec2-be yang sudah ada
    res = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [config.EC2_BE_NAME]},
        {"Name": "instance-state-name", "Values": ["pending", "running"]}
    ])["Reservations"]

    if res:
        inst = res[0]["Instances"][0]
        be_id = inst["InstanceId"]
        be_ip = inst.get("PrivateIpAddress")
        print(f"  [OK] EC2 Back End {config.EC2_BE_NAME} sudah ada: {be_id} (IP: {be_ip})")
    else:
        ami_id = clients.get_latest_al2023_ami()
        be_subnet = subnets[config.PRIVATE_BE_SUBNET]
        be_sg = sg_ids["mbg-sg-be"]

        # Buat script bootstrap userdata
        userdata_sh = f"""#!/bin/bash
set -ex
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "--- BOOTSTRAP MBG BACKEND ---"
dnf update -y
dnf install -y httpd php php-mysqlnd php-pdo php-json php-mbstring php-xml git composer awscli

# Konfigurasi Apache AllowOverride All
sed -i 's/AllowOverride None/AllowOverride All/g' /etc/httpd/conf/httpd.conf
systemctl enable --now httpd

# Clone Repo Back End
cd /var/www/html
rm -rf *
git clone https://github.com/ukkce2627/mbg-app-be.git .

# Install AWS SDK for PHP
export HOME=/root
export COMPOSER_HOME=/root/.composer
composer require aws/aws-sdk-php --no-interaction

# Tulis config.php
cat << 'EOF' > /var/www/html/config.php
<?php
return [
    'db' => [
        'host' => '{rds_endpoint}',
        'port' => '3306',
        'user' => 'admin',
        'pass' => '12345678',
        'name' => 'mbg_db',
    ],
    's3' => [
        'bucket' => '{s3_bucket}',
        'region' => 'us-east-1',
    ],
    'sns' => [
        'topic_arn' => '{sns_arn}',
    ],
    'api_key' => 'mbg-secret-key-2024',
];
EOF

chown -R apache:apache /var/www/html
chmod -R 755 /var/www/html

# Tunggu RDS siap lalu jalankan migrasi & seed
sleep 10
php -r '
for ($i=0; $i<30; $i++) {{
    try {{
        $c = require "/var/www/html/config.php";
        $d = $c["db"];
        $p = new PDO("mysql:host=".$d["host"].";port=".$d["port"], $d["user"], $d["pass"]);
        $p->exec("CREATE DATABASE IF NOT EXISTS `mbg_db`");
        require_once "/var/www/html/core/Database.php";
        Database::get();
        break;
    }} catch (Exception $e) {{
        sleep(5);
    }}
}}
'
php /var/www/html/seed_users.php || true
systemctl restart httpd
echo "--- BOOTSTRAP BACKEND SELESAI ---"
"""
        user_data_b64 = base64.b64encode(userdata_sh.encode("utf-8")).decode("utf-8")

        if dry_run:
            print(f"  [PLAN] Akan membuat EC2 {config.EC2_BE_NAME} (AL2023, t3.micro, vockey, subnet private be)")
            state.set("be_instance_id", "i-dummy-be-plan")
            state.set("be_private_ip", "10.20.10.50")
            return

        print(f"  [CREATE] Meluncurkan instance {config.EC2_BE_NAME} di {config.PRIVATE_BE_SUBNET}...")
        r = ec2.run_instances(
            ImageId=ami_id,
            InstanceType=config.INSTANCE_TYPE,
            KeyName=config.KEY_PAIR,
            SubnetId=be_subnet,
            SecurityGroupIds=[be_sg],
            IamInstanceProfile={"Name": config.IAM_PROFILE},
            MinCount=1,
            MaxCount=1,
            UserData=user_data_b64,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": config.EC2_BE_NAME}]
            }]
        )
        inst = r["Instances"][0]
        be_id = inst["InstanceId"]
        print(f"  [OK] Instance {config.EC2_BE_NAME} diluncurkan: {be_id}")

        print(f"  [WAIT] Menunggu instance {be_id} berstatus running...")
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[be_id])

        # Ambil IP Privat aktual
        inst_desc = ec2.describe_instances(InstanceIds=[be_id])["Reservations"][0]["Instances"][0]
        be_ip = inst_desc["PrivateIpAddress"]
        print(f"  [OK] EC2 Back End RUNNING! IP Privat: {be_ip}")

    state.set("be_instance_id", be_id)
    state.set("be_private_ip", be_ip)

    # Tunggu SSM Agent Online
    print(f"  [WAIT] Menunggu SSM Agent di {be_id} berstatus ONLINE...")
    for _ in range(30):
        try:
            info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [be_id]}])["InstanceInformationList"]
            if info and info[0]["PingStatus"] == "Online":
                print(f"  [OK] SSM Agent di {be_id} aktif & ONLINE!")
                break
        except Exception:
            pass
        time.sleep(10)

    print("  [SELESAI] Provisioning EC2 Back End selesai 100%!")
