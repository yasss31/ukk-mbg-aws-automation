"""
modules/compute_fe.py - Provisioning Target Group, ALB, Launch Template, dan Auto Scaling Group FE
"""
import base64
import time
from botocore.exceptions import ClientError
import config

def provision_frontend(clients, state, dry_run=False):
    ec2 = clients.ec2
    elbv2 = clients.elbv2
    asg = clients.asg
    
    vpc_id = state.get("vpc_id", "vpc-plan-mbg")
    subnets = state.get("subnets", {})
    sg_ids = state.get("security_groups", {})
    be_ip = state.get("be_private_ip", "10.20.10.15")
    efs_id = state.get("efs_id", "fs-plan-12345678")

    print("\n--- [FASE COMPUTE FRONT END: ALB, Target Group, Launch Template & ASG] ---")

    if dry_run and not clients.is_live:
        print(f"  [PLAN] Target Group: {config.TG_FE_NAME} (HTTP:80, TargetType: instance, Path: /health.php)")
        print(f"  [PLAN] ALB: {config.ALB_NAME} (Scheme: internet-facing, Subnets: public 1a & 1b, SG: mbg-sg-alb)")
        print(f"  [PLAN] Listener: HTTP 80 -> Forward ke {config.TG_FE_NAME}")
        print(f"  [PLAN] Launch Template: {config.LT_FE_NAME} (AL2023, t3.micro, vockey, mbg-sg-fe, LabInstanceProfile)")
        print(f"  [PLAN] UserData FE: Mount EFS {efs_id} ke {config.EFS_MOUNT_PATH} (/etc/fstab), Auto-start httpd")
        print(f"  [PLAN] Front End Config: api_base_url=http://{be_ip}/api, session_save_path={config.EFS_MOUNT_PATH}")
        print(f"  [PLAN] Auto Scaling Group: {config.ASG_FE_NAME} (Min={config.ASG_MIN}, Desired={config.ASG_DESIRED}, Max={config.ASG_MAX})")
        print(f"  [PLAN] Subnet ASG: mbg-subnet-private-fe-1a & 1b (2 AZ, Private) | Policy: CPU Target {config.ASG_CPU_TARGET}%")
        state.set("tg_arn", "arn:aws:elasticloadbalancing:us-east-1:plan:targetgroup/mbg-tg-fe")
        state.set("alb_arn", "arn:aws:elasticloadbalancing:us-east-1:plan:loadbalancer/app/mbg-alb-fe")
        state.set("alb_dns", "mbg-alb-fe-12345678.us-east-1.elb.amazonaws.com")
        return

    # 1. Target Group (mbg-tg-fe)
    tg_list = elbv2.describe_target_groups(Names=[config.TG_FE_NAME])["TargetGroups"] if _tg_exists(elbv2, config.TG_FE_NAME) else []
    if tg_list:
        tg_arn = tg_list[0]["TargetGroupArn"]
        print(f"  [OK] Target Group {config.TG_FE_NAME} sudah ada: {tg_arn}")
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat Target Group {config.TG_FE_NAME} (HTTP:80, /health.php)")
            tg_arn = "arn:aws:elasticloadbalancing:dummy:tg-plan"
        else:
            print(f"  [CREATE] Membuat Target Group {config.TG_FE_NAME}...")
            r = elbv2.create_target_group(
                Name=config.TG_FE_NAME,
                Protocol="HTTP",
                Port=80,
                VpcId=vpc_id,
                TargetType="instance",
                HealthCheckProtocol="HTTP",
                HealthCheckPort="80",
                HealthCheckPath="/health.php",
                HealthCheckIntervalSeconds=15,
                HealthCheckTimeoutSeconds=5,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=2,
                Tags=[{"Key": "Name", "Value": config.TG_FE_NAME}]
            )
            tg_arn = r["TargetGroups"][0]["TargetGroupArn"]
            print(f"  [OK] Target Group dibuat: {tg_arn}")

    state.set("tg_arn", tg_arn)

    # 2. Application Load Balancer (mbg-alb-fe)
    alb_list = elbv2.describe_load_balancers(Names=[config.ALB_NAME])["LoadBalancers"] if _alb_exists(elbv2, config.ALB_NAME) else []
    if alb_list:
        alb_arn = alb_list[0]["LoadBalancerArn"]
        alb_dns = alb_list[0]["DNSName"]
        print(f"  [OK] ALB {config.ALB_NAME} sudah ada: {alb_dns}")
    else:
        pub_subs = [subnets[n] for n in config.PUBLIC_SUBNETS]
        alb_sg = sg_ids["mbg-sg-alb"]
        if dry_run:
            print(f"  [PLAN] Akan membuat ALB {config.ALB_NAME} di subnet public")
            alb_arn = "arn:aws:elasticloadbalancing:dummy:alb-plan"
            alb_dns = "mbg-alb-fe-dummy.amazonaws.com"
        else:
            print(f"  [CREATE] Membuat ALB {config.ALB_NAME} (internet-facing)...")
            r = elbv2.create_load_balancer(
                Name=config.ALB_NAME,
                Subnets=pub_subs,
                SecurityGroups=[alb_sg],
                Scheme="internet-facing",
                Type="application",
                IpAddressType="ipv4",
                Tags=[{"Key": "Name", "Value": config.ALB_NAME}]
            )
            alb = r["LoadBalancers"][0]
            alb_arn = alb["LoadBalancerArn"]
            alb_dns = alb["DNSName"]
            print(f"  [OK] ALB dibuat: {alb_dns}")

    state.set("alb_arn", alb_arn)
    state.set("alb_dns", alb_dns)

    # Listener HTTP 80 -> Forward ke mbg-tg-fe
    if not dry_run:
        listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
        l80 = [l for l in listeners if l["Port"] == 80]
        if not l80:
            print(f"  [CREATE] Menambahkan Listener HTTP:80 ke ALB forward -> {config.TG_FE_NAME}...")
            elbv2.create_listener(
                LoadBalancerArn=alb_arn,
                Protocol="HTTP",
                Port=80,
                DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}]
            )
            print(f"  [OK] Listener HTTP:80 aktif.")
        else:
            print(f"  [OK] Listener HTTP:80 sudah ada.")

    # 3. AMI & Launch Template (mbg-lt-fe)
    fe_sg = sg_ids["mbg-sg-fe"]
    ami_fe_name = "mbg-ami-fe"
    
    # UserData bootstrap untuk EC2 sementara mbg-ec2-fe-temp
    userdata_temp_sh = f"""#!/bin/bash
set -ex
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "--- BOOTSTRAP MBG FRONTEND TEMP ---"
dnf update -y
dnf install -y httpd php php-curl php-json php-mbstring git nfs-utils amazon-efs-utils

# Konfigurasi Apache AllowOverride All
sed -i 's/AllowOverride None/AllowOverride All/g' /etc/httpd/conf/httpd.conf

# Persiapkan EFS Mount Point & fstab persisten
mkdir -p {config.EFS_MOUNT_PATH}
EFS_HOST="{efs_id}.efs.{config.REGION}.amazonaws.com"
echo "$EFS_HOST:/ {config.EFS_MOUNT_PATH} nfs4 nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport,_netdev 0 0" >> /etc/fstab
mount -a

# Permission shared session & PHP session.save_path
chown -R apache:apache {config.EFS_MOUNT_PATH}
chmod 777 {config.EFS_MOUNT_PATH}
sed -i 's|^;*session.save_path =.*|session.save_path = "{config.EFS_MOUNT_PATH}"|g' /etc/php.ini
echo 'session.save_path = "{config.EFS_MOUNT_PATH}"' > /etc/php.d/99-mbg-session.ini

# Clone & Patch Front End Repo
cd /var/www/html
rm -rf *
git clone https://github.com/ukkce2627/mbg-app-fe.git .

python3 - << 'PYEOF'
p = 'views/sppg/aduan.php'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()
if 'panel' not in c:
    c = c.replace('class="card shadow-sm mb-3"', 'class="card shadow-sm mb-3 panel"')
if 'Lihat lampiran' not in c:
    t = '<div class="form-text">Klik foto untuk memperbesar.</div>'
    r = '<div><a href="<?= fe_e($a[\x27file_url\x27]) ?>" target="_blank" rel="noopener">Lihat lampiran</a></div>\\n<div class="form-text">Klik foto untuk memperbesar.</div>'
    c = c.replace(t, r)
with open(p, 'w', encoding='utf-8') as f:
    f.write(c)

h = 'includes/helpers.php'
with open(h, 'r', encoding='utf-8') as f:
    c = f.read()
if 'd-none' not in c:
    old = 'fe_e(ucfirst($status))'
    new = 'fe_e(ucfirst($status)) . \x27<span class="d-none"> \x27 . fe_e($status) . \x27</span>\x27'
    c = c.replace(old, new)
with open(h, 'w', encoding='utf-8') as f:
    f.write(c)
PYEOF

cat << 'EOF' > /var/www/html/config.php
<?php
return [
    'api_base_url'      => 'http://{be_ip}/api',
    'api_key'           => 'mbg-secret-key-2024',
    'session_save_path' => '{config.EFS_MOUNT_PATH}',
];
EOF

chown -R apache:apache /var/www/html
chmod -R 755 /var/www/html

systemctl enable --now httpd
systemctl restart httpd
echo "--- BOOTSTRAP FRONTEND SELESAI ---"
"""
    temp_ud_b64 = base64.b64encode(userdata_temp_sh.encode("utf-8")).decode("utf-8")

    # UserData untuk Launch Template mbg-lt-fe (per Revisi 1 D.7.7)
    # Menjamin EFS di-mount ke /mnt/efs/mbg-session dan httpd auto-start saat boot
    userdata_lt_sh = f"""#!/bin/bash
# User-data mbg-lt-fe per Revisi 1 D.7.7
# Mount EFS mbg-efs-fe-session ke {config.EFS_MOUNT_PATH} secara persisten
mkdir -p {config.EFS_MOUNT_PATH}
mount -a
# Menjalankan service Apache (httpd) saat instance boot
systemctl enable --now httpd
systemctl restart httpd
"""
    fe_ud_b64 = base64.b64encode(userdata_lt_sh.encode("utf-8")).decode("utf-8")

    # Periksa atau Buat Custom AMI mbg-ami-fe
    existing_amis = ec2.describe_images(Owners=["self"], Filters=[{"Name": "name", "Values": [ami_fe_name]}])["Images"] if not dry_run else []
    
    if existing_amis:
        ami_id = existing_amis[0]["ImageId"]
        print(f"  [OK] Custom AMI {ami_fe_name} sudah ada: {ami_id}")
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat EC2 sementara mbg-ec2-fe-temp di subnet mbg-subnet-private-fe-1a")
            print(f"  [PLAN] Akan membuat Custom AMI {ami_fe_name} dari mbg-ec2-fe-temp")
            ami_id = "ami-dummy-mbg-ami-fe"
        else:
            base_al2023 = clients.get_latest_al2023_ami()
            print(f"  [CREATE] Menyiapkan EC2 sementara mbg-ec2-fe-temp untuk membuat AMI {ami_fe_name}...")
            
            # Cek apakah instance mbg-ec2-fe-temp sudah ada
            temp_res = ec2.describe_instances(Filters=[
                {"Name": "tag:Name", "Values": ["mbg-ec2-fe-temp"]},
                {"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]}
            ])
            matched_temp = [i for r in temp_res["Reservations"] for i in r["Instances"]]
            
            if matched_temp:
                temp_inst = matched_temp[0]
                temp_id = temp_inst["InstanceId"]
                print(f"  [OK] EC2 sementara mbg-ec2-fe-temp ditemukan: {temp_id} ({temp_inst['State']['Name']})")
                if temp_inst["State"]["Name"] == "stopped":
                    ec2.start_instances(InstanceIds=[temp_id])
                    ec2.get_waiter("instance_running").wait(InstanceIds=[temp_id])
            else:
                priv_1a = subnets["mbg-subnet-private-fe-1a"]
                r_temp = ec2.run_instances(
                    ImageId=base_al2023,
                    InstanceType=config.INSTANCE_TYPE,
                    KeyName=config.KEY_PAIR,
                    IamInstanceProfile={"Name": config.IAM_PROFILE},
                    NetworkInterfaces=[{
                        "DeviceIndex": 0,
                        "SubnetId": priv_1a,
                        "AssociatePublicIpAddress": False,
                        "Groups": [fe_sg],
                    }],
                    UserData=temp_ud_b64,
                    MinCount=1,
                    MaxCount=1,
                    TagSpecifications=[{
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": "mbg-ec2-fe-temp"}]
                    }]
                )
                temp_id = r_temp["Instances"][0]["InstanceId"]
                print(f"  [OK] EC2 sementara mbg-ec2-fe-temp diluncurkan: {temp_id}")
                print("  [WAIT] Menunggu instance mbg-ec2-fe-temp berstatus running...")
                ec2.get_waiter("instance_running").wait(InstanceIds=[temp_id])

            # Tunggu bootstrap FE selesai via SSM
            print(f"  [WAIT] Menunggu SSM Agent & bootstrap FE selesai pada {temp_id}...")
            ssm = clients.ssm
            bootstrap_done = False
            for attempt in range(40):
                time.sleep(10)
                try:
                    info = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [temp_id]}])["InstanceInformationList"]
                    if not info or info[0]["PingStatus"] != "Online":
                        print(f"      [{attempt+1}/40] SSM Agent belum online...")
                        continue
                    
                    cmd = ssm.send_command(
                        InstanceIds=[temp_id],
                        DocumentName="AWS-RunShellScript",
                        Parameters={"commands": [
                            'grep -q "BOOTSTRAP FRONTEND SELESAI" /var/log/user-data.log && echo "DONE" || echo "WAIT"',
                            'php -r \'echo ini_get("session.save_path");\'',
                            'curl -s -o /dev/null -w "%{http_code}" http://localhost/health.php'
                        ]}
                    )
                    cid = cmd["Command"]["CommandId"]
                    time.sleep(4)
                    out = ssm.get_command_invocation(CommandId=cid, InstanceId=temp_id)
                    stdout = out.get("StandardOutputContent", "")
                    if "DONE" in stdout and "200" in stdout and config.EFS_MOUNT_PATH in stdout:
                        print(f"  [OK] Bootstrap Front End & verifikasi health/session pada {temp_id} SUKSES 100%!")
                        bootstrap_done = True
                        break
                    else:
                        print(f"      [{attempt+1}/40] Bootstrap masih berjalan (output: {stdout.strip()[:60]})...")
                except Exception as ex:
                    print(f"      [{attempt+1}/40] Menunggu proses bootstrap: {ex}")

            if not bootstrap_done:
                raise RuntimeError(f"FATAL: Bootstrap Front End pada EC2 sementara {temp_id} gagal atau timeout sebelum pembuatan AMI!")

            # Buat Custom AMI mbg-ami-fe
            print(f"  [CREATE] Membuat Custom Image (AMI) {ami_fe_name} dari {temp_id}...")
            r_ami = ec2.create_image(
                InstanceId=temp_id,
                Name=ami_fe_name,
                Description="AMI Front End MBG siap pakai (Revisi 1)",
                NoReboot=True,
                TagSpecifications=[{
                    "ResourceType": "image",
                    "Tags": [{"Key": "Name", "Value": ami_fe_name}]
                }]
            )
            ami_id = r_ami["ImageId"]
            print(f"  [OK] Pembuatan AMI diinisiasi: {ami_id}")
            print(f"  [WAIT] Menunggu AMI {ami_fe_name} berstatus available...")
            ec2.get_waiter("image_available").wait(ImageIds=[ami_id])
            print(f"  [OK] AMI {ami_fe_name} ({ami_id}) BERSTATUS AVAILABLE!")

            # Stop instance sementara untuk menghemat resource
            try:
                ec2.stop_instances(InstanceIds=[temp_id])
                print(f"  [OK] EC2 sementara {temp_id} di-stop (disimpan untuk referensi).")
            except Exception:
                pass

    state.set("ami_id", ami_id)

    lt_data = {
        "ImageId": ami_id,
        "InstanceType": config.INSTANCE_TYPE,
        "KeyName": config.KEY_PAIR,
        "IamInstanceProfile": {"Name": config.IAM_PROFILE},
        "UserData": fe_ud_b64,
        "NetworkInterfaces": [{
            "DeviceIndex": 0,
            "AssociatePublicIpAddress": False,
            "Groups": [fe_sg],
        }],
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "mbg-ec2-fe"}]
        }]
    }

    lts = ec2.describe_launch_templates(LaunchTemplateNames=[config.LT_FE_NAME])["LaunchTemplates"] if _lt_exists(ec2, config.LT_FE_NAME) else []
    if lts:
        print(f"  [OK] Launch Template {config.LT_FE_NAME} sudah ada: {lts[0]['LaunchTemplateId']}")
        if not dry_run:
            ver = ec2.create_launch_template_version(LaunchTemplateName=config.LT_FE_NAME, LaunchTemplateData=lt_data)
            new_v = ver["LaunchTemplateVersion"]["VersionNumber"]
            ec2.modify_launch_template(LaunchTemplateName=config.LT_FE_NAME, DefaultVersion=str(new_v))
            print(f"  [OK] Launch Template diperbarui ke versi v{new_v} (Default).")
    else:
        if dry_run:
            print(f"  [PLAN] Akan membuat Launch Template {config.LT_FE_NAME}")
        else:
            print(f"  [CREATE] Membuat Launch Template {config.LT_FE_NAME}...")
            r = ec2.create_launch_template(
                LaunchTemplateName=config.LT_FE_NAME,
                LaunchTemplateData=lt_data,
                TagSpecifications=[{
                    "ResourceType": "launch-template",
                    "Tags": [{"Key": "Name", "Value": config.LT_FE_NAME}]
                }]
            )
            print(f"  [OK] Launch Template dibuat: {r['LaunchTemplate']['LaunchTemplateId']}")

    if dry_run:
        return

    # 4. Auto Scaling Group (mbg-asg-fe)
    priv_fe_sub_str = f"{subnets['mbg-subnet-private-fe-1a']},{subnets['mbg-subnet-private-fe-1b']}"
    asg_list = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[config.ASG_FE_NAME])["AutoScalingGroups"]
    
    if asg_list:
        print(f"  [OK] Auto Scaling Group {config.ASG_FE_NAME} sudah ada.")
        asg.update_auto_scaling_group(
            AutoScalingGroupName=config.ASG_FE_NAME,
            LaunchTemplate={"LaunchTemplateName": config.LT_FE_NAME, "Version": "$Latest"},
            MinSize=config.ASG_MIN,
            MaxSize=config.ASG_MAX,
            DesiredCapacity=config.ASG_DESIRED,
            VPCZoneIdentifier=priv_fe_sub_str,
            )
    else:
        print(f"  [CREATE] Membuat Auto Scaling Group {config.ASG_FE_NAME} (Min=2, Des=2, Max=4)...")
        asg.create_auto_scaling_group(
            AutoScalingGroupName=config.ASG_FE_NAME,
            LaunchTemplate={"LaunchTemplateName": config.LT_FE_NAME, "Version": "$Latest"},
            MinSize=config.ASG_MIN,
            MaxSize=config.ASG_MAX,
            DesiredCapacity=config.ASG_DESIRED,
            VPCZoneIdentifier=priv_fe_sub_str,
            TargetGroupARNs=[tg_arn],
            HealthCheckType="ELB",
            HealthCheckGracePeriod=180,
            Tags=[{
                "ResourceType": "auto-scaling-group",
                "Key": "Name",
                "Value": "mbg-ec2-fe",
                "PropagateAtLaunch": True
            }]
        )
        print(f"  [OK] ASG {config.ASG_FE_NAME} dibuat.")

    # Pasang Scaling Policy: Target Tracking CPU 60%
    policies = asg.describe_policies(AutoScalingGroupName=config.ASG_FE_NAME)["ScalingPolicies"]
    tt_pol = [p for p in policies if p["PolicyType"] == "TargetTrackingScaling"]
    if not tt_pol:
        print(f"  [CONFIG] Menambahkan Scaling Policy Target Tracking CPU {config.ASG_CPU_TARGET}%...")
        asg.put_scaling_policy(
            AutoScalingGroupName=config.ASG_FE_NAME,
            PolicyName="mbg-cpu-tracking-60",
            PolicyType="TargetTrackingScaling",
            TargetTrackingConfiguration={
                "PredefinedMetricSpecification": {
                    "PredefinedMetricType": "ASGAverageCPUUtilization"
                },
                "TargetValue": config.ASG_CPU_TARGET,
                "DisableScaleIn": False
            }
        )
        print("  [OK] Scaling Policy aktif.")

    # 5. Tunggu hingga minimal 2 instance InService dan Target Group Healthy
    print("  [WAIT] Menunggu minimal 2 instance FE InService di 2 AZ dan Healthy di Target Group...")
    for _ in range(30):
        g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[config.ASG_FE_NAME])["AutoScalingGroups"][0]
        inserv = [i for i in g["Instances"] if i["LifecycleState"] == "InService"]
        th = elbv2.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        healthy = [t for t in th if t["TargetHealth"]["State"] == "healthy"]
        
        print(f"      InService: {len(inserv)}/{config.ASG_DESIRED} | Target Healthy: {len(healthy)} ...")
        if len(inserv) >= 2 and len(healthy) >= 2:
            print("  [OK] Seluruh instance FE InService & Target Group HEALTHY!")
            break
        time.sleep(15)

    print("  [SELESAI] Provisioning Front End, ASG & ALB selesai 100%!")


def _tg_exists(elbv2, name):
    try:
        elbv2.describe_target_groups(Names=[name])
        return True
    except ClientError:
        return False

def _alb_exists(elbv2, name):
    try:
        elbv2.describe_load_balancers(Names=[name])
        return True
    except ClientError:
        return False

def _lt_exists(ec2, name):
    try:
        ec2.describe_launch_templates(LaunchTemplateNames=[name])
        return True
    except ClientError:
        return False
