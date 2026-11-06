"""
destroy.py - Sistem Otomasi Penghapusan Resource UKK Cloud Engineer MBG
Menghapus seluruh resource AWS yang dibuat khusus untuk proyek MBG secara reverse-dependency.
Dilengkapi perlindungan Account ID guard, verifikasi nama/tag resource, simulasi (--plan),
dan konfirmasi interaktif per fase (--apply).
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
from botocore.exceptions import ClientError

import config
from aws_clients import AWSClients
from state_manager import StateManager


class DestroyLogger:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir or (config.PROJECT_ROOT / "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"destroy_{ts}.log"

    def log(self, message):
        clean_msg = str(message)
        print(clean_msg)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(clean_msg + "\n")
        except Exception:
            pass


def banner():
    print("""
=============================================================================
  SISTEM CLEANUP / DESTROY UKK CLOUD ENGINEER 2026/2027 - SMKN 1 BANYUMAS
  Penghapusan Resource MBG (AWS Academy Learner Lab - us-east-1)
=============================================================================
""")


def verify_account_guard(clients, logger):
    """
    Memastikan kredensial aktif dan berasal dari akun AWS yang diizinkan.
    Mencegah penghapusan resource pada akun yang salah.
    """
    if not clients.is_live or not clients.account_id:
        logger.log("[ERROR] Gagal verifikasi identitas AWS STS. Sesi tidak aktif.")
        sys.exit(1)
    
    # Cek format 12-digit AWS Account ID
    if len(clients.account_id) != 12 or not clients.account_id.isdigit():
        logger.log(f"[ERROR] AWS Account ID tidak valid: {clients.account_id}")
        sys.exit(1)

    logger.log(f"  [GUARD OK] Terhubung ke AWS Account: {clients.account_id}")
    logger.log(f"  [GUARD OK] Caller ARN: {clients.caller_arn}")
    logger.log(f"  [GUARD OK] Target Region: {config.REGION}")


def prompt_confirmation(phase_name, resource_desc, auto_approve=False):
    """
    Meminta konfirmasi ketik 'DELETE' untuk melanjutkan penghapusan.
    """
    if auto_approve:
        return True

    print("\n" + "-" * 70)
    print(f"PERINGATAN: Akan menghapus resource fase [{phase_name}]")
    print(f"Rincian: {resource_desc}")
    print("-" * 70)
    
    val = input(f"Ketik 'DELETE' untuk melanjutkan penghapusan fase {phase_name} (atau 'skip' untuk melewati): ").strip()
    if val == "DELETE":
        return True
    elif val.lower() == "skip":
        print(f"--> Fase [{phase_name}] dilewati pengguna.")
        return False
    else:
        print("--> Input tidak cocok. Operasi dibatalkan demi keamanan.")
        sys.exit(0)


# =============================================================================
# FASE 1: FRONTEND (ASG, ALB, LISTENER, TARGET GROUP, LAUNCH TEMPLATE)
# =============================================================================
def destroy_frontend(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    asg = clients.asg
    elbv2 = clients.elbv2
    ec2 = clients.ec2

    log("\n--- [FASE 1: CLEANUP FRONTEND (ASG, ALB, TG, Launch Template)] ---")

    # 1. Auto Scaling Group
    asg_name = config.ASG_FE_NAME
    asg_list = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])["AutoScalingGroups"]
    if asg_list:
        g = asg_list[0]
        insts = g.get("Instances", [])
        log(f"  Ditemukan ASG {asg_name} dengan {len(insts)} instance aktif.")
        if dry_run:
            log(f"  [PLAN] Akan set kapasitas ASG {asg_name} ke 0, tunggu instance terminate, lalu hapus ASG.")
        else:
            log(f"  [MODIFY] Menyetel kapasitas ASG {asg_name} ke Min=0, Desired=0, Max=0...")
            asg.update_auto_scaling_group(
                AutoScalingGroupName=asg_name,
                MinSize=0,
                MaxSize=0,
                DesiredCapacity=0
            )
            # Tunggu instance terminate
            log(f"  [WAIT] Menunggu seluruh instance ASG selesai terminate...")
            while True:
                curr = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])["AutoScalingGroups"]
                if not curr or len(curr[0].get("Instances", [])) == 0:
                    break
                log(f"      Sisa instance: {len(curr[0].get('Instances', []))} ...")
                time.sleep(10)

            # Hapus scaling policy
            pol = asg.describe_policies(AutoScalingGroupName=asg_name)["ScalingPolicies"]
            for p in pol:
                log(f"  [DELETE] Menghapus scaling policy {p['PolicyName']}...")
                asg.delete_policy(AutoScalingGroupName=asg_name, PolicyName=p["PolicyName"])

            log(f"  [DELETE] Menghapus Auto Scaling Group {asg_name}...")
            asg.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)
            log(f"  [OK] ASG {asg_name} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] ASG {asg_name} sudah tidak ada.")

    # 2. ALB & Listener
    alb_name = config.ALB_NAME
    albs = [a for a in elbv2.describe_load_balancers()["LoadBalancers"] if a.get("LoadBalancerName") == alb_name]
    if albs:
        alb_arn = albs[0]["LoadBalancerArn"]
        log(f"  Ditemukan ALB {alb_name}: {alb_arn}")
        if dry_run:
            log(f"  [PLAN] Akan menghapus listener dan ALB {alb_name}.")
        else:
            # Hapus listener port 80
            listeners = elbv2.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
            for l in listeners:
                log(f"  [DELETE] Menghapus Listener {l['ListenerArn']}...")
                elbv2.delete_listener(ListenerArn=l["ListenerArn"])

            log(f"  [DELETE] Menghapus ALB {alb_name}...")
            elbv2.delete_load_balancer(LoadBalancerArn=alb_arn)
            log(f"  [WAIT] Menunggu ALB selesai dihapus...")
            while True:
                curr = [a for a in elbv2.describe_load_balancers()["LoadBalancers"] if a.get("LoadBalancerName") == alb_name]
                if not curr:
                    break
                time.sleep(5)
            log(f"  [OK] ALB {alb_name} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] ALB {alb_name} sudah tidak ada.")

    # 3. Target Group
    tg_name = config.TG_FE_NAME
    tgs = [t for t in elbv2.describe_target_groups()["TargetGroups"] if t.get("TargetGroupName") == tg_name]
    if tgs:
        tg_arn = tgs[0]["TargetGroupArn"]
        if dry_run:
            log(f"  [PLAN] Akan menghapus Target Group {tg_name}: {tg_arn}")
        else:
            log(f"  [DELETE] Menghapus Target Group {tg_name}...")
            elbv2.delete_target_group(TargetGroupArn=tg_arn)
            log(f"  [OK] Target Group {tg_name} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] Target Group {tg_name} sudah tidak ada.")

    # 4. Launch Template
    lt_name = config.LT_FE_NAME
    lts = ec2.describe_launch_templates(Filters=[{"Name": "launch-template-name", "Values": [lt_name]}])["LaunchTemplates"]
    if lts:
        lt_id = lts[0]["LaunchTemplateId"]
        if dry_run:
            log(f"  [PLAN] Akan menghapus Launch Template {lt_name} ({lt_id})")
        else:
            log(f"  [DELETE] Menghapus Launch Template {lt_name} ({lt_id})...")
            ec2.delete_launch_template(LaunchTemplateName=lt_name)
            log(f"  [OK] Launch Template {lt_name} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] Launch Template {lt_name} sudah tidak ada.")

    # 5. EC2 Frontend Sementara (mbg-ec2-fe-temp)
    temp_res = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["mbg-ec2-fe-temp"]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopped", "stopping"]}
    ])
    temp_insts = [i["InstanceId"] for r in temp_res["Reservations"] for i in r["Instances"]]
    if temp_insts:
        if dry_run:
            log(f"  [PLAN] Akan menterminate EC2 sementara mbg-ec2-fe-temp: {temp_insts}")
        else:
            log(f"  [DELETE] Menterminate EC2 sementara mbg-ec2-fe-temp {temp_insts}...")
            ec2.terminate_instances(InstanceIds=temp_insts)
            log("  [WAIT] Menunggu mbg-ec2-fe-temp terminated...")
            try:
                ec2.get_waiter("instance_terminated").wait(InstanceIds=temp_insts)
            except Exception:
                pass
            log("  [OK] EC2 sementara mbg-ec2-fe-temp berhasil di-terminate.")

    # 6. Custom AMI (mbg-ami-fe) dan snapshot EBS terkait
    amis = ec2.describe_images(Owners=["self"], Filters=[{"Name": "name", "Values": ["mbg-ami-fe"]}])["Images"]
    for a in amis:
        ami_id = a["ImageId"]
        snaps = [
            bd["Ebs"]["SnapshotId"]
            for bd in a.get("BlockDeviceMappings", [])
            if "Ebs" in bd and "SnapshotId" in bd["Ebs"]
        ]
        if dry_run:
            log(f"  [PLAN] Akan menderegister AMI {ami_id} (mbg-ami-fe) dan menghapus snapshot {snaps}")
        else:
            log(f"  [DELETE] Menderegister AMI {ami_id} (mbg-ami-fe)...")
            ec2.deregister_image(ImageId=ami_id)
            log(f"  [OK] AMI {ami_id} dideregister.")
            for s in snaps:
                try:
                    log(f"  [DELETE] Menghapus snapshot {s}...")
                    ec2.delete_snapshot(SnapshotId=s)
                    log(f"  [OK] Snapshot {s} dihapus.")
                except Exception as ex:
                    log(f"  [WARN] Gagal menghapus snapshot {s}: {ex}")

    return True


# =============================================================================
# FASE 2: BACKEND (EC2 mbg-ec2-be)
# =============================================================================
def destroy_backend(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    ec2 = clients.ec2

    log("\n--- [FASE 2: CLEANUP BACKEND (EC2 mbg-ec2-be)] ---")
    inst_name = config.EC2_BE_NAME
    res = ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [inst_name]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}
    ])["Reservations"]

    instances = [i for r in res for i in r["Instances"]]
    if instances:
        for i in instances:
            iid = i["InstanceId"]
            state_nm = i["State"]["Name"]
            if dry_run:
                log(f"  [PLAN] Akan terminate EC2 Back End: {inst_name} ({iid}) [Status: {state_nm}]")
            else:
                log(f"  [TERMINATE] Menghentikan & menghapus EC2 {inst_name} ({iid})...")
                ec2.terminate_instances(InstanceIds=[iid])
                log(f"  [WAIT] Menunggu instance {iid} berstatus terminated...")
                waiter = ec2.get_waiter("instance_terminated")
                waiter.wait(InstanceIds=[iid])
                log(f"  [OK] Instance {iid} berhasil di-terminate.")
    else:
        log(f"  [SKIPPED] EC2 Back End {inst_name} sudah tidak ada.")

    return True


# =============================================================================
# FASE 3: EFS (MOUNT TARGETS & FILE SYSTEM)
# =============================================================================
def destroy_efs(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    efs = clients.efs

    log("\n--- [FASE 3: CLEANUP EFS (Mount Targets & File System)] ---")
    fs_list = efs.describe_file_systems()["FileSystems"]
    matched = [f for f in fs_list if f.get("Name") == config.EFS_NAME or any(t.get("Value") == config.EFS_NAME for t in f.get("Tags", []))]

    if matched:
        fs_id = matched[0]["FileSystemId"]
        log(f"  Ditemukan EFS {config.EFS_NAME}: {fs_id}")
        
        # Cek Mount Targets
        mts = efs.describe_mount_targets(FileSystemId=fs_id)["MountTargets"]
        if dry_run:
            log(f"  [PLAN] Akan menghapus {len(mts)} mount target EFS, lalu menghapus file system {fs_id}.")
        else:
            for m in mts:
                mt_id = m["MountTargetId"]
                log(f"  [DELETE] Menghapus Mount Target {mt_id} di subnet {m['SubnetId']}...")
                efs.delete_mount_target(MountTargetId=mt_id)

            if mts:
                log(f"  [WAIT] Menunggu seluruh mount target EFS selesai dihapus...")
                while True:
                    curr_mts = efs.describe_mount_targets(FileSystemId=fs_id)["MountTargets"]
                    if not curr_mts:
                        break
                    time.sleep(5)
                log(f"  [OK] Seluruh mount target EFS telah terhapus.")

            log(f"  [DELETE] Menghapus File System EFS {fs_id}...")
            # Kadang EFS butuh delay beberapa detik setelah MT terhapus
            time.sleep(3)
            efs.delete_file_system(FileSystemId=fs_id)
            log(f"  [OK] EFS {fs_id} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] EFS {config.EFS_NAME} sudah tidak ada.")

    return True


# =============================================================================
# FASE 4: DATABASE (RDS MYSQL & DB SUBNET GROUP)
# =============================================================================
def destroy_database(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    rds = clients.rds

    log("\n--- [FASE 4: CLEANUP DATABASE (RDS MySQL & DB Subnet Group)] ---")
    db_id = config.RDS_IDENTIFIER
    try:
        insts = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"]
        if insts:
            status = insts[0]["DBInstanceStatus"]
            log(f"  Ditemukan RDS MySQL {db_id} [Status: {status}]")
            if dry_run:
                log(f"  [PLAN] Akan menghapus RDS instance {db_id} (SkipFinalSnapshot=True) lalu DB Subnet Group.")
            else:
                if status != "deleting":
                    log(f"  [DELETE] Menghapus RDS MySQL {db_id} (SkipFinalSnapshot=True)...")
                    rds.delete_db_instance(
                        DBInstanceIdentifier=db_id,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True
                    )
                log(f"  [WAIT] Menunggu RDS {db_id} selesai dihapus (~3-5 menit)...")
                while True:
                    try:
                        rds.describe_db_instances(DBInstanceIdentifier=db_id)
                        time.sleep(15)
                    except ClientError as e:
                        if "DBInstanceNotFound" in str(e):
                            log(f"  [OK] RDS MySQL {db_id} telah terhapus.")
                            break
                        raise
    except ClientError as e:
        if "DBInstanceNotFound" in str(e):
            log(f"  [SKIPPED] RDS MySQL {db_id} sudah tidak ada.")
        else:
            raise

    # Hapus DB Subnet Group
    sng_name = config.RDS_SUBNET_GROUP
    try:
        rds.describe_db_subnet_groups(DBSubnetGroupName=sng_name)
        if dry_run:
            log(f"  [PLAN] Akan menghapus DB Subnet Group {sng_name}.")
        else:
            log(f"  [DELETE] Menghapus DB Subnet Group {sng_name}...")
            time.sleep(2)
            rds.delete_db_subnet_group(DBSubnetGroupName=sng_name)
            log(f"  [OK] DB Subnet Group {sng_name} berhasil dihapus.")
    except ClientError as e:
        if "DBSubnetGroupNotFoundFault" in str(e):
            log(f"  [SKIPPED] DB Subnet Group {sng_name} sudah tidak ada.")
        else:
            raise

    return True


# =============================================================================
# FASE 5: STORAGE (S3 BUCKET & ALL OBJECTS)
# =============================================================================
def destroy_storage(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    s3 = clients.s3
    bucket_name = f"{config.S3_PREFIX_NAME}-{clients.account_id}-{config.REGION}-an"

    log("\n--- [FASE 5: CLEANUP STORAGE (Amazon S3 Bucket)] ---")
    try:
        s3.head_bucket(Bucket=bucket_name)
        log(f"  Ditemukan Bucket S3: {bucket_name}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchBucket"):
            log(f"  [SKIPPED] Bucket S3 {bucket_name} sudah tidak ada.")
            return True
        raise

    if dry_run:
        log(f"  [PLAN] Akan mengosongkan seluruh objek, versi, dan delete markers di bucket {bucket_name}, lalu menghapus bucket.")
        return True

    # 1. Hapus versi objek & delete markers jika ada
    log(f"  [PURGE] Memeriksa dan menghapus seluruh object versions & delete markers...")
    try:
        paginator = s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket_name):
            to_delete = []
            for v in page.get("Versions", []):
                to_delete.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            for dm in page.get("DeleteMarkers", []):
                to_delete.append({"Key": dm["Key"], "VersionId": dm["VersionId"]})
            if to_delete:
                s3.delete_objects(Bucket=bucket_name, Delete={"Objects": to_delete})
                log(f"      Dihapus {len(to_delete)} versi objek / delete markers...")
    except ClientError:
        pass

    # 2. Hapus objek standar
    log(f"  [PURGE] Memeriksa dan menghapus objek reguler...")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name):
        objs = page.get("Contents", [])
        if objs:
            to_delete = [{"Key": o["Key"]} for o in objs]
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": to_delete})
            log(f"      Dihapus {len(to_delete)} objek...")

    # 3. Hapus bucket
    log(f"  [DELETE] Menghapus bucket S3 {bucket_name}...")
    s3.delete_bucket(Bucket=bucket_name)
    log(f"  [OK] Bucket S3 {bucket_name} berhasil dihapus.")
    return True


# =============================================================================
# FASE 6: NOTIFICATION (SNS TOPIC & SUBSCRIPTION)
# =============================================================================
def destroy_notification(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    sns = clients.sns
    topic_name = config.SNS_TOPIC_NAME

    log("\n--- [FASE 6: CLEANUP NOTIFIKASI (Amazon SNS)] ---")
    topics = sns.list_topics()["Topics"]
    matched = [t["TopicArn"] for t in topics if t["TopicArn"].endswith(f":{topic_name}")]

    if matched:
        topic_arn = matched[0]
        log(f"  Ditemukan SNS Topic: {topic_arn}")
        
        # Subscriptions
        subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]
        if dry_run:
            log(f"  [PLAN] Akan menghapus {len(subs)} subscription dan SNS topic {topic_arn}.")
        else:
            for s in subs:
                s_arn = s["SubscriptionArn"]
                if s_arn and s_arn != "PendingConfirmation":
                    log(f"  [UNSUBSCRIBE] Menghapus subscription: {s_arn}...")
                    try:
                        sns.unsubscribe(SubscriptionArn=s_arn)
                    except ClientError:
                        pass
            
            log(f"  [DELETE] Menghapus SNS Topic {topic_arn}...")
            sns.delete_topic(TopicArn=topic_arn)
            log(f"  [OK] SNS Topic {topic_name} berhasil dihapus.")
    else:
        log(f"  [SKIPPED] SNS Topic {topic_name} sudah tidak ada.")

    return True


# =============================================================================
# FASE 7: SECURITY (5 SECURITY GROUPS)
# =============================================================================
def destroy_security(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    ec2 = clients.ec2

    log("\n--- [FASE 7: CLEANUP SECURITY (5 Security Groups)] ---")
    
    # Ambil VPC ID
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
    vpc_id = vpcs[0]["VpcId"] if vpcs else None

    filters = [{"Name": "group-name", "Values": list(config.SECURITY_GROUPS.keys())}]
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})

    sgs = ec2.describe_security_groups(Filters=filters)["SecurityGroups"]
    sg_map = {g["GroupName"]: g["GroupId"] for g in sgs}

    if not sg_map:
        log("  [SKIPPED] Seluruh Security Group MBG sudah tidak ada.")
        return True

    if dry_run:
        for name, gid in sg_map.items():
            log(f"  [PLAN] Akan menghapus Security Group {name} ({gid})")
        return True

    # 1. Cabut semua ingress rules terlebih dahulu untuk memutus circular references antar SG
    log("  [REVOKE] Mencabut seluruh aturan inbound pada semua Security Group MBG...")
    for name, gid in sg_map.items():
        try:
            g_desc = ec2.describe_security_groups(GroupIds=[gid])["SecurityGroups"][0]
            ip_perms = g_desc.get("IpPermissions", [])
            if ip_perms:
                ec2.revoke_security_group_ingress(GroupId=gid, IpPermissions=ip_perms)
                log(f"      Inbound rules dicabut dari {name} ({gid}).")
        except ClientError as e:
            log(f"      [WARN] Cabut rule {name}: {e}")

    # 2. Hapus masing-masing SG dengan retry untuk mengantisipasi pelepasan ENI
    log("  [DELETE] Menghapus Security Groups...")
    for name, gid in sg_map.items():
        deleted = False
        for attempt in range(5):
            try:
                ec2.delete_security_group(GroupId=gid)
                log(f"  [OK] Security Group {name} ({gid}) berhasil dihapus.")
                deleted = True
                break
            except ClientError as e:
                if "DependencyViolation" in str(e):
                    log(f"      Menunggu pelepasan interface pada {name} (attempt {attempt+1}/5)...")
                    time.sleep(6)
                elif "InvalidGroup.NotFound" in str(e):
                    deleted = True
                    break
                else:
                    log(f"      [ERROR] Gagal hapus {name}: {e}")
                    break
        if not deleted:
            log(f"  [WARN] Security Group {name} ({gid}) belum dapat dihapus karena dependensi aktif.")

    return True


# =============================================================================
# FASE 8: NETWORK (ROUTE TABLES, NAT GW, EIP, IGW, SUBNETS, VPC)
# =============================================================================
def destroy_network(clients, state, dry_run=False, logger=None):
    log = logger.log if logger else print
    ec2 = clients.ec2

    log("\n--- [FASE 8: CLEANUP NETWORK (Routing, NAT GW, EIP, IGW, Subnets, VPC)] ---")

    # 1. Temukan VPC MBG
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.VPC_NAME]}])["Vpcs"]
    if not vpcs:
        log(f"  [SKIPPED] VPC {config.VPC_NAME} sudah tidak ada.")
        return True

    vpc_id = vpcs[0]["VpcId"]
    log(f"  Ditemukan VPC MBG: {config.VPC_NAME} ({vpc_id})")

    # A. Custom Route Tables
    rts = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
    for rt in rts:
        rt_id = rt["RouteTableId"]
        is_main = any(a.get("Main") for a in rt.get("Associations", []))
        if is_main:
            continue
        
        # Disassociate custom associations
        if dry_run:
            log(f"  [PLAN] Akan menghapus Route Table custom {rt_id}")
        else:
            for a in rt.get("Associations", []):
                aid = a.get("RouteTableAssociationId")
                if aid and not a.get("Main"):
                    log(f"  [DISASSOCIATE] Melepas asosiasi {aid} pada RT {rt_id}...")
                    try:
                        ec2.disassociate_route_table(AssociationId=aid)
                    except ClientError:
                        pass
            try:
                log(f"  [DELETE] Menghapus Route Table {rt_id}...")
                ec2.delete_route_table(RouteTableId=rt_id)
                log(f"  [OK] Route Table {rt_id} berhasil dihapus.")
            except ClientError as e:
                log(f"  [WARN] Gagal hapus RT {rt_id}: {e}")

    # B. NAT Gateway & Elastic IP
    nats = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "state", "Values": ["pending", "available", "deleting"]}
        ]
    )["NatGateways"]

    eips_to_release = []
    for n in nats:
        nat_id = n["NatGatewayId"]
        for addr in n.get("NatGatewayAddresses", []):
            alloc_id = addr.get("AllocationId")
            if alloc_id:
                eips_to_release.append(alloc_id)
        
        if dry_run:
            log(f"  [PLAN] Akan menghapus NAT Gateway {nat_id} dan melepas Elastic IP.")
        else:
            if n["State"] != "deleted":
                log(f"  [DELETE] Menghapus NAT Gateway {nat_id}...")
                ec2.delete_nat_gateway(NatGatewayId=nat_id)
                log(f"  [WAIT] Menunggu NAT Gateway {nat_id} berstatus deleted (~60-90 detik)...")
                while True:
                    cur = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]
                    if cur["State"] == "deleted":
                        log(f"  [OK] NAT Gateway {nat_id} telah deleted.")
                        break
                    time.sleep(10)

    # Lepas Elastic IP
    if not dry_run:
        for alloc_id in eips_to_release:
            log(f"  [RELEASE] Melepas Elastic IP {alloc_id}...")
            try:
                ec2.release_address(AllocationId=alloc_id)
                log(f"  [OK] Elastic IP {alloc_id} berhasil dilepas.")
            except ClientError as e:
                log(f"  [WARN] Gagal lepas Elastic IP {alloc_id}: {e}")

    # C. Internet Gateway
    igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]
    for igw in igws:
        igw_id = igw["InternetGatewayId"]
        if dry_run:
            log(f"  [PLAN] Akan detach & delete Internet Gateway {igw_id}")
        else:
            log(f"  [DETACH] Melepas IGW {igw_id} dari VPC {vpc_id}...")
            ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
            log(f"  [DELETE] Menghapus Internet Gateway {igw_id}...")
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            log(f"  [OK] IGW {igw_id} berhasil dihapus.")

    # D. Subnets
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    for s in subs:
        sid = s["SubnetId"]
        sname = [t["Value"] for t in s.get("Tags", []) if t["Key"] == "Name"]
        nm = sname[0] if sname else sid
        if dry_run:
            log(f"  [PLAN] Akan menghapus Subnet {nm} ({sid})")
        else:
            log(f"  [DELETE] Menghapus Subnet {nm} ({sid})...")
            # Loop retry singkat bila ada ENI background yang baru selesai dilepas
            deleted = False
            for _ in range(5):
                try:
                    ec2.delete_subnet(SubnetId=sid)
                    log(f"  [OK] Subnet {nm} ({sid}) berhasil dihapus.")
                    deleted = True
                    break
                except ClientError:
                    time.sleep(4)
            if not deleted:
                log(f"  [WARN] Subnet {nm} ({sid}) belum dapat dihapus.")

    # E. VPC
    if dry_run:
        log(f"  [PLAN] Akan menghapus VPC {config.VPC_NAME} ({vpc_id})")
    else:
        log(f"  [DELETE] Menghapus VPC {config.VPC_NAME} ({vpc_id})...")
        deleted = False
        for _ in range(5):
            try:
                ec2.delete_vpc(VpcId=vpc_id)
                log(f"  [OK] VPC {config.VPC_NAME} ({vpc_id}) berhasil dihapus.")
                deleted = True
                break
            except ClientError:
                time.sleep(5)
        if not deleted:
            log(f"  [WARN] VPC {config.VPC_NAME} ({vpc_id}) belum dapat dihapus. Periksa dependensi tersisa.")

    return True


# =============================================================================
# FASE 9: LOCAL STATE RESET
# =============================================================================
def destroy_local_state(state, dry_run=False, logger=None):
    log = logger.log if logger else print
    log("\n--- [FASE 9: RESET LOCAL STATE] ---")
    if dry_run:
        log("  [PLAN] Akan mereset state/resources.json menjadi state kosong.")
    else:
        state.clear()
        log("  [OK] File state/resources.json berhasil dikosongkan (reset ke {}).")
    return True


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
def main():
    banner()
    parser = argparse.ArgumentParser(description="Script Cleanup / Destroy Resource UKK MBG AWS Learner Lab")
    parser.add_argument("--plan", action="store_true", help="Simulasi audit resource yang akan dihapus tanpa mengubah AWS")
    parser.add_argument("--apply", action="store_true", help="Eksekusi penghapusan resource AWS dengan konfirmasi interaktif")
    parser.add_argument("--phase", choices=["frontend", "backend", "efs", "database", "storage", "notification", "security", "network", "state"],
                        help="Hapus hanya fase tertentu")
    parser.add_argument("--credentials", help="Lokasi file credentials AWS Academy (default: ./credentials)")
    parser.add_argument("--profile", default="default", help="Nama profil kredensial (default: default)")
    parser.add_argument("--auto-approve", action="store_true", help="Lewati prompt konfirmasi interaktif (Gunakan dengan sangat hati-hati)")

    args = parser.parse_args()

    if not (args.plan or args.apply):
        parser.print_help()
        print("\nContoh:")
        print("  python destroy.py --plan             # Cek daftar resource yang akan dihapus")
        print("  python destroy.py --apply            # Jalankan cleanup dengan konfirmasi per fase")
        sys.exit(0)

    logger = DestroyLogger()
    logger.log("[1] Memeriksa Kredensial dan Identitas AWS...")

    try:
        clients = AWSClients(cred_path=args.credentials, profile=args.profile, allow_offline=args.plan)
        verify_account_guard(clients, logger)
    except Exception as e:
        logger.log(f"\n[ERROR] GAGAL OTENTIKASI / VERIFIKASI AWS:\n{e}\n")
        sys.exit(1)

    state = StateManager()

    phases = {
        "frontend": ("Front End (ASG, ALB, Listener, Target Group, Launch Template)",
                     lambda dry: destroy_frontend(clients, state, dry_run=dry, logger=logger)),
        "backend": ("Back End EC2 (mbg-ec2-be)",
                    lambda dry: destroy_backend(clients, state, dry_run=dry, logger=logger)),
        "efs": ("EFS Mount Targets & File System (mbg-efs-fe-session)",
                lambda dry: destroy_efs(clients, state, dry_run=dry, logger=logger)),
        "database": ("RDS MySQL (mbg-rds-mysql) & DB Subnet Group",
                     lambda dry: destroy_database(clients, state, dry_run=dry, logger=logger)),
        "storage": ("Amazon S3 Bucket & Objects (mbg-uploads-*)",
                    lambda dry: destroy_storage(clients, state, dry_run=dry, logger=logger)),
        "notification": ("Amazon SNS Topic (mbg-sns-notifikasi) & Subscription",
                         lambda dry: destroy_notification(clients, state, dry_run=dry, logger=logger)),
        "security": ("5 Security Groups (mbg-sg-*)",
                     lambda dry: destroy_security(clients, state, dry_run=dry, logger=logger)),
        "network": ("Network (Route Table, NAT Gateway, EIP, IGW, Subnets, VPC)",
                    lambda dry: destroy_network(clients, state, dry_run=dry, logger=logger)),
        "state": ("Local State File (state/resources.json)",
                  lambda dry: destroy_local_state(state, dry_run=dry, logger=logger)),
    }

    order = ["frontend", "backend", "efs", "database", "storage", "notification", "security", "network", "state"]

    if args.plan:
        logger.log("\n================ [MODE PLAN: AUDIT DAFTAR RESOURCE] ================")
        logger.log("Pemeriksaan ini BERSIFAT AMAN (READ-ONLY), tidak ada resource AWS yang diubah.")
        target_phases = [args.phase] if args.phase else order
        for p in target_phases:
            desc, fn = phases[p]
            fn(dry=True)
        logger.log("\n[OK] Plan selesai: Seluruh resource di atas adalah target cleanup.")
        logger.log("Untuk mengeksekusi penghapusan secara nyata, jalankan: python destroy.py --apply")
        return

    if args.apply:
        logger.log("\n================ [MODE APPLY: EKSEKUSI PENGHAPUSAN] ================")
        if not args.auto_approve:
            print("\nPERINGATAN SANGAT PENTING:")
            print("Perintah ini akan MENGHAPUS SELURUH RESOURCE PROYEK MBG secara permanen.")
            confirm = input("Ketik 'DELETE' untuk mengonfirmasi seluruh proses cleanup: ").strip()
            if confirm != "DELETE":
                print("Operasi dibatalkan. Tidak ada resource yang dihapus.")
                sys.exit(0)

        target_phases = [args.phase] if args.phase else order
        for p in target_phases:
            desc, fn = phases[p]
            confirmed = prompt_confirmation(p, desc, auto_approve=args.auto_approve)
            if confirmed:
                fn(dry=False)

        logger.log("\n=============================================================================")
        logger.log("  [SUKSES] PROSES CLEANUP / DESTROY SELESAI DENGAN AMAN!")
        logger.log(f"  Log aktivitas tersimpan di: {logger.log_file}")
        logger.log("=============================================================================\n")


if __name__ == "__main__":
    main()

